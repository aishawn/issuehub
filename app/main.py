from __future__ import annotations

import hashlib
import hmac
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
from contextlib import asynccontextmanager, contextmanager
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("APP_DB", str(ROOT / "teamai.db")))
STATIC_DIR = ROOT / "web"
logger = logging.getLogger("teamai")
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

ISSUE_TEMPLATE = """## 问题现象\n\n{content}\n\n## 环境\n\n{environment}\n\n## 复现步骤\n\n{reproduction}\n\n## 预期结果\n\n\n## 实际结果\n\n\n## 已尝试方案\n\n\n## 根因\n\n\n## 解决方案\n\n\n## 验证结果\n\n\n## 相关链接\n\n"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_config() -> None:
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
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS records (
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, source TEXT NOT NULL,
          author_id TEXT, session_id TEXT, title TEXT NOT NULL, content TEXT NOT NULL,
          tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'triage',
          external_system TEXT, external_id TEXT, external_url TEXT, sync_error TEXT,
          content_hash TEXT NOT NULL UNIQUE, sync_key TEXT, sync_lock_until REAL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_records_hash ON records(content_hash);
        CREATE INDEX IF NOT EXISTS idx_records_updated ON records(updated_at DESC);
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(records)")}
        if "sync_error" not in columns:
            conn.execute("ALTER TABLE records ADD COLUMN sync_error TEXT")
        if "sync_key" not in columns:
            conn.execute("ALTER TABLE records ADD COLUMN sync_key TEXT")
        if "sync_lock_until" not in columns:
            conn.execute("ALTER TABLE records ADD COLUMN sync_lock_until REAL")
        conn.execute("UPDATE records SET sync_key = lower(hex(randomblob(32))) WHERE sync_key IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_sync_pending ON records(sync_error, sync_lock_until, updated_at)")
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_records_hash_unique ON records(content_hash)")
        except sqlite3.IntegrityError:
            # Existing installations may contain duplicates from before the constraint.
            # Keep startup available; the application still performs a duplicate lookup.
            pass


class RecordIn(BaseModel):
    title: str | None = Field(default=None, max_length=180)
    content: str = Field(min_length=1, max_length=100_000)
    source: str = Field(default="web", min_length=1, max_length=60)
    author_id: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=180)
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


class SolutionIn(BaseModel):
    solution: str = Field(min_length=1, max_length=50_000)
    source: str = Field(default="web", min_length=1, max_length=60)

    @field_validator("solution", "source")
    @classmethod
    def values_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class ClassifyIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


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
    if any(word in lowered for word in ("解决", "修复", "方案", "验证通过", "fixed", "workaround")):
        return "solution"
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
    reason = getattr(exc, "reason", None)
    if reason:
        message = re.sub(r"https?://\S+", "[GitLab endpoint]", str(reason))
        return f"GitLab request failed: {message[:300]}"
    if isinstance(exc, ValueError):
        message = re.sub(r"https?://\S+", "[GitLab endpoint]", str(exc))
        return f"GitLab response error: {message[:300]}"
    return f"GitLab request failed ({type(exc).__name__})"


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
            error = update_gitlab(str(external_id), content, status)
            if error:
                raise RuntimeError(error)
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


def update_gitlab(external_id: str, content: str, status: str) -> str | None:
    project = urllib.parse.quote(os.environ["GITLAB_PROJECT_ID"].strip(), safe="")
    path = f"/projects/{project}/issues/{urllib.parse.quote(external_id, safe='')}"
    status_labels = {"triage", "in-progress", "resolved"}
    payload: dict[str, Any] = {
        "description": content,
        "add_labels": f"status::{status}",
        "remove_labels": ",".join(f"status::{label}" for label in sorted(status_labels - {status})),
    }
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
    return result


def create_record(record: RecordIn, kind: Literal["note", "issue", "solution"]) -> dict[str, Any]:
    safe_content = redact(record.content)
    title = redact(title_for(record, kind))
    body = safe_content if kind == "note" else ISSUE_TEMPLATE.format(content=safe_content, environment=redact(record.environment), reproduction=redact(record.reproduction))
    safe_source = redact(record.source)
    safe_tags = [redact(tag) for tag in record.tags]
    tags = list(dict.fromkeys(safe_tags + [f"source::{safe_source}", f"type::{kind}"]))
    digest = content_hash(title, body)
    sync_error = "GitLab synchronization pending" if gitlab_configured() else None
    stamp = now()
    try:
        with db() as conn:
            # Serialize the lookup and insert even on legacy databases where
            # duplicate historical hashes prevented the unique index migration.
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute("SELECT * FROM records WHERE content_hash = ? ORDER BY id LIMIT 1", (digest,)).fetchone()
            if duplicate:
                return response(duplicate) | {"duplicate": True}
            cursor = conn.execute("INSERT INTO records (kind,source,author_id,session_id,title,content,tags,status,external_system,external_id,external_url,sync_error,content_hash,sync_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (kind, safe_source, redact(record.author_id) if record.author_id else None, redact(record.session_id) if record.session_id else None, title, body, json.dumps(tags, ensure_ascii=False), "triage", None, None, None, sync_error, digest, digest, stamp, stamp))
            row = conn.execute("SELECT * FROM records WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        with db() as conn:
            row = conn.execute("SELECT * FROM records WHERE content_hash = ?", (digest,)).fetchone()
        return response(row) | {"duplicate": True}
    if gitlab_configured():
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
        pending = conn.execute("SELECT COUNT(*) AS count FROM records WHERE sync_error IS NOT NULL").fetchone()["count"]
    return {"ok": True, "gitlab_configured": gitlab_configured(), "pending_sync": pending}


def sync_record(record_id: int) -> str:
    """Claim and synchronize one pending row; the lease prevents duplicate workers."""
    started = time.time()
    with db() as conn:
        claimed = conn.execute(
            "UPDATE records SET sync_lock_until = ? WHERE id = ? AND sync_error IS NOT NULL AND (sync_lock_until IS NULL OR sync_lock_until < ?)",
            (started + 60, record_id, started),
        )
        if claimed.rowcount != 1:
            return "skipped"
        row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()

    try:
        external_system, external_id, external_url = row["external_system"], row["external_id"], row["external_url"]
        for _ in range(3):
            if external_id:
                error = update_gitlab(external_id, row["content"], row["status"])
            else:
                external_system, external_id, external_url, error = publish_gitlab(
                    row["title"], row["content"], json.loads(row["tags"] or "[]"), row["sync_key"], row["status"]
                )
            if error:
                raise RuntimeError(error)
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
                return "synced"
        raise RuntimeError("Record changed repeatedly during GitLab synchronization; retry later")
    except Exception as exc:
        with db() as conn:
            conn.execute(
                "UPDATE records SET sync_error = ?, sync_lock_until = NULL, updated_at = ? WHERE id = ?",
                (str(exc)[:500], now(), record_id),
            )
        return "failed"


@app.post("/v1/notes")
def create_note(record: RecordIn) -> dict[str, Any]:
    return create_record(record, "note")


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
        rows = conn.execute("SELECT id FROM records WHERE sync_error IS NOT NULL AND (sync_lock_until IS NULL OR sync_lock_until < ?) ORDER BY updated_at ASC LIMIT ?", (time.time(), limit)).fetchall()
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
        pending = conn.execute("SELECT COUNT(*) AS count FROM records WHERE sync_error IS NOT NULL").fetchone()["count"]
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
def overview() -> dict[str, Any]:
    with db() as conn:
        totals = conn.execute("SELECT COUNT(*) AS total FROM records").fetchone()["total"]
        by_kind = {row["kind"]: row["count"] for row in conn.execute("SELECT kind, COUNT(*) AS count FROM records GROUP BY kind")}
        by_status = {row["status"]: row["count"] for row in conn.execute("SELECT status, COUNT(*) AS count FROM records GROUP BY status")}
        recent = conn.execute("SELECT COUNT(*) AS count FROM records WHERE created_at >= datetime('now', '-7 days')").fetchone()["count"]
    return {"total": totals, "recent": recent, "by_kind": by_kind, "by_status": by_status}


@app.get("/v1/search")
def search(q: str = Query(default="", max_length=200), kind: str | None = None, status: str | None = None, source: str | None = None, limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> list[dict[str, Any]]:
    term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    query = f"%{term}%"
    sql = "SELECT * FROM records WHERE (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')"
    params: list[Any] = [query, query, query]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with db() as conn:
        return [response(row) for row in conn.execute(sql, params).fetchall()]


@app.get("/v1/issues/{record_id}")
def get_issue(record_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    return response(row)


def update_issue(record_id: int, payload: SolutionIn, status: str) -> dict[str, Any]:
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Record not found")
            extra = f"\n\n## {'解决方案' if status == 'resolved' else '追加解决方案'}\n\n{redact(payload.solution)}"
            if status == "resolved":
                extra += f"\n\n## 验证结果\n\n已由 {redact(payload.source)} 标记为已解决。"
            stamp = now()
            new_content = row["content"] + extra
            new_hash = content_hash(row["title"], new_content)
            duplicate = conn.execute("SELECT id FROM records WHERE content_hash = ? AND id != ?", (new_hash, record_id)).fetchone()
            if duplicate:
                raise HTTPException(status_code=409, detail=f"内容与记录 #{duplicate['id']} 重复，未更新")
            sync_error = "GitLab synchronization pending" if gitlab_configured() else None
            conn.execute("UPDATE records SET content = ?, updated_at = ?, status = ?, content_hash = ?, sync_error = ? WHERE id = ?", (new_content, stamp, status, new_hash, sync_error, record_id))
            updated = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        if "content_hash" not in str(exc):
            raise
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
