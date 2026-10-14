from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import re
import sqlite3
import time
import asyncio
import urllib.error
import urllib.parse
import urllib.request
import mimetypes
import unicodedata
from contextlib import asynccontextmanager, contextmanager
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from minio import Minio
from minio.error import S3Error

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # PostgreSQL support is optional for SQLite-only installs.
    psycopg = None
    dict_row = None

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("APP_DB", str(ROOT / "teamai.db")))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
STATIC_DIR = ROOT / "web"
logger = logging.getLogger("teamai")
INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())


class SafeSyncFailure(Exception):
    """A GitLab error summary already reduced to safe, non-secret text."""


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql: str, params=()):
        sql = sql.replace("BEGIN IMMEDIATE", "BEGIN")
        return self.connection.execute(sql.replace("?", "%s"), params)

    def lock_record(self, record_id: int):
        return self.connection.execute("SELECT * FROM records WHERE id = %s FOR UPDATE", (record_id,)).fetchone()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_config()
    init_db()
    worker = None
    interval = sync_interval_seconds()
    if interval > 0 and gitlab_configured():
        worker = asyncio.create_task(sync_retry_worker(interval))
    try:
        yield
    finally:
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass


app = FastAPI(title="TeamAI Issue Hub", version="0.1.0", lifespan=lifespan)
app.mount("/web", StaticFiles(directory=STATIC_DIR), name="web")
API_KEY = os.getenv("TEAMAI_API_KEY", "")
ATTACHMENT_MAX_BYTES = int(os.getenv("ATTACHMENT_MAX_BYTES", str(20 * 1024 * 1024)))
ATTACHMENT_BUCKET = os.getenv("ATTACHMENT_BUCKET", "teamai-attachments")
ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".md", ".txt", ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


def object_store() -> Minio:
    endpoint = os.getenv("S3_ENDPOINT", "").strip()
    if not endpoint:
        raise HTTPException(status_code=503, detail="Attachment storage is not configured; set S3_ENDPOINT")
    try:
        return Minio(endpoint, access_key=os.getenv("S3_ACCESS_KEY", ""), secret_key=os.getenv("S3_SECRET_KEY", ""), secure=os.getenv("S3_SECURE", "false").lower() == "true")
    except Exception as exc:
        logger.error("Attachment storage configuration is invalid: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Attachment storage is unavailable") from None

