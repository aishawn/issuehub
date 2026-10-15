from __future__ import annotations

import importlib
import os
import sqlite3
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg import sql
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def postgres_api(tmp_path, monkeypatch):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run the isolated PostgreSQL integration test")

    schema = f"teamai_test_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    parts = urlsplit(database_url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "options"]
    query.append(("options", f"-csearch_path={schema}"))
    isolated_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    monkeypatch.setenv("DATABASE_URL", isolated_url)
    monkeypatch.setenv("APP_DB", str(tmp_path / "unused.db"))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("TEAMAI_ADMIN_USERS", "test-user")
    monkeypatch.delenv("TEAMAI_API_KEY", raising=False)
    for name in ("GITLAB_URL", "GITLAB_TOKEN", "GITLAB_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)

    import app.main as main
    importlib.reload(main)
    try:
        with TestClient(main.app, headers={"X-TeamAI-User": "test-user"}) as client:
            yield client
    finally:
        with psycopg.connect(database_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_issue_to_reviewed_knowledge_lifecycle(postgres_api):
    api = postgres_api
    issue = api.post("/v1/issues", json={
        "title": "PostgreSQL integration issue",
        "content": "A transient startup failure was observed.",
        "project": "issuehub",
        "work_type": "组件开发",
        "tech_stack": ["FastAPI", "PostgreSQL"],
    }).json()
    assert issue["project"] == "issuehub"
    assert issue["work_type"] == "组件开发"
    assert issue["tech_stack"] == ["FastAPI", "PostgreSQL"]
    assert api.get("/v1/search", params={"q": "PostgreSQL"}).json()[0]["id"] == issue["id"]

    resolved = api.post(f"/v1/issues/{issue['id']}/resolve", json={
        "solution": "Wait for database readiness before connecting.",
        "verification": "Repeated five cold starts; first request succeeded each time.",
    }).json()
    assert resolved["knowledge_status"] == "published"
    article_id = resolved["knowledge_record_id"]
    article = api.get(f"/v1/issues/{article_id}").json()
    assert article["kind"] == "knowledge"
    assert article["parent_issue_id"] == issue["id"]
    assert api.get("/v1/search", params={"kind": "knowledge"}).json()[0]["id"] == article_id

    stale = api.post(f"/v1/issues/{issue['id']}/reopen", json={"solution": "A regression was found."}).json()
    assert stale["knowledge_status"] == "needs_review"
    assert api.get("/v1/search", params={"kind": "knowledge"}).json() == []
    revised_issue = api.post(f"/v1/issues/{issue['id']}/resolve", json={
        "solution": "Apply the corrected readiness check.",
        "verification": "Repeated five cold starts after the correction; all passed.",
    }).json()
    assert revised_issue["knowledge_status"] == "published"
    assert revised_issue["knowledge_record_id"] == article_id
    revised = api.get(f"/v1/issues/{article_id}").json()
    assert revised["id"] == article_id
    assert "corrected readiness check" in revised["content"]


def test_postgres_concurrent_issue_updates_keep_both_solutions(postgres_api):
    api = postgres_api
    issue = api.post("/v1/issues", json={"content": "Concurrent PostgreSQL updates must not overwrite each other."}).json()

    def append(solution):
        return api.post(f"/v1/issues/{issue['id']}/append-solution", json={"solution": solution}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(append, ("first concurrent solution", "second concurrent solution")))
    assert statuses == [200, 200]
    content = api.get(f"/v1/issues/{issue['id']}").json()["content"]
    assert "first concurrent solution" in content
    assert "second concurrent solution" in content


def test_postgres_concurrent_knowledge_publish_creates_one_article(postgres_api):
    api = postgres_api
    import app.main as main

    issue = api.post("/v1/issues", json={"content": "Only one article may be published for this issue."}).json()
    resolved = api.post(f"/v1/issues/{issue['id']}/resolve", json={"solution": "Apply stable fix.", "verification": "Passed five times."}).json()
    with main.db() as conn:
        conn.execute(
            "UPDATE records SET knowledge_status = 'pending_review', knowledge_record_id = NULL WHERE id = ?",
            (issue["id"],),
        )
        if resolved.get("knowledge_record_id"):
            conn.execute("DELETE FROM record_events WHERE record_id = ?", (resolved["knowledge_record_id"],))
            conn.execute("DELETE FROM records WHERE id = ?", (resolved["knowledge_record_id"],))

    def publish(reviewer):
        return api.post(f"/v1/issues/{issue['id']}/publish-knowledge", json={
            "title": "Verified concurrent publish",
            "content": "A reusable, verified article.",
            "reviewer": reviewer,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(publish, ("reviewer-a", "reviewer-b")))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(api.get("/v1/search", params={"kind": "knowledge"}).json()) == 1


def test_postgres_record_edit_persists_metadata_and_rejects_resolved_issue_edit(postgres_api):
    api = postgres_api
    record = api.post("/v1/notes", json={"title": "Before edit", "content": "PostgreSQL edit check", "project": "old"}).json()
    edited = api.put(f"/v1/records/{record['id']}", json={
        "expected_updated_at": record["updated_at"],
        "title": "After edit", "content": "Updated PostgreSQL edit check", "project": "issuehub",
        "tech_stack": ["FastAPI", "PostgreSQL"], "tags": ["ops"],
    })
    assert edited.status_code == 200
    assert edited.json()["title"] == "After edit"
    assert edited.json()["project"] == "issuehub"
    assert edited.json()["tech_stack"] == ["FastAPI", "PostgreSQL"]
    assert "type::note" in edited.json()["tags"]
    assert "ops" in edited.json()["tags"]
    stale = api.put(f"/v1/records/{record['id']}", json={
        "expected_updated_at": record["updated_at"], "title": "Stale edit", "content": "Must not overwrite",
        "project": "", "tech_stack": [], "tags": [],
    })
    assert stale.status_code == 409
    assert api.get(f"/v1/issues/{record['id']}").json()["title"] == "After edit"

    issue = api.post("/v1/issues", json={"content": "A resolved issue cannot bypass verification by editing."}).json()
    api.post(f"/v1/issues/{issue['id']}/resolve", json={"solution": "fix", "verification": "passed"})
    rejected = api.put(f"/v1/records/{issue['id']}", json={
        "expected_updated_at": issue["updated_at"],
        "title": "Edited resolved issue", "content": "new body", "project": "", "tech_stack": [], "tags": [],
    })
    assert rejected.status_code == 409


def test_postgres_schema_migration_is_recorded_once_and_startup_is_idempotent(postgres_api, monkeypatch):
    import app.main as main

    api = postgres_api
    api.post("/v1/issues", json={"content": "Schema migration startup check"})
    with main.db() as conn:
        before = conn.execute("SELECT sync_key FROM records").fetchone()["sync_key"]
        versions_before = conn.execute("SELECT version FROM teamai_schema_migrations ORDER BY version").fetchall()
    assert [row["version"] for row in versions_before] == [1, 2, 3, 4, 5]

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _index: main.init_db(), range(4)))

    with main.db() as conn:
        after = conn.execute("SELECT sync_key FROM records").fetchone()["sync_key"]
        versions_after = conn.execute("SELECT version FROM teamai_schema_migrations ORDER BY version").fetchall()
    assert after == before
    assert [row["version"] for row in versions_after] == [1, 2, 3, 4, 5]


def test_postgres_startup_rejects_database_schema_from_newer_application(postgres_api):
    import app.main as main

    with main.db() as conn:
        conn.execute("INSERT INTO teamai_schema_migrations (version, applied_at) VALUES (999, 'future')")
    with pytest.raises(RuntimeError, match="newer than this application"):
        main.init_db()


def test_postgres_startup_rejects_schema_inconsistent_with_migration_record(postgres_api):
    import app.main as main

    with main.db() as conn:
        conn.execute("ALTER TABLE records DROP COLUMN reviewed_by")
    with pytest.raises(RuntimeError, match="migration record is inconsistent"):
        main.init_db()


def test_postgres_sqlite_migration_is_idempotent_and_preserves_legacy_relationships(tmp_path):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL migration integration test")

    schema = f"teamai_migration_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    parts = urlsplit(database_url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "options"]
    query.append(("options", f"-csearch_path={schema}"))
    isolated_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    source_path = tmp_path / "legacy.db"
    issue_content = "Legacy verified issue body"
    article_content = "Legacy reusable knowledge"
    from app.main import content_hash

    with sqlite3.connect(source_path) as source:
        source.execute("""
            CREATE TABLE records (
              id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
              title TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
              content_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              parent_issue_id INTEGER, knowledge_record_id INTEGER
            )
        """)
        source.execute("INSERT INTO records VALUES (1,'issue','web','Legacy issue',?,'resolved',?,'2025-01-01','2025-01-02',NULL,2)", (issue_content, content_hash("Legacy issue", issue_content)))
        source.execute("INSERT INTO records VALUES (2,'knowledge','team-review','Legacy article',?,'resolved',?,'2025-01-02','2025-01-02',1,NULL)", (article_content, content_hash("Legacy article", article_content)))

    script = Path(__file__).resolve().parents[1] / "teamai_migrate.py"
    command = [sys.executable, str(script), str(source_path), "--database-url", isolated_url]
    try:
        first = subprocess.run(command, capture_output=True, text=True, check=True)
        assert "Migrated 2; skipped 0" in first.stdout
        second = subprocess.run(command, capture_output=True, text=True, check=True)
        assert "Migrated 0; skipped 2 (same ID: 2, same content hash: 2); unresolved relationships: 0." in second.stdout
        with psycopg.connect(isolated_url) as target:
            rows = target.execute("SELECT id, kind, knowledge_status, parent_issue_id, knowledge_record_id FROM records ORDER BY id").fetchall()
            assert [row[0] for row in rows] == [1, 2]
            assert rows[0][2] == "pending_review"
            assert rows[0][4] == 2
            assert rows[1][2] == "published"
            assert rows[1][3] == 1
            next_id = target.execute("INSERT INTO records (kind,source,title,content,content_hash,created_at,updated_at) VALUES ('note','test','Next','Next','next-hash','2026','2026') RETURNING id").fetchone()[0]
            assert next_id > 2
        with sqlite3.connect(source_path) as source:
            assert source.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    finally:
        with psycopg.connect(database_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_sqlite_migration_rolls_back_entire_import_on_invalid_row(tmp_path):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL migration rollback test")

    schema = f"teamai_migration_rollback_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    parts = urlsplit(database_url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "options"]
    query.append(("options", f"-csearch_path={schema}"))
    isolated_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    source_path = tmp_path / "invalid-rows.db"
    with sqlite3.connect(source_path) as source:
        source.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, kind TEXT, source TEXT, title TEXT, content TEXT, status TEXT, content_hash TEXT, created_at TEXT, updated_at TEXT)")
        source.execute("INSERT INTO records VALUES (1,'note','legacy','Valid row','Valid content','triage','valid-hash','2025','2025')")
        source.execute("INSERT INTO records VALUES (2,'note','legacy',NULL,'Invalid title','triage','invalid-hash','2025','2025')")

    script = Path(__file__).resolve().parents[1] / "teamai_migrate.py"
    command = [sys.executable, str(script), str(source_path), "--database-url", isolated_url]
    try:
        failed = subprocess.run(command, capture_output=True, text=True)
        assert failed.returncode != 0
        with psycopg.connect(isolated_url) as target:
            assert target.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
    finally:
        with psycopg.connect(database_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