ISSUE_TEMPLATE = """## 问题现象\n\n{content}\n\n## 环境\n\n{environment}\n\n## 复现步骤\n\n{reproduction}\n\n## 预期结果\n\n\n## 实际结果\n\n\n## 已尝试方案\n\n\n## 根因\n\n\n## 解决方案\n\n\n## 验证结果\n\n\n## 相关链接\n\n"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_config() -> None:
    production = os.getenv("APP_ENV", "development").strip().lower() == "production"
    if production and not DATABASE_URL:
        raise RuntimeError("DATABASE_URL must point to PostgreSQL in production")
    if DATABASE_URL:
        try:
            parsed_db = urllib.parse.urlsplit(DATABASE_URL)
            hostname = parsed_db.hostname
            parsed_db.port
        except ValueError as exc:
            raise RuntimeError("DATABASE_URL must be a PostgreSQL URL with a database name") from exc
        if parsed_db.scheme not in {"postgres", "postgresql"} or not hostname or not parsed_db.path.strip("/"):
            raise RuntimeError("DATABASE_URL must be a PostgreSQL URL with a database name")
        if psycopg is None:
            raise RuntimeError("PostgreSQL support requires psycopg; install requirements.txt")
    if ATTACHMENT_MAX_BYTES < 1 or ATTACHMENT_MAX_BYTES > 100 * 1024 * 1024:
        raise RuntimeError("ATTACHMENT_MAX_BYTES must be between 1 and 104857600")
    if len(ATTACHMENT_BUCKET) < 3 or len(ATTACHMENT_BUCKET) > 63 or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", ATTACHMENT_BUCKET):
        raise RuntimeError("ATTACHMENT_BUCKET must be a valid S3 bucket name")
    if API_KEY and any(ord(char) < 33 or ord(char) > 126 for char in API_KEY):
        raise RuntimeError("TEAMAI_API_KEY must contain visible ASCII characters without spaces")
    gitlab_values = (os.getenv("GITLAB_URL", "").strip(), os.getenv("GITLAB_TOKEN", "").strip(), os.getenv("GITLAB_PROJECT_ID", "").strip())
    if any(gitlab_values) and not all(gitlab_values):
        raise RuntimeError("GitLab sync requires GITLAB_URL, GITLAB_TOKEN, and GITLAB_PROJECT_ID together")
    if gitlab_values[0]:
        try:
            parsed = urllib.parse.urlsplit(gitlab_values[0])
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("GITLAB_URL must be a valid http(s) base URL without credentials, query, or fragment") from exc
        if parsed.scheme not in {"http", "https"} or not hostname or port == 0 or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError("GITLAB_URL must be an http(s) base URL without credentials, query, or fragment")
        if any(ord(char) < 33 or ord(char) > 126 for char in gitlab_values[1]):
            raise RuntimeError("GITLAB_TOKEN must contain visible ASCII characters without spaces")
    if os.getenv("APP_ENV", "development").strip().lower() == "production" and API_KEY and len(API_KEY) < 32:
        raise RuntimeError("TEAMAI_API_KEY must be at least 32 characters in production")
    if os.getenv("APP_ENV", "development").strip().lower() == "production":
        for setting in ("TEAMAI_REVIEWER_ID_HEADER", "TEAMAI_REVIEWER_GROUPS_HEADER"):
            header = os.getenv(setting, "").strip()
            if not header or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", header):
                raise RuntimeError(f"{setting} must name a trusted reverse-proxy header in production")
        if not os.getenv("TEAMAI_REVIEWER_REQUIRED_GROUP", "").strip():
            raise RuntimeError("TEAMAI_REVIEWER_REQUIRED_GROUP must be configured in production")
        if os.getenv("TEAMAI_REVIEWER_ID_HEADER", "").strip().lower() == os.getenv("TEAMAI_REVIEWER_GROUPS_HEADER", "").strip().lower():
            raise RuntimeError("TEAMAI_REVIEWER_ID_HEADER and TEAMAI_REVIEWER_GROUPS_HEADER must be different")
    sync_interval_seconds()


def sync_interval_seconds() -> float:
    raw = os.getenv("SYNC_INTERVAL_SECONDS", "60").strip()
    try:
        interval = float(raw)
    except ValueError as exc:
        raise RuntimeError("SYNC_INTERVAL_SECONDS must be a number between 0 and 86400") from exc
    if not 0 <= interval <= 86400:
        raise RuntimeError("SYNC_INTERVAL_SECONDS must be a number between 0 and 86400")
    return interval


@contextmanager
def db():
    if DATABASE_URL:
        conn = PostgresConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row))
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        (conn.connection if DATABASE_URL else conn).commit()
    except Exception:
        (conn.connection if DATABASE_URL else conn).rollback()
        raise
    finally:
        (conn.connection if DATABASE_URL else conn).close()


def init_db() -> None:
    with db() as conn:
        if DATABASE_URL:
            # Serialize schema setup across API replicas. The transaction-scoped
            # lock is released automatically when the db() context commits/rolls back.
            conn.execute("SELECT pg_advisory_xact_lock(731904218)")
            conn.execute("CREATE TABLE IF NOT EXISTS teamai_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied_versions = {row["version"] for row in conn.execute("SELECT version FROM teamai_schema_migrations").fetchall()}
            unknown_versions = applied_versions - {1, 2, 3, 4, 5}
            if unknown_versions:
                raise RuntimeError(f"Database schema is newer than this application (versions: {sorted(unknown_versions)})")

            if 1 in applied_versions:
                columns = {row["column_name"] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'records'").fetchall()}
                required_columns = {
                    "id", "kind", "source", "title", "content", "tags", "status", "content_hash",
                    "sync_error", "sync_key", "sync_lock_until", "created_at", "updated_at", "project",
                    "tech_stack", "knowledge_status", "parent_issue_id", "knowledge_record_id", "reviewed_by",
                }
                missing_columns = required_columns - columns
                if missing_columns:
                    raise RuntimeError(f"Database schema migration record is inconsistent; missing columns: {sorted(missing_columns)}")

            # Version 1 is the current PostgreSQL schema. All schema changes and
            # one-time data repairs live inside this migration transaction.
            if 1 not in applied_versions:
                conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
          id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
          author_id TEXT, session_id TEXT, title TEXT NOT NULL, content TEXT NOT NULL,
          tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'triage',
          external_system TEXT, external_id TEXT, external_url TEXT, sync_error TEXT,
          content_hash TEXT NOT NULL UNIQUE, sync_key TEXT, sync_lock_until DOUBLE PRECISION,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          project TEXT NOT NULL DEFAULT '', tech_stack TEXT NOT NULL DEFAULT '[]',
          knowledge_status TEXT NOT NULL DEFAULT 'not_applicable',
          parent_issue_id INTEGER, knowledge_record_id INTEGER, reviewed_by TEXT
            )
            """)
                columns = {row["column_name"] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'records'").fetchall()}
                conn.execute("CREATE INDEX IF NOT EXISTS idx_records_hash ON records(content_hash)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_records_updated ON records(updated_at DESC)")
                if "sync_error" not in columns:
                    conn.execute("ALTER TABLE records ADD COLUMN sync_error TEXT")
                if "sync_key" not in columns:
                    conn.execute("ALTER TABLE records ADD COLUMN sync_key TEXT")
                if "sync_lock_until" not in columns:
                    conn.execute("ALTER TABLE records ADD COLUMN sync_lock_until DOUBLE PRECISION")
                for name, declaration in (
                    ("project", "TEXT NOT NULL DEFAULT ''"),
                    ("tech_stack", "TEXT NOT NULL DEFAULT '[]'"),
                    ("knowledge_status", "TEXT NOT NULL DEFAULT 'not_applicable'"),
                    ("parent_issue_id", "INTEGER"),
                    ("knowledge_record_id", "INTEGER"),
                    ("reviewed_by", "TEXT"),
                ):
                    if name not in columns:
                        conn.execute(f"ALTER TABLE records ADD COLUMN {name} {declaration}")
                conn.execute("ALTER TABLE records ALTER COLUMN sync_key SET DEFAULT md5(random()::text || clock_timestamp()::text)")
                conn.execute("UPDATE records SET knowledge_status = 'pending_review' WHERE kind = 'issue' AND status = 'resolved' AND knowledge_status = 'not_applicable'")
                conn.execute("UPDATE records SET sync_key = md5(random()::text || clock_timestamp()::text) WHERE sync_key IS NULL")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_records_knowledge ON records(kind, knowledge_status, updated_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_records_sync_pending ON records(sync_error, sync_lock_until, updated_at)")
                duplicates = conn.execute("SELECT 1 FROM records GROUP BY content_hash HAVING COUNT(*) > 1 LIMIT 1").fetchone()
                if not duplicates:
                    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_records_hash_unique ON records(content_hash)")
                conn.execute("INSERT INTO teamai_schema_migrations (version, applied_at) VALUES (1, ?)", (now(),))
            if 2 not in applied_versions:
                conn.execute("CREATE TABLE IF NOT EXISTS record_events (id BIGSERIAL PRIMARY KEY, record_id BIGINT NOT NULL REFERENCES records(id), event_type TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL, created_at TEXT NOT NULL)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_record_events_record ON record_events(record_id, created_at DESC, id DESC)")
                conn.execute("INSERT INTO record_events (record_id, event_type, actor, summary, created_at) SELECT records.id, 'legacy', COALESCE(records.author_id, records.source, ''), '历史记录（活动明细从工作台启用时开始追踪）', records.created_at FROM records")
                conn.execute("INSERT INTO teamai_schema_migrations (version, applied_at) VALUES (2, ?)", (now(),))
            else:
                event_columns = {row["column_name"] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'record_events'").fetchall()}
                if not {"id", "record_id", "event_type", "actor", "summary", "created_at"} <= event_columns:
                    raise RuntimeError("Database schema migration record is inconsistent; record_events table is incomplete")
            if 3 not in applied_versions:
                conn.execute("CREATE TABLE IF NOT EXISTS record_attachments (id BIGSERIAL PRIMARY KEY, record_id BIGINT NOT NULL REFERENCES records(id) ON DELETE CASCADE, object_key TEXT NOT NULL UNIQUE, file_name TEXT NOT NULL, content_type TEXT NOT NULL, size_bytes BIGINT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_record_attachments_record ON record_attachments(record_id, id)")
                conn.execute("INSERT INTO teamai_schema_migrations (version, applied_at) VALUES (3, ?)", (now(),))
            if 4 not in applied_versions:
                conn.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS work_type TEXT NOT NULL DEFAULT ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_records_work_type ON records(work_type, updated_at DESC)")
                conn.execute("INSERT INTO teamai_schema_migrations (version, applied_at) VALUES (4, ?)", (now(),))
            else:
                columns = {row["column_name"] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'records'").fetchall()}
                if "work_type" not in columns:
                    raise RuntimeError("Database schema migration record is inconsistent; records.work_type is missing")
            if 5 not in applied_versions:
                conn.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'shared'")
                conn.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS owner_username TEXT NOT NULL DEFAULT ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_records_visibility_owner ON records(kind, visibility, owner_username, updated_at DESC)")
                conn.execute("INSERT INTO teamai_schema_migrations (version, applied_at) VALUES (5, ?)", (now(),))
            else:
                columns = {row["column_name"] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'records'").fetchall()}
                if not {"visibility", "owner_username"} <= columns:
                    raise RuntimeError("Database schema migration record is inconsistent; visibility columns are missing")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS records (
              id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, source TEXT NOT NULL,
              author_id TEXT, session_id TEXT, title TEXT NOT NULL, content TEXT NOT NULL,
              tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'triage',
              external_system TEXT, external_id TEXT, external_url TEXT, sync_error TEXT,
              content_hash TEXT NOT NULL UNIQUE, sync_key TEXT, sync_lock_until REAL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              project TEXT NOT NULL DEFAULT '', tech_stack TEXT NOT NULL DEFAULT '[]',
              work_type TEXT NOT NULL DEFAULT '',
              visibility TEXT NOT NULL DEFAULT 'shared', owner_username TEXT NOT NULL DEFAULT '',
              knowledge_status TEXT NOT NULL DEFAULT 'not_applicable',
              parent_issue_id INTEGER, knowledge_record_id INTEGER, reviewed_by TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_records_hash ON records(content_hash);
            CREATE INDEX IF NOT EXISTS idx_records_updated ON records(updated_at DESC);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(records)")}
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_hash ON records(content_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_updated ON records(updated_at DESC)")
            if "sync_error" not in columns:
                conn.execute("ALTER TABLE records ADD COLUMN sync_error TEXT")
            if "sync_key" not in columns:
                conn.execute("ALTER TABLE records ADD COLUMN sync_key TEXT")
            if "sync_lock_until" not in columns:
                conn.execute("ALTER TABLE records ADD COLUMN sync_lock_until REAL")
            for name, declaration in (
                ("project", "TEXT NOT NULL DEFAULT ''"),
                ("tech_stack", "TEXT NOT NULL DEFAULT '[]'"),
                ("knowledge_status", "TEXT NOT NULL DEFAULT 'not_applicable'"),
                ("parent_issue_id", "INTEGER"),
                ("knowledge_record_id", "INTEGER"),
                ("reviewed_by", "TEXT"),
                ("work_type", "TEXT NOT NULL DEFAULT ''"),
                ("visibility", "TEXT NOT NULL DEFAULT 'shared'"),
                ("owner_username", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE records ADD COLUMN {name} {declaration}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_knowledge ON records(kind, knowledge_status, updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_sync_pending ON records(sync_error, sync_lock_until, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_work_type ON records(work_type, updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_visibility_owner ON records(kind, visibility, owner_username, updated_at DESC)")
            conn.execute("UPDATE records SET knowledge_status = 'pending_review' WHERE kind = 'issue' AND status = 'resolved' AND knowledge_status = 'not_applicable'")
            conn.execute("UPDATE records SET sync_key = lower(hex(randomblob(32))) WHERE sync_key IS NULL")
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_records_hash_unique ON records(content_hash)")
            except sqlite3.IntegrityError:
                # Existing installations may contain duplicates from before the constraint.
                # Keep startup available; the application still performs a duplicate lookup.
                pass
            conn.execute("CREATE TABLE IF NOT EXISTS record_events (id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL REFERENCES records(id), event_type TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_record_events_record ON record_events(record_id, created_at DESC, id DESC)")
            conn.execute("INSERT INTO record_events (record_id, event_type, actor, summary, created_at) SELECT records.id, 'legacy', COALESCE(records.author_id, records.source, ''), '历史记录（活动明细从工作台启用时开始追踪）', records.created_at FROM records WHERE NOT EXISTS (SELECT 1 FROM record_events WHERE record_events.record_id = records.id)")
            conn.execute("CREATE TABLE IF NOT EXISTS record_attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE, object_key TEXT NOT NULL UNIQUE, file_name TEXT NOT NULL, content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_record_attachments_record ON record_attachments(record_id, id)")


def record_event(conn, record_id: int, event_type: str, summary: str, actor: str = "") -> None:
    conn.execute("INSERT INTO record_events (record_id, event_type, actor, summary, created_at) VALUES (?, ?, ?, ?, ?)", (record_id, event_type, redact(actor)[:120], redact(summary)[:500], now()))


class RecordIn(BaseModel):
    title: str | None = Field(default=None, max_length=180)
    content: str = Field(min_length=1, max_length=100_000)
    source: str = Field(default="web", min_length=1, max_length=60)
    author_id: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=180)
    project: str = Field(default="", max_length=180)
    work_type: Literal["算法调研", "算法预研", "模型部署", "组件开发", "项目支撑", "工作日常"] = "项目支撑"
    visibility: Literal["shared", "private"] = "shared"
    tech_stack: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    environment: str = Field(default="", max_length=20_000)
    reproduction: str = Field(default="", max_length=20_000)

    @field_validator("source")
    @classmethod
    def source_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        tags = list(dict.fromkeys(tag.strip() for tag in values if tag.strip()))
        if any(len(tag) > 80 for tag in tags):
            raise ValueError("each tag must be at most 80 characters")
        return tags

    @field_validator("tech_stack")
    @classmethod
    def normalize_tech_stack(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 80 for value in normalized):
            raise ValueError("each technology must be at most 80 characters")
        return normalized

    @field_validator("project")
    @classmethod
    def normalize_project(cls, value: str) -> str:
        return value.strip()


class KnowledgePublishIn(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1, max_length=100_000)
    reviewer: str = Field(default="", max_length=120)

    @field_validator("title", "content")
    @classmethod
    def required_values_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value

    @field_validator("reviewer")
    @classmethod
    def normalize_reviewer(cls, value: str) -> str:
        return value.strip()


class RecordUpdateIn(BaseModel):
    expected_updated_at: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1, max_length=100_000)
    project: str = Field(default="", max_length=180)
    work_type: Literal["算法调研", "算法预研", "模型部署", "组件开发", "项目支撑", "工作日常"] | None = None
    visibility: Literal["shared", "private"] | None = None
    tech_stack: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title", "content")
    @classmethod
    def required_values_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value

    @field_validator("project")
    @classmethod
    def normalize_project(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags", "tech_stack")
    @classmethod
    def normalize_list_values(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 80 for value in normalized):
            raise ValueError("each value must be at most 80 characters")
        return normalized


class SolutionIn(BaseModel):
    solution: str = Field(min_length=1, max_length=50_000)
    source: str = Field(default="web", min_length=1, max_length=60)
    verification: str | None = Field(default=None, max_length=20_000)

    @field_validator("solution", "source")
    @classmethod
    def values_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class ClassifyIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


def current_username(request: Request, *, required: bool = False) -> str:
    username = unicodedata.normalize("NFKC", urllib.parse.unquote(request.headers.get("x-teamai-user", ""))).strip()
    if len(username) > 80 or any(ord(char) < 32 for char in username):
        raise HTTPException(status_code=400, detail="Invalid username")
    if required and not username:
        raise HTTPException(status_code=400, detail="Enter a username to use TeamAI")
    return username.casefold()


def can_view_record(row, username: str) -> bool:
    return row["kind"] != "note" or row["visibility"] != "private" or bool(username and row["owner_username"] == username)


def require_record_visible(row, username: str) -> None:
    if not row or not can_view_record(row, username):
        raise HTTPException(status_code=404, detail="Record not found")


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    supplied_id = request.headers.get("x-request-id", "")
    request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_id) else uuid.uuid4().hex
    if API_KEY and request.url.path.startswith("/v1/"):
        supplied = request.headers.get("x-teamai-key", "")
        if not hmac.compare_digest(supplied, API_KEY):
            response = JSONResponse({"detail": "Authentication required", "request_id": request_id}, status_code=401)
            response = add_security_headers(response, request_id, request.url.path)
            logger.warning("request_id=%s method=%s path=%s status=401", request_id, request.method, request.url.path)
            return response
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        logger.exception("request_id=%s method=%s path=%s status=500 duration_ms=%s", request_id, request.method, request.url.path, elapsed_ms)
        response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    response = add_security_headers(response, request_id, request.url.path)
    logger.info("request_id=%s method=%s path=%s status=%s duration_ms=%s", request_id, request.method, request.url.path, response.status_code, elapsed_ms)
    return response


def add_security_headers(response: Response, request_id: str, path: str) -> Response:
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if path.startswith("/v1/") or path == "/health":
        response.headers["Cache-Control"] = "no-store"
    if path == "/" or path.startswith("/web/"):
        response.headers["Content-Security-Policy"] = "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; img-src 'self' data:; font-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'"
    return response


def redact(value: str) -> str:
    for pattern, replacement in [
        (r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1[REDACTED]"),
        (r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s]+", r"\1=[REDACTED]"),
        (r"glpat-[A-Za-z0-9_-]+", "glpat-[REDACTED]"),
        (r"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}", "[REDACTED_GITHUB_TOKEN]"),
        (r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]"),
        (r"sk-[A-Za-z0-9_-]{12,}", "sk-[REDACTED]"),
    ]:
        value = re.sub(pattern, replacement, value)
    return value


def infer_kind(content: str) -> str:
    lowered = content.lower()
    if any(word in lowered for word in ("报错", "失败", "无法", "bug", "error", "exception", "问题", "不能")):
        return "issue"
    return "note"


def title_for(record: RecordIn, kind: str) -> str:
    if record.title and record.title.strip():
        return record.title.strip()
    first = re.sub(r"\s+", " ", record.content.strip()).split("\n", 1)[0]
    prefix = {"issue": "问题", "solution": "解决方案", "note": "随笔"}[kind]
    return f"{prefix}：{first[:150]}"


def content_hash(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


def record_digest(title: str, content: str, kind: str, visibility: str, owner_username: str) -> str:
    if kind == "note" and visibility == "private":
        return content_hash(title, f"{content}\n[private-owner:{owner_username.casefold()}]")
    return content_hash(title, content)


def gitlab_configured() -> bool:
    return all(os.getenv(name, "").strip() for name in ("GITLAB_URL", "GITLAB_TOKEN", "GITLAB_PROJECT_ID"))


class _RejectGitLabRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def gitlab_request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{os.environ['GITLAB_URL'].strip().rstrip('/')}/api/v4{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers={"PRIVATE-TOKEN": os.environ["GITLAB_TOKEN"].strip(), "Content-Type": "application/json"})
    opener = urllib.request.build_opener(_RejectGitLabRedirects())
    with opener.open(request, timeout=12) as response:
        return json.loads(response.read().decode())


def gitlab_error_message(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code:
        return f"GitLab returned HTTP {code}"
    if isinstance(exc, ValueError) and str(exc).startswith(("GitLab returned an invalid", "GitLab issue response did not include an ID")):
        return str(exc)[:300]
    return "GitLab request failed; see server logs for the request ID"


def publish_gitlab(title: str, content: str, tags: list[str], sync_key: str, status: str) -> tuple[str | None, str | None, str | None, str | None]:
    if not gitlab_configured():
        return None, None, None, None
    project = urllib.parse.quote(os.environ["GITLAB_PROJECT_ID"].strip(), safe="")
    sync_label = f"teamai-sync-{sync_key}"
    try:
        query = urllib.parse.urlencode({"labels": sync_label, "state": "all", "per_page": 100})
        existing = gitlab_request("GET", f"/projects/{project}/issues?{query}")
        if not isinstance(existing, list):
            raise ValueError("GitLab returned an invalid issue lookup response")
        match = next((issue for issue in existing if isinstance(issue, dict) and sync_label in (issue.get("labels") or [])), None)
        if match is not None:
            result = match
            external_id = result.get("iid") or result.get("id")
            if external_id is None:
                raise ValueError("GitLab issue lookup response did not include an ID")
            error = update_gitlab(str(external_id), content, status, title)
            if error:
                raise SafeSyncFailure(error)
        else:
            result = gitlab_request("POST", f"/projects/{project}/issues", {"title": title, "description": content, "labels": ",".join(dict.fromkeys(tags + ["status::triage", sync_label]))})
        if not isinstance(result, dict):
            raise ValueError("GitLab returned an invalid issue response")
        external_id = result.get("iid") or result.get("id")
        if external_id is None:
            raise ValueError("GitLab issue response did not include an ID")
        return "gitlab", str(external_id), result.get("web_url"), None
    except Exception as exc:
        return None, None, None, gitlab_error_message(exc)


def update_gitlab(external_id: str, content: str, status: str, title: str | None = None) -> str | None:
    project = urllib.parse.quote(os.environ["GITLAB_PROJECT_ID"].strip(), safe="")
    path = f"/projects/{project}/issues/{urllib.parse.quote(external_id, safe='')}"
    status_labels = {"triage", "in-progress", "resolved"}
    payload: dict[str, Any] = {
        "description": content,
        "add_labels": f"status::{status}",
        "remove_labels": ",".join(f"status::{label}" for label in sorted(status_labels - {status})),
    }
    if title is not None:
        payload["title"] = title
    if status == "resolved":
        payload["state_event"] = "close"
    elif status in {"triage", "reopened"}:
        payload["state_event"] = "reopen"
    try:
        gitlab_request("PUT", path, payload)
        return None
    except Exception as exc:
        return gitlab_error_message(exc)


def response(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["tags"] = json.loads(result["tags"] or "[]")
    result["tech_stack"] = json.loads(result.get("tech_stack") or "[]")
    return result


def lock_record(conn, record_id: int):
    if DATABASE_URL:
        return conn.lock_record(record_id)
    return conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()


def safe_sync_error(exc: Exception) -> str:
    """Persist a bounded, credential-safe sync failure summary."""
    if isinstance(exc, SafeSyncFailure):
        return str(exc)[:300]
    return gitlab_error_message(exc)[:300]


def create_record(record: RecordIn, kind: Literal["note", "issue", "solution"], username: str = "") -> dict[str, Any]:
    if kind != "note" and record.visibility != "shared":
        raise HTTPException(status_code=422, detail="Only notes can use personal visibility")
    if kind == "note" and record.visibility == "private" and not username:
        raise HTTPException(status_code=400, detail="Enter a username before creating a personal note")
    safe_content = redact(record.content)
    title = redact(title_for(record, kind))
    if kind == "issue":
        body = ISSUE_TEMPLATE.format(content=safe_content, environment=redact(record.environment), reproduction=redact(record.reproduction))
    elif kind == "solution":
        sections = [safe_content]
        if record.environment.strip():
            sections.append(f"## 环境\n\n{redact(record.environment)}")
        if record.reproduction.strip():
            sections.append(f"## 复现 / 验证步骤\n\n{redact(record.reproduction)}")
        body = "\n\n".join(sections)
    else:
        body = safe_content
    safe_source = redact(record.source)
    safe_tags = [redact(tag) for tag in record.tags]
    tags = list(dict.fromkeys(safe_tags + [f"source::{safe_source}", f"type::{kind}"]))
    owner_username = username if kind == "note" else ""
    visibility = record.visibility if kind == "note" else "shared"
    digest = record_digest(title, body, kind, visibility, owner_username)
    sync_error = "GitLab synchronization pending" if gitlab_configured() and visibility == "shared" else None
    stamp = now()
    try:
        with db() as conn:
            # Serialize the lookup and insert even on legacy databases where
            # duplicate historical hashes prevented the unique index migration.
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute("SELECT * FROM records WHERE content_hash = ? ORDER BY id LIMIT 1", (digest,)).fetchone()
            if duplicate:
                return response(duplicate) | {"duplicate": True}
            insert_sql = "INSERT INTO records (kind,source,author_id,session_id,title,content,tags,status,external_system,external_id,external_url,sync_error,content_hash,sync_key,created_at,updated_at,project,tech_stack,work_type,visibility,owner_username) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            params = (kind, safe_source, redact(record.author_id) if record.author_id else None, redact(record.session_id) if record.session_id else None, title, body, json.dumps(tags, ensure_ascii=False), "triage", None, None, None, sync_error, digest, digest, stamp, stamp, redact(record.project), json.dumps([redact(value) for value in record.tech_stack], ensure_ascii=False), record.work_type, visibility, owner_username)
            if DATABASE_URL:
                record_id = conn.execute(insert_sql + " RETURNING id", params).fetchone()["id"]
            else:
                cursor = conn.execute(insert_sql, params)
                record_id = cursor.lastrowid
            record_event(conn, record_id, "created", "创建" + {"note": "随记", "issue": "问题", "solution": "方案"}[kind])
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    except INTEGRITY_ERRORS as exc:
        with db() as conn:
            row = conn.execute("SELECT * FROM records WHERE content_hash = ?", (digest,)).fetchone()
        if row:
            return response(row) | {"duplicate": True}
        raise HTTPException(status_code=409, detail="Record conflicts with an existing database constraint") from exc
    if gitlab_configured() and visibility == "shared":
        sync_record(row["id"])
        with db() as conn:
            row = conn.execute("SELECT * FROM records WHERE id = ?", (row["id"],)).fetchone()
    return response(row)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    with db() as conn:
        pending = conn.execute("SELECT COUNT(*) AS count FROM records WHERE sync_error IS NOT NULL AND (kind != 'note' OR visibility != 'private')").fetchone()["count"]
    return {"ok": True, "gitlab_configured": gitlab_configured(), "pending_sync": pending, "reviewer_auth_required": os.getenv("APP_ENV", "development").strip().lower() == "production"}


def sync_record(record_id: int) -> str:
    """Claim and synchronize one pending row; the lease prevents duplicate workers."""
    started = time.time()
    with db() as conn:
        claimed = conn.execute(
            "UPDATE records SET sync_lock_until = ? WHERE id = ? AND sync_error IS NOT NULL AND (kind != 'note' OR visibility != 'private') AND (sync_lock_until IS NULL OR sync_lock_until < ?)",
            (started + 60, record_id, started),
        )
        if claimed.rowcount != 1:
            return "skipped"
        row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()

    try:
        external_system, external_id, external_url = row["external_system"], row["external_id"], row["external_url"]
        for _ in range(3):
            if external_id:
                error = update_gitlab(external_id, row["content"], row["status"], row["title"])
            else:
                external_system, external_id, external_url, error = publish_gitlab(
                    row["title"], row["content"], json.loads(row["tags"] or "[]"), row["sync_key"], row["status"]
                )
            if error:
                raise SafeSyncFailure(error)
            with db() as conn:
                latest = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
                if latest["content_hash"] != row["content_hash"] or latest["status"] != row["status"]:
                    row = dict(latest)
                    row["external_id"] = external_id
                    continue
                finalized = conn.execute(
                    "UPDATE records SET external_system = ?, external_id = ?, external_url = ?, sync_error = NULL, sync_lock_until = NULL, updated_at = ? WHERE id = ? AND content_hash = ? AND status = ?",
                    (external_system, external_id, external_url, now(), record_id, row["content_hash"], row["status"]),
                )
                if finalized.rowcount != 1:
                    row = dict(conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone())
                    row["external_id"] = external_id
                    continue
                record_event(conn, record_id, "sync_succeeded", "GitLab Issue 已同步")
                return "synced"
        raise RuntimeError("Record changed repeatedly during GitLab synchronization; retry later")
    except Exception as exc:
        with db() as conn:
            conn.execute(
                "UPDATE records SET sync_error = ?, sync_lock_until = NULL, updated_at = ? WHERE id = ?",
                (safe_sync_error(exc), now(), record_id),
            )
            record_event(conn, record_id, "sync_failed", "GitLab 同步失败：" + safe_sync_error(exc))
        return "failed"


@app.post("/v1/notes")
def create_note(record: RecordIn, request: Request) -> dict[str, Any]:
    return create_record(record, "note", current_username(request))


@app.post("/v1/issues")
def create_issue(record: RecordIn) -> dict[str, Any]:
    return create_record(record, "issue")


@app.post("/v1/solutions")
def create_solution(record: RecordIn) -> dict[str, Any]:
    return create_record(record, "solution")


@app.post("/v1/sync")
def sync_pending(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    if not gitlab_configured():
        return {"synced": 0, "failed": 0, "pending": 0, "message": "GitLab is not configured"}
    with db() as conn:
        rows = conn.execute("SELECT id FROM records WHERE sync_error IS NOT NULL AND (kind != 'note' OR visibility != 'private') AND (sync_lock_until IS NULL OR sync_lock_until < ?) ORDER BY updated_at ASC LIMIT ?", (time.time(), limit)).fetchall()
    synced = 0
    failed = 0
    skipped = 0
    for row in rows:
        result = sync_record(row["id"])
        if result == "synced":
            synced += 1
        elif result == "failed":
            failed += 1
        else:
            skipped += 1
    with db() as conn:
        pending = conn.execute("SELECT COUNT(*) AS count FROM records WHERE sync_error IS NOT NULL AND (kind != 'note' OR visibility != 'private')").fetchone()["count"]
    return {"synced": synced, "failed": failed, "skipped": skipped, "pending": pending}


async def sync_retry_worker(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            result = await asyncio.to_thread(sync_pending, limit=10)
            logger.info(
                "automatic GitLab sync finished synced=%s failed=%s skipped=%s pending=%s",
                result["synced"], result["failed"], result["skipped"], result["pending"],
            )
        except Exception:
            logger.exception("automatic GitLab sync retry failed")


@app.post("/v1/classify")
def classify(payload: ClassifyIn) -> dict[str, str]:
    kind = infer_kind(payload.content)
    return {"kind": kind, "title": title_for(RecordIn(content=payload.content), kind)}


@app.get("/v1/overview")
def overview(request: Request) -> dict[str, Any]:
    # Counts are scoped the same way as search so personal-note activity is not
    # exposed to other username contexts.
    username = current_username(request)
    with db() as conn:
        totals = conn.execute("SELECT COUNT(*) AS total FROM records WHERE kind != 'note' OR visibility != 'private' OR owner_username = ?", (username,)).fetchone()["total"]
        by_kind = {row["kind"]: row["count"] for row in conn.execute("SELECT kind, COUNT(*) AS count FROM records WHERE kind != 'note' OR visibility != 'private' OR owner_username = ? GROUP BY kind", (username,))}
        by_status = {row["status"]: row["count"] for row in conn.execute("SELECT status, COUNT(*) AS count FROM records WHERE kind = 'issue' GROUP BY status")}
        recent = conn.execute("SELECT COUNT(*) AS count FROM records WHERE created_at >= ? AND (kind != 'note' OR visibility != 'private' OR owner_username = ?)", ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(), username)).fetchone()["count"]
        pending_knowledge = conn.execute("SELECT COUNT(*) AS count FROM records WHERE kind = 'issue' AND status = 'resolved' AND knowledge_status IN ('pending_review','needs_review')").fetchone()["count"]
    return {"total": totals, "recent": recent, "by_kind": by_kind, "by_status": by_status, "pending_knowledge": pending_knowledge}


@app.get("/v1/search")
def search(request: Request, q: str = Query(default="", max_length=200), kind: str | None = None, status: str | None = None, source: str | None = None, knowledge_status: Literal["pending_review", "needs_review", "review"] | None = None, visibility: Literal["shared", "private"] | None = None, limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> list[dict[str, Any]]:
    term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    query = f"%{term}%"
    username = current_username(request)
    sql = "SELECT * FROM records WHERE (kind != 'note' OR visibility != 'private' OR owner_username = ?) AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\' OR project LIKE ? ESCAPE '\\' OR tech_stack LIKE ? ESCAPE '\\' OR work_type LIKE ? ESCAPE '\\') AND (kind != 'knowledge' OR knowledge_status = 'published')"
    params: list[Any] = [username, query, query, query, query, query, query]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if visibility:
        sql += " AND kind = 'note' AND visibility = ?"
        params.append(visibility)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if knowledge_status == "review":
        sql += " AND kind = 'issue' AND knowledge_status IN ('pending_review','needs_review')"
    elif knowledge_status:
        sql += " AND knowledge_status = ?"
        params.append(knowledge_status)
    if kind == "knowledge":
        sql += " AND knowledge_status = 'published'"
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with db() as conn:
        return [response(row) for row in conn.execute(sql, params).fetchall()]


@app.get("/v1/issues/{record_id}")
def get_issue(record_id: int, request: Request) -> dict[str, Any]:
    username = current_username(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        events = conn.execute("SELECT id, event_type, actor, summary, created_at FROM record_events WHERE record_id = ? ORDER BY created_at DESC, id DESC LIMIT 100", (record_id,)).fetchall() if row else []
        attachments = conn.execute("SELECT id, file_name, content_type, size_bytes, sha256, created_at FROM record_attachments WHERE record_id = ? ORDER BY id", (record_id,)).fetchall() if row else []
    require_record_visible(row, username)
    return response(row) | {"events": [dict(event) for event in events], "attachments": [dict(item) for item in attachments]}


@app.post("/v1/records/{record_id}/attachments")
async def upload_attachment(record_id: int, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    username = current_username(request)
    filename = unicodedata.normalize("NFC", Path(file.filename or "attachment").name).strip()
    if not filename or len(filename) > 240 or filename in {".", ".."}:
        raise HTTPException(status_code=422, detail="Invalid attachment filename")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="File type is not allowed")
    payload = await file.read(ATTACHMENT_MAX_BYTES + 1)
    await file.close()
    if not payload:
        raise HTTPException(status_code=422, detail="Attachment cannot be empty")
    if len(payload) > ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"Attachment exceeds {ATTACHMENT_MAX_BYTES} bytes")
    with db() as conn:
        record = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    require_record_visible(record, username)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if extension in {".html", ".htm"}:
        content_type = "text/html"
    digest = hashlib.sha256(payload).hexdigest()
    object_key = f"records/{record_id}/{uuid.uuid4().hex}{extension}"
    store = object_store()
    try:
        store.put_object(ATTACHMENT_BUCKET, object_key, io.BytesIO(payload), len(payload), content_type="application/octet-stream", metadata={"sha256": digest})
    except Exception as exc:
        logger.error("Attachment upload failed record_id=%s error=%s", record_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Attachment storage is unavailable") from None
    try:
        with db() as conn:
            current_record = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
            require_record_visible(current_record, username)
            if conn.execute("SELECT COUNT(*) AS count FROM record_attachments WHERE record_id = ?", (record_id,)).fetchone()["count"] >= 30:
                raise HTTPException(status_code=409, detail="A record can have at most 30 attachments")
            cursor = conn.execute("INSERT INTO record_attachments (record_id, object_key, file_name, content_type, size_bytes, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (record_id, object_key, filename, content_type, len(payload), digest, now()))
            attachment_id = cursor.fetchone()["id"] if DATABASE_URL else cursor.lastrowid
            record_event(conn, record_id, "attachment_added", f"添加附件：{filename}")
            row = conn.execute("SELECT id, file_name, content_type, size_bytes, sha256, created_at FROM record_attachments WHERE id = ?", (attachment_id,)).fetchone()
    except Exception:
        try:
            store.remove_object(ATTACHMENT_BUCKET, object_key)
        except Exception:
            logger.error("Orphaned attachment cleanup failed record_id=%s", record_id)
        raise
    return dict(row)


@app.get("/v1/records/{record_id}/attachments/{attachment_id}")
def download_attachment(record_id: int, attachment_id: int, request: Request) -> Response:
    username = current_username(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM record_attachments WHERE id = ? AND record_id = ?", (attachment_id, record_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")
    with db() as conn:
        record = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    require_record_visible(record, username)
    store = object_store()
    try:
        obj = store.get_object(ATTACHMENT_BUCKET, row["object_key"])
        try:
            payload = obj.read(ATTACHMENT_MAX_BYTES + 1)
        finally:
            if hasattr(obj, "close"):
                obj.close()
            if hasattr(obj, "release_conn"):
                obj.release_conn()
    except Exception as exc:
        logger.error("Attachment download failed record_id=%s attachment_id=%s error=%s", record_id, attachment_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Attachment storage is unavailable") from None
    if len(payload) > ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=502, detail="Stored attachment exceeds the configured size limit")
    safe_name = row["file_name"].replace('"', "")
    return Response(payload, media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(safe_name)}", "Content-Length": str(len(payload)), "X-Content-Type-Options": "nosniff"})


@app.put("/v1/records/{record_id}")
def edit_record(record_id: int, payload: RecordUpdateIn, request: Request) -> dict[str, Any]:
    username = current_username(request)
    title = redact(payload.title)
    content = redact(payload.content)
    project = redact(payload.project)
    tech_stack = [redact(value) for value in payload.tech_stack]
    digest = content_hash(title, content)
    stamp = now()
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = lock_record(conn, record_id)
            require_record_visible(row, username)
            if row["updated_at"] != payload.expected_updated_at:
                raise HTTPException(status_code=409, detail="Record changed since it was opened. Refresh and try again.")
            if row["kind"] == "knowledge":
                raise HTTPException(status_code=409, detail="Edit knowledge through the source issue review flow")
            if row["kind"] != "note" and payload.visibility is not None:
                raise HTTPException(status_code=422, detail="Only notes can change visibility")
            if row["kind"] == "issue" and row["status"] == "resolved":
                raise HTTPException(status_code=409, detail="Reopen the issue before editing verified content")
            visibility = payload.visibility or row["visibility"]
            owner_username = row["owner_username"]
            if row["kind"] == "note" and visibility == "private":
                if not owner_username or owner_username != username:
                    raise HTTPException(status_code=403, detail="Only the note owner can make it personal")
                if row["visibility"] != "private" and (row["external_system"] or row["external_id"] or row["sync_error"]):
                    raise HTTPException(status_code=409, detail="A note already synchronized or queued for GitLab cannot be made personal")
            digest = record_digest(title, content, row["kind"], visibility, owner_username)
            sync_error = "GitLab synchronization pending" if gitlab_configured() and visibility == "shared" else None
            duplicate = conn.execute("SELECT id FROM records WHERE content_hash = ? AND id != ?", (digest, record_id)).fetchone()
            if duplicate:
                raise HTTPException(status_code=409, detail=f"内容与记录 #{duplicate['id']} 重复，未更新")
            existing_tags = json.loads(row["tags"] or "[]")
            system_tags = [tag for tag in existing_tags if tag.startswith(("source::", "type::"))]
            tags = list(dict.fromkeys(system_tags + [redact(tag) for tag in payload.tags]))
            conn.execute(
                "UPDATE records SET title = ?, content = ?, tags = ?, content_hash = ?, project = ?, tech_stack = ?, work_type = ?, visibility = ?, sync_error = ?, sync_lock_until = NULL, updated_at = ? WHERE id = ?",
                (title, content, json.dumps(tags, ensure_ascii=False), digest, project, json.dumps(tech_stack, ensure_ascii=False), payload.work_type or row["work_type"], visibility, sync_error, stamp, record_id),
            )
            record_event(conn, record_id, "edited", "编辑了标题、正文或分类信息")
            updated = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    except INTEGRITY_ERRORS as exc:
        if "content_hash" not in str(exc):
            raise HTTPException(status_code=409, detail="Record conflicts with an existing database constraint") from exc
        raise HTTPException(status_code=409, detail="内容与现有记录重复，未更新") from exc
    if gitlab_configured() and updated["visibility"] == "shared":
        sync_record(record_id)
        with db() as conn:
            updated = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    return response(updated)


def update_issue(record_id: int, payload: SolutionIn, status: str) -> dict[str, Any]:
    if status == "resolved" and not (payload.verification or "").strip():
        raise HTTPException(status_code=422, detail="Verification evidence is required to resolve an issue")
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = lock_record(conn, record_id)
            if not row:
                raise HTTPException(status_code=404, detail="Record not found")
            if row["kind"] != "issue":
                raise HTTPException(status_code=409, detail="Lifecycle actions are available only for issue records")
            extra = f"\n\n## {'解决方案' if status == 'resolved' else '追加解决方案'}\n\n{redact(payload.solution)}"
            if status == "resolved":
                extra += f"\n\n## 验证结果\n\n{redact(payload.verification or '')}\n\n已由 {redact(payload.source)} 标记为已解决。"
            stamp = now()
            new_content = row["content"] + extra
            new_hash = content_hash(row["title"], new_content)
            duplicate = conn.execute("SELECT id FROM records WHERE content_hash = ? AND id != ?", (new_hash, record_id)).fetchone()
            if duplicate:
                raise HTTPException(status_code=409, detail=f"内容与记录 #{duplicate['id']} 重复，未更新")
            sync_error = "GitLab synchronization pending" if gitlab_configured() else None
            knowledge_status = row["knowledge_status"]
            if status == "resolved" and row["kind"] == "issue" and knowledge_status == "not_applicable":
                knowledge_status = "pending_review"
            if status == "triage" and row["kind"] == "issue" and knowledge_status == "pending_review":
                knowledge_status = "not_applicable"
            conn.execute("UPDATE records SET content = ?, updated_at = ?, status = ?, content_hash = ?, sync_error = ?, knowledge_status = ? WHERE id = ?", (new_content, stamp, status, new_hash, sync_error, knowledge_status, record_id))
            event_type = {"in-progress": "solution_added", "resolved": "resolved", "triage": "reopened"}[status]
            summary = {"in-progress": "追加解决方案", "resolved": "问题已解决并进入知识审核", "triage": "问题重新打开"}[status]
            record_event(conn, record_id, event_type, summary)
            if status == "triage" and row["kind"] == "issue" and row["knowledge_status"] == "published" and row["knowledge_record_id"]:
                knowledge_status = "needs_review"
                conn.execute("UPDATE records SET knowledge_status = ?, updated_at = ? WHERE id = ?", (knowledge_status, stamp, record_id))
                conn.execute("UPDATE records SET knowledge_status = ?, updated_at = ? WHERE id = ?", (knowledge_status, stamp, row["knowledge_record_id"]))
            updated = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    except INTEGRITY_ERRORS as exc:
        if "content_hash" not in str(exc):
            raise HTTPException(status_code=409, detail="Record conflicts with an existing database constraint") from exc
        raise HTTPException(status_code=409, detail="内容与现有记录重复，未更新") from exc
    if gitlab_configured():
        sync_record(record_id)
        with db() as conn:
            updated = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    return response(updated)


@app.post("/v1/issues/{record_id}/append-solution")
def append_solution(record_id: int, payload: SolutionIn) -> dict[str, Any]:
    return update_issue(record_id, payload, "in-progress")


@app.post("/v1/issues/{record_id}/resolve")
def resolve_issue(record_id: int, payload: SolutionIn) -> dict[str, Any]:
    return update_issue(record_id, payload, "resolved")


@app.post("/v1/issues/{record_id}/reopen")
def reopen_issue(record_id: int, payload: SolutionIn) -> dict[str, Any]:
    return update_issue(record_id, payload, "triage")


@app.get("/v1/knowledge/candidates")
def knowledge_candidates(limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM records WHERE kind = 'issue' AND status = 'resolved' AND knowledge_status IN ('pending_review','needs_review') ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [response(row) for row in rows]


@app.post("/v1/issues/{record_id}/publish-knowledge")
def publish_knowledge(record_id: int, payload: KnowledgePublishIn, request: Request) -> dict[str, Any]:
    title = redact(payload.title)
    content = redact(payload.content)
    if os.getenv("APP_ENV", "development").strip().lower() == "production":
        identity_header = os.environ["TEAMAI_REVIEWER_ID_HEADER"].strip()
        groups_header = os.environ["TEAMAI_REVIEWER_GROUPS_HEADER"].strip()
        reviewer = request.headers.get(identity_header, "").strip()
        groups = set(re.split(r"[,;\s]+", request.headers.get(groups_header, "").strip()))
        required_group = os.environ["TEAMAI_REVIEWER_REQUIRED_GROUP"].strip()
        if not reviewer:
            raise HTTPException(status_code=403, detail="Authenticated reviewer identity required")
        if len(reviewer) > 120 or any(ord(char) < 32 for char in reviewer):
            raise HTTPException(status_code=403, detail="Invalid authenticated reviewer identity")
        if required_group not in groups:
            raise HTTPException(status_code=403, detail="Reviewer group membership required")
        reviewer = redact(reviewer)
    else:
        reviewer = redact(payload.reviewer)
        if not reviewer:
            raise HTTPException(status_code=422, detail="Reviewer label is required in development mode")
    stamp = now()
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            issue = lock_record(conn, record_id)
            if issue and issue["kind"] != "issue":
                issue = None
            if not issue:
                raise HTTPException(status_code=404, detail="Issue not found")
            if issue["status"] != "resolved" or issue["knowledge_status"] not in {"pending_review", "needs_review"}:
                raise HTTPException(status_code=409, detail="Only resolved issues awaiting review can be published")
            tags = json.loads(issue["tags"] or "[]")
            tags = list(dict.fromkeys([tag for tag in tags if not tag.startswith("type::")] + ["type::knowledge", "knowledge::verified"]))
            digest = content_hash(title, content)
            current = conn.execute("SELECT * FROM records WHERE id = ? AND kind = 'knowledge' AND parent_issue_id = ?", (issue["knowledge_record_id"], record_id)).fetchone() if issue["knowledge_record_id"] else None
            duplicate = conn.execute("SELECT id FROM records WHERE kind = 'knowledge' AND content_hash = ? AND id != ?", (digest, current["id"] if current else -1)).fetchone()
            if duplicate:
                raise HTTPException(status_code=409, detail=f"Knowledge entry already exists as record #{duplicate['id']}")
            if current:
                knowledge_id = current["id"]
                conn.execute("UPDATE records SET title = ?, content = ?, tags = ?, status = 'resolved', content_hash = ?, sync_key = ?, updated_at = ?, project = ?, tech_stack = ?, work_type = ?, knowledge_status = 'published', reviewed_by = ? WHERE id = ?", (title, content, json.dumps(tags, ensure_ascii=False), digest, digest, stamp, issue["project"], issue["tech_stack"], issue["work_type"], reviewer, knowledge_id))
            else:
                insert_sql = "INSERT INTO records (kind,source,title,content,tags,status,content_hash,sync_key,created_at,updated_at,project,tech_stack,work_type,knowledge_status,parent_issue_id,reviewed_by) VALUES ('knowledge','team-review',?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                params = (title, content, json.dumps(tags, ensure_ascii=False), "resolved", digest, digest, stamp, stamp, issue["project"], issue["tech_stack"], issue["work_type"], "published", record_id, reviewer)
                if DATABASE_URL:
                    knowledge_id = conn.execute(insert_sql + " RETURNING id", params).fetchone()["id"]
                else:
                    knowledge_id = conn.execute(insert_sql, params).lastrowid
            conn.execute("UPDATE records SET knowledge_status = 'published', knowledge_record_id = ?, updated_at = ? WHERE id = ?", (knowledge_id, stamp, record_id))
            record_event(conn, record_id, "knowledge_published", f"审核通过并发布团队知识 #{knowledge_id}", reviewer)
            record_event(conn, knowledge_id, "knowledge_published", f"由问题 #{record_id} 审核发布", reviewer)
            row = conn.execute("SELECT * FROM records WHERE id = ?", (knowledge_id,)).fetchone()
    except INTEGRITY_ERRORS as exc:
        if "content_hash" not in str(exc):
            raise HTTPException(status_code=409, detail="Knowledge entry conflicts with an existing database constraint") from exc
        raise HTTPException(status_code=409, detail="Equivalent record already exists") from exc
    return response(row)
