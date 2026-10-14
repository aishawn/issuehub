import importlib
import json
import sqlite3
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Event, Thread

import pytest
from starlette.testclient import TestClient
import teamai_backup
from teamai_backup import backup_database, restore_database, verify_database

BACKUP_TEST_SCHEMA = """CREATE TABLE records (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
    author_id TEXT, session_id TEXT, title TEXT NOT NULL, content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'triage',
    external_system TEXT, external_id TEXT, external_url TEXT,
    content_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)"""


def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "60")
    monkeypatch.delenv("TEAMAI_API_KEY", raising=False)
    for name in ("GITLAB_URL", "GITLAB_TOKEN", "GITLAB_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_issue_lifecycle_and_overview(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        created = api.post("/v1/issues", json={"content": "无法连接 MCP 服务", "source": "qwen-code", "tags": ["mcp"]})
        assert created.status_code == 200
        issue = created.json()
        assert issue["kind"] == "issue"
        assert "mcp" in issue["tags"]

        assert api.get("/v1/search", params={"source": "qwen-code", "kind": "issue"}).json()[0]["id"] == issue["id"]
        assert api.get("/v1/overview").json()["by_status"]["triage"] == 1

        resolved = api.post(f"/v1/issues/{issue['id']}/resolve", json={"solution": "补充 MCP 配置并重启", "source": "test"})
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert "补充 MCP 配置" in resolved.json()["content"]
        assert api.get("/v1/overview").json()["by_status"]["resolved"] == 1
        reopened = api.post(f"/v1/issues/{issue['id']}/reopen", json={"solution": "回归后重新打开", "source": "test"})
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "triage"


def test_redacts_credentials_and_deduplicates(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        payload = {"content": "token: secret-value glpat-abcdefghijklmnop ghp_123456789012345678901234567890 AKIAIOSFODNN7EXAMPLE", "source": "teamai-cli"}
        first = api.post("/v1/notes", json=payload).json()
        assert "secret-value" not in first["content"]
        assert "ghp_123456789012345678901234567890" not in first["content"]
        assert "AKIAIOSFODNN7EXAMPLE" not in first["content"]
        assert "[REDACTED]" in first["content"]
        second = api.post("/v1/notes", json=payload)
        assert second.status_code == 200
        assert second.json()["duplicate"] is True


def test_redacts_credentials_from_metadata_tags_and_resolution_source(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        captured = api.post("/v1/notes", json={
            "content": "metadata redaction check",
            "source": "token=source-secret",
            "author_id": "password=author-secret",
            "session_id": "ghp_123456789012345678901234567890",
            "tags": ["secret=tag-secret", "glpat-abcdefghijklmnopqrst"],
        }).json()
        serialized = json.dumps(captured)
        for secret in ("source-secret", "author-secret", "123456789012345678901234567890", "tag-secret", "abcdefghijklmnopqrst"):
            assert secret not in serialized
        assert captured["source"] == "token=[REDACTED]"
        assert captured["author_id"] == "password=[REDACTED]"
        assert "secret=[REDACTED]" in captured["tags"]

        issue = api.post("/v1/issues", json={"content": "resolution source redaction check"}).json()
        resolved = api.post(f"/v1/issues/{issue['id']}/resolve", json={
            "solution": "A verified fix",
            "source": "api_key=resolution-secret",
        }).json()
        assert "resolution-secret" not in resolved["content"]
        assert "api_key=[REDACTED]" in resolved["content"]


def test_api_key_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("TEAMAI_API_KEY", "test-key")
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as api:
        rejected = api.get("/v1/search")
        assert rejected.status_code == 401
        for alias in ("/%76%31/search", "/v1%2Fsearch", "/v1//search"):
            assert api.get(alias).status_code == 401, f"API key guard missed normalized path {alias}"
        assert rejected.headers["x-content-type-options"] == "nosniff"
        assert rejected.headers["x-frame-options"] == "DENY"
        assert api.get("/v1/search", headers={"X-TeamAI-Key": "test-key"}).status_code == 200


def test_request_id_validation_and_generic_internal_error_response(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        import app.main as main

        def fail():
            raise RuntimeError("private exception detail")

        main.app.add_api_route("/test/internal-error", fail)
        correlated = api.get("/health", headers={"X-Request-ID": "test-run-42"})
        assert correlated.headers["x-request-id"] == "test-run-42"
        invalid = api.get("/health", headers={"X-Request-ID": "bad id"})
        assert invalid.headers["x-request-id"] != "bad id"
        failed = api.get("/test/internal-error")
        assert failed.status_code == 500
        assert failed.json()["detail"] == "Internal server error"
        assert "private exception detail" not in failed.text
        assert failed.json()["request_id"] == failed.headers["x-request-id"]


def test_startup_rejects_partial_gitlab_and_short_production_key(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB", str(tmp_path / "config.db"))
    monkeypatch.delenv("TEAMAI_API_KEY", raising=False)
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)
    import app.main as main
    importlib.reload(main)
    with pytest.raises(RuntimeError, match="requires GITLAB_URL"):
        with TestClient(main.app):
            pass

    monkeypatch.delenv("GITLAB_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TEAMAI_API_KEY", "too-short")
    importlib.reload(main)
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        with TestClient(main.app):
            pass


def test_startup_rejects_invalid_gitlab_port_and_header_token(monkeypatch):
    import app.main as main

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("GITLAB_TOKEN", "valid-token")
    monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
    for url in ("https://gitlab.example:not-a-port", "https://gitlab.example:0", "https://[invalid"):
        monkeypatch.setenv("GITLAB_URL", url)
        with pytest.raises(RuntimeError, match="GITLAB_URL"):
            main.validate_config()

    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
    for token in ("token with spaces", "token\nvalue", "令牌"):
        monkeypatch.setenv("GITLAB_TOKEN", token)
        with pytest.raises(RuntimeError, match="GITLAB_TOKEN"):
            main.validate_config()


def test_startup_rejects_api_keys_that_cannot_be_sent_as_headers(monkeypatch):
    import app.main as main

    monkeypatch.setenv("APP_ENV", "development")
    for name in ("GITLAB_URL", "GITLAB_TOKEN", "GITLAB_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    for value in ("key with spaces", "key\nvalue", "密钥", " key "):
        monkeypatch.setenv("TEAMAI_API_KEY", value)
        importlib.reload(main)
        with pytest.raises(RuntimeError, match="TEAMAI_API_KEY"):
            main.validate_config()
    monkeypatch.delenv("TEAMAI_API_KEY", raising=False)
    importlib.reload(main)


def test_sync_retry_interval_validation(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch):
        import app.main as main

        for value, expected in (("0", 0), ("0.5", 0.5), ("86400", 86400)):
            monkeypatch.setenv("SYNC_INTERVAL_SECONDS", value)
            assert main.sync_interval_seconds() == expected
        for value in ("-1", "86401", "nan", "invalid"):
            monkeypatch.setenv("SYNC_INTERVAL_SECONDS", value)
            with pytest.raises(RuntimeError, match="SYNC_INTERVAL_SECONDS"):
                main.sync_interval_seconds()


def test_sync_is_safe_without_gitlab(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        result = api.post("/v1/sync")
        assert result.status_code == 200
        assert result.json()["message"] == "GitLab is not configured"
        assert api.get("/health").json()["pending_sync"] == 0
        assert api.get("/health").headers["cache-control"] == "no-store"
        assert "db" not in api.get("/health").json()


def test_background_retry_recovers_pending_gitlab_record(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB", str(tmp_path / "auto-sync.db"))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "0.05")
    monkeypatch.delenv("TEAMAI_API_KEY", raising=False)
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
    monkeypatch.setenv("GITLAB_TOKEN", "test")
    monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
    import app.main as main
    importlib.reload(main)

    gitlab_online = Event()
    synced = Event()

    def fake_request(method, path, payload=None):
        if not gitlab_online.is_set():
            raise urllib.error.URLError("temporary outage")
        if method == "GET":
            return []
        synced.set()
        return {"iid": 101, "web_url": "https://gitlab.example/issues/101"}

    monkeypatch.setattr(main, "gitlab_request", fake_request)
    with TestClient(main.app) as api:
        created = api.post("/v1/issues", json={"content": "断网后自动恢复同步"}).json()
        assert created["sync_error"]
        assert api.get("/health").json()["pending_sync"] == 1

        gitlab_online.set()
        assert synced.wait(timeout=3), "background retry did not publish the pending record"
        deadline = time.monotonic() + 3
        recovered = api.get(f"/v1/issues/{created['id']}").json()
        while recovered["external_id"] is None and time.monotonic() < deadline:
            time.sleep(0.02)
            recovered = api.get(f"/v1/issues/{created['id']}").json()
        assert recovered["external_id"] == "101"
        assert recovered["sync_error"] is None
        assert api.get("/health").json()["pending_sync"] == 0


def test_sync_updates_existing_gitlab_issue_and_posts_only_missing_ids(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        import app.main as main

        existing = api.post("/v1/issues", json={"content": "已有远端问题"}).json()
        new = api.post("/v1/issues", json={"content": "尚未创建远端问题"}).json()
        with main.db() as conn:
            conn.execute("UPDATE records SET external_system='gitlab', external_id='42', sync_error='retry' WHERE id=?", (existing["id"],))
            conn.execute("UPDATE records SET sync_error='retry' WHERE id=?", (new["id"],))
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
        monkeypatch.setenv("GITLAB_TOKEN", "test")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
        calls = []

        def fake_request(method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET":
                return []
            return {"iid": 77, "web_url": "https://gitlab.example/issues/77"}

        monkeypatch.setattr(main, "gitlab_request", fake_request)
        result = api.post("/v1/sync").json()
        assert result["synced"] == 2
        assert [call[0] for call in calls] == ["PUT", "GET", "POST"]
        assert calls[0][1].endswith("/issues/42")
        assert calls[2][1].endswith("/issues")
        assert "teamai-sync-" in calls[2][2]["labels"]


def test_gitlab_http_transport_encodes_authenticates_and_sanitizes_errors(monkeypatch):
    import app.main as main

    received = []

    class GitLabHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def send_json(self, status, body):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            received.append(("GET", self.path, self.headers.get("PRIVATE-TOKEN"), None))
            if self.path.endswith("/fail"):
                self.send_json(403, {"message": "temporary-test-token must not leak"})
            else:
                self.send_json(200, [])

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(("POST", self.path, self.headers.get("PRIVATE-TOKEN"), payload))
            self.send_json(201, {"iid": 32, "web_url": "http://gitlab.local/issues/32"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), GitLabHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GITLAB_URL", f"http://127.0.0.1:{server.server_port}/gitlab")
    monkeypatch.setenv("GITLAB_TOKEN", "temporary-test-token")
    monkeypatch.setenv("GITLAB_PROJECT_ID", "group/sub/project")
    try:
        result = main.publish_gitlab("Issue title", "Issue body", ["type::issue"], "a" * 64, "triage")
        assert result == ("gitlab", "32", "http://gitlab.local/issues/32", None)
        assert received[0][:3] == (
            "GET",
            "/gitlab/api/v4/projects/group%2Fsub%2Fproject/issues?labels=teamai-sync-" + "a" * 64 + "&state=all&per_page=100",
            "temporary-test-token",
        )
        assert received[1][0:3] == (
            "POST",
            "/gitlab/api/v4/projects/group%2Fsub%2Fproject/issues",
            "temporary-test-token",
        )
        assert received[1][3] == {
            "title": "Issue title",
            "description": "Issue body",
            "labels": "type::issue,status::triage,teamai-sync-" + "a" * 64,
        }

        with pytest.raises(urllib.error.HTTPError) as raised:
            main.gitlab_request("GET", "/fail")
        error = main.gitlab_error_message(raised.value)
        assert error == "GitLab returned HTTP 403"
        assert "temporary-test-token" not in error
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_gitlab_transport_does_not_forward_private_token_across_redirects(monkeypatch):
    import app.main as main

    redirected = []
    original_auth = []

    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            redirected.append(self.headers.get("PRIVATE-TOKEN"))
            self.send_response(200)
            self.end_headers()

    destination = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            original_auth.append(self.headers.get("PRIVATE-TOKEN"))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/capture")
            self.end_headers()

    source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [Thread(target=server.serve_forever, daemon=True) for server in (source, destination)]
    for thread in threads:
        thread.start()
    monkeypatch.setenv("GITLAB_URL", f"http://127.0.0.1:{source.server_port}")
    monkeypatch.setenv("GITLAB_TOKEN", "gitlab-redirect-test-token")
    monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
    try:
        with pytest.raises(urllib.error.HTTPError) as raised:
            main.gitlab_request("GET", "/redirect")
        assert raised.value.code == 302
    finally:
        for server in (source, destination):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert original_auth == ["gitlab-redirect-test-token"]
    assert redirected == []


def test_malformed_gitlab_response_keeps_local_record_and_safe_error(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        import app.main as main

        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
        monkeypatch.setenv("GITLAB_TOKEN", "never-store-this-token")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
        monkeypatch.setattr(main, "gitlab_request", lambda method, *args, **kwargs: [] if method == "GET" else ["invalid"])
        created = api.post("/v1/issues", json={"content": "GitLab 返回异常响应"})
        assert created.status_code == 200
        record = created.json()
        assert record["id"]
        assert record["external_id"] is None
        assert "invalid issue response" in record["sync_error"]
        assert "never-store-this-token" not in record["sync_error"]
        assert api.get(f"/v1/issues/{record['id']}").status_code == 200


def test_gitlab_create_retry_reconciles_issue_after_lost_response(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        import app.main as main

        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
        monkeypatch.setenv("GITLAB_TOKEN", "test")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
        remote_issues = []
        post_count = 0
        updates = []

        def fake_request(method, path, payload=None):
            nonlocal post_count
            if method == "GET":
                return list(remote_issues)
            if method == "POST":
                post_count += 1
                remote_issues.append({"iid": 88, "web_url": "https://gitlab.example/issues/88", "labels": payload["labels"].split(",")})
                raise urllib.error.URLError("response lost after remote commit")
            updates.append(payload)
            return {}

        monkeypatch.setattr(main, "gitlab_request", fake_request)
        created = api.post("/v1/issues", json={"content": "远端创建成功但响应丢失"}).json()
        assert created["external_id"] is None
        assert created["sync_error"]

        appended = api.post(f"/v1/issues/{created['id']}/append-solution", json={"solution": "失败窗口内补充的最新内容"}).json()
        assert appended["sync_error"] is None
        updated = api.get(f"/v1/issues/{created['id']}").json()
        assert updated["external_id"] == "88"
        assert "失败窗口内补充的最新内容" in updates[0]["description"]
        assert post_count == 1


def test_concurrent_gitlab_capture_claims_one_local_record_and_one_issue(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        import app.main as main

        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
        monkeypatch.setenv("GITLAB_TOKEN", "test")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
        lookup_started = Event()
        release_lookup = Event()
        posts = []

        def fake_request(method, path, payload=None):
            if method == "GET":
                lookup_started.set()
                assert release_lookup.wait(timeout=5)
                return []
            posts.append(payload)
            return {"iid": 91, "web_url": "https://gitlab.example/issues/91"}

        monkeypatch.setattr(main, "gitlab_request", fake_request)
        payload = {"content": "并发时 GitLab 也只创建一次", "source": "web"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(api.post, "/v1/issues", json=payload)
            assert lookup_started.wait(timeout=5)
            second = pool.submit(api.post, "/v1/issues", json=payload)
            second_response = second.result(timeout=5)
            release_lookup.set()
            first_response = first.result(timeout=5)

        assert first_response.status_code == second_response.status_code == 200
        assert first_response.json()["id"] == second_response.json()["id"]
        assert len(posts) == 1


def test_concurrent_duplicate_capture_is_safe_without_legacy_unique_index(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL, author_id TEXT, session_id TEXT, title TEXT NOT NULL, content TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'triage', external_system TEXT, external_id TEXT, external_url TEXT, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO records (kind, source, title, content, tags, status, content_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("note", "legacy", "old", "old", "[]", "triage", "legacy-duplicate", "2020", "2020"),
                ("note", "legacy", "old", "old", "[]", "triage", "legacy-duplicate", "2020", "2020"),
            ],
        )
        conn.commit()

    with client(tmp_path, monkeypatch) as api:
        import app.main as main

        payload = {"content": "两条并发录入不能产生重复本地记录", "source": "web"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(api.post, "/v1/issues", json=payload)
            second = pool.submit(api.post, "/v1/issues", json=payload)
            results = [first.result(timeout=5), second.result(timeout=5)]

        assert all(result.status_code == 200 for result in results)
        assert results[0].json()["id"] == results[1].json()["id"]
        assert sum(bool(result.json().get("duplicate")) for result in results) == 1
        with main.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM records WHERE content_hash = ?", (main.content_hash(results[0].json()["title"], results[0].json()["content"]),)).fetchone()[0] == 1


def test_issue_reopen_updates_gitlab_and_keeps_local_change_on_failure(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        import app.main as main

        issue = api.post("/v1/issues", json={"content": "需要重新打开"}).json()
        with main.db() as conn:
            conn.execute("UPDATE records SET external_system='gitlab', external_id='9' WHERE id=?", (issue["id"],))
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
        monkeypatch.setenv("GITLAB_TOKEN", "test")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
        calls = []

        def failing_request(method, path, payload=None):
            calls.append((method, payload))
            raise urllib.error.URLError("GitLab unavailable")

        monkeypatch.setattr(main, "gitlab_request", failing_request)
        response = api.post(f"/v1/issues/{issue['id']}/reopen", json={"solution": "复现仍存在", "source": "test"})
        assert response.status_code == 200
        assert response.json()["status"] == "triage"
        assert "复现仍存在" in response.json()["content"]
        assert response.json()["sync_error"]
        assert calls[0][0] == "PUT"
        assert calls[0][1]["state_event"] == "reopen"
        assert calls[0][1]["add_labels"] == "status::triage"
        assert "status::resolved" in calls[0][1]["remove_labels"]


def test_gitlab_status_sync_updates_labels_and_close_event(monkeypatch):
    import app.main as main

    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example")
    monkeypatch.setenv("GITLAB_TOKEN", "test")
    monkeypatch.setenv("GITLAB_PROJECT_ID", "group/project")
    calls = []
    monkeypatch.setattr(main, "gitlab_request", lambda method, path, payload=None: calls.append(payload) or {})
    for status in ("in-progress", "resolved"):
        assert main.update_gitlab("12", "description", status) is None
    assert calls[0]["add_labels"] == "status::in-progress"
    assert calls[0]["remove_labels"] == "status::resolved,status::triage"
    assert calls[1]["add_labels"] == "status::resolved"
    assert calls[1]["state_event"] == "close"
    assert calls[1]["remove_labels"] == "status::in-progress,status::triage"


def test_database_parent_is_created_and_connections_use_safe_pragmas(tmp_path, monkeypatch):
    db_path = tmp_path / "nested" / "data" / "teamai.db"
    monkeypatch.setenv("APP_DB", str(db_path))
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as api:
        assert api.get("/health").status_code == 200
    assert db_path.exists()
    with main.db() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_database_schema_migrates_legacy_records_with_sync_key(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, source TEXT NOT NULL,
            author_id TEXT, session_id TEXT, title TEXT NOT NULL, content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'triage',
            external_system TEXT, external_id TEXT, external_url TEXT, content_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO records (kind,source,title,content,content_hash,created_at,updated_at) VALUES ('issue','web','old','body','old-hash','2026-01-01','2026-01-01')")
        conn.commit()
    monkeypatch.setenv("APP_DB", str(db_path))
    monkeypatch.delenv("TEAMAI_API_KEY", raising=False)
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as api:
        assert api.get("/health").status_code == 200
    with main.db() as conn:
        row = conn.execute("SELECT sync_key, sync_lock_until FROM records WHERE id=1").fetchone()
        assert len(row["sync_key"]) == 64
        assert row["sync_lock_until"] is None


def test_duplicate_content_conflict_does_not_partially_update_issue(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        issue = api.post("/v1/issues", json={"title": "另一个问题", "content": "其他内容"}).json()
        intended_content = issue["content"] + "\n\n## 追加解决方案\n\n复现步骤"
        api.post("/v1/notes", json={"title": issue["title"], "content": intended_content})
        response = api.post(f"/v1/issues/{issue['id']}/append-solution", json={"solution": "复现步骤"})
        assert response.status_code == 409
        assert api.get(f"/v1/issues/{issue['id']}").json()["content"] == issue["content"]


def test_search_filters_pagination_and_security_headers(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        page = api.get("/")
        assert page.status_code == 200
        assert "script-src 'self'" in page.headers["content-security-policy"]
        assert api.get("/web/app.js").headers["content-security-policy"] == page.headers["content-security-policy"]
        api.post("/v1/issues", json={"content": "编译器超时", "source": "qwen-code", "tags": ["build"]})
        api.post("/v1/notes", json={"content": "网络排查记录", "source": "web"})
        first_page = api.get("/v1/search", params={"source": "qwen-code", "kind": "issue", "limit": 1, "offset": 0})
        assert first_page.status_code == 200
        assert len(first_page.json()) == 1
        assert first_page.json()[0]["source"] == "qwen-code"
        assert first_page.headers["x-content-type-options"] == "nosniff"
        assert first_page.headers["x-frame-options"] == "DENY"
        assert first_page.headers["cache-control"] == "no-store"
        assert api.get("/v1/issues/9999").status_code == 404


def test_search_treats_like_metacharacters_as_literal_text(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        literal = r"rate 100% alpha_beta C:\team"
        wildcard_matches = "rate 100x alphaXbeta C:ateam"
        api.post("/v1/notes", json={"content": literal})
        api.post("/v1/notes", json={"content": wildcard_matches})

        for term in ("100%", "alpha_beta", r"C:\team"):
            response = api.get("/v1/search", params={"q": term})
            assert response.status_code == 200
            assert [item["content"] for item in response.json()] == [literal]


def test_search_pagination_is_stable_when_records_share_updated_at(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        import app.main as main

        records = [api.post("/v1/notes", json={"content": f"stable page record {index}"}).json() for index in range(3)]
        with main.db() as conn:
            conn.execute("UPDATE records SET updated_at = '2026-01-01T00:00:00+00:00'")

        first_page = api.get("/v1/search", params={"q": "stable page", "limit": 2, "offset": 0}).json()
        second_page = api.get("/v1/search", params={"q": "stable page", "limit": 2, "offset": 2}).json()

        assert [row["id"] for row in first_page + second_page] == [row["id"] for row in reversed(records)]


def test_concurrent_identical_captures_are_deduplicated(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        payload = {"content": "并发提交同一条记录", "source": "web"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: api.post("/v1/notes", json=payload), range(8)))
        assert all(response.status_code == 200 for response in responses)
        assert len({response.json()["id"] for response in responses}) == 1
        assert sum(not response.json().get("duplicate", False) for response in responses) == 1


def test_concurrent_issue_updates_preserve_every_appended_solution(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        issue = api.post("/v1/issues", json={"content": "并发追加时不能丢方案"}).json()
        start = Barrier(8)

        def append(index):
            start.wait(timeout=5)
            return api.post(
                f"/v1/issues/{issue['id']}/append-solution",
                json={"solution": f"并发方案 {index}", "source": "test"},
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(append, range(8)))

        assert all(response.status_code == 200 for response in responses)
        content = api.get(f"/v1/issues/{issue['id']}").json()["content"]
        assert all(f"并发方案 {index}" in content for index in range(8))


def test_input_validation_normalizes_tags_and_rejects_blank_or_oversized_values(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as api:
        created = api.post("/v1/notes", json={"content": "有标签的记录", "tags": [" build ", "build", "mcp"]})
        assert created.status_code == 200
        assert created.json()["tags"] == ["build", "mcp", "source::web", "type::note"]
        assert api.post("/v1/notes", json={"content": "无效来源", "source": "  "}).status_code == 422
        assert api.post("/v1/notes", json={"content": "超长标签", "tags": ["x" * 81]}).status_code == 422
        assert api.post("/v1/issues/1/append-solution", json={"solution": "  "}).status_code == 422


def test_database_backup_verify_and_restore_preserve_safety_copy(tmp_path):
    database = tmp_path / "teamai.db"
    snapshot = tmp_path / "backups" / "teamai.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(BACKUP_TEST_SCHEMA)
        conn.execute("INSERT INTO records (id, kind, source, title, content, content_hash, created_at, updated_at) VALUES (1, 'issue', 'test', 'original', 'snapshot content', 'hash-1', '2026-01-01', '2026-01-01')")
        conn.commit()
    assert backup_database(database, snapshot) == 1
    assert verify_database(snapshot) == 1
    with pytest.raises(FileExistsError):
        backup_database(database, snapshot)

    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO records (id, kind, source, title, content, content_hash, created_at, updated_at) VALUES (2, 'note', 'test', 'newer', 'current content', 'hash-2', '2026-01-02', '2026-01-02')")
        conn.commit()
    restored_count, safety_copy = restore_database(snapshot, database)
    assert restored_count == 1
    assert verify_database(database) == 1
    assert verify_database(safety_copy) == 2

    fresh_database = tmp_path / "new-install" / "teamai.db"
    fresh_count, fresh_safety_copy = restore_database(snapshot, fresh_database)
    assert fresh_count == 1
    assert fresh_safety_copy is None
    assert verify_database(fresh_database) == 1


def test_concurrent_backups_never_overwrite_the_same_destination(tmp_path, monkeypatch):
    database = tmp_path / "teamai.db"
    snapshot = (tmp_path / "backups" / "teamai.db").resolve()
    with closing(sqlite3.connect(database)) as conn:
        conn.execute(BACKUP_TEST_SCHEMA)
        conn.execute("INSERT INTO records (id, kind, source, title, content, content_hash, created_at, updated_at) VALUES (1, 'note', 'test', 'source', 'content', 'hash', '2026-01-01', '2026-01-01')")
        conn.commit()

    original_publish = teamai_backup._publish_backup
    both_checked = Barrier(2)

    def synchronized_publish(stage, path):
        both_checked.wait(timeout=5)
        return original_publish(stage, path)

    monkeypatch.setattr(teamai_backup, "_publish_backup", synchronized_publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(backup_database, database, snapshot) for _ in range(2)]
        successes = []
        rejections = 0
        for future in futures:
            try:
                successes.append(future.result(timeout=10))
            except FileExistsError:
                rejections += 1

    assert successes == [1]
    assert rejections == 1
    assert verify_database(snapshot) == 1
    assert not list(snapshot.parent.glob(".teamai.db.backup-*.tmp"))


def test_failed_backup_verification_leaves_no_published_or_temporary_file(tmp_path):
    corrupt_source = tmp_path / "corrupt.db"
    destination = tmp_path / "backups" / "teamai.db"
    corrupt_source.write_bytes(b"not a valid sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        backup_database(corrupt_source, destination)

    assert not destination.exists()
    assert not list(destination.parent.glob(".teamai.db.backup-*.tmp"))


def test_backup_verification_rejects_incomplete_record_schema(tmp_path):
    incomplete = tmp_path / "incomplete.db"
    with closing(sqlite3.connect(incomplete)) as conn:
        conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, kind TEXT, title TEXT, content TEXT, content_hash TEXT)")
        conn.commit()

    with pytest.raises(sqlite3.DatabaseError, match="missing columns"):
        verify_database(incomplete)


def test_backup_cli_creates_and_verifies_snapshot(tmp_path, monkeypatch):
    database = tmp_path / "source.db"
    snapshot = tmp_path / "backups" / "snapshot.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute(BACKUP_TEST_SCHEMA)
        conn.commit()
    monkeypatch.setattr(teamai_backup, "DB_PATH", database)
    assert teamai_backup.main(["backup", str(snapshot)]) == 0
    assert teamai_backup.main(["verify", str(snapshot)]) == 0


def test_restore_rejects_corrupt_backup_without_touching_current_database(tmp_path):
    database = tmp_path / "current.db"
    corrupt = tmp_path / "corrupt.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute(BACKUP_TEST_SCHEMA)
        conn.execute("INSERT INTO records (id, kind, source, title, content, content_hash, created_at, updated_at) VALUES (1, 'note', 'test', 'keep', 'current', 'hash', '2026-01-01', '2026-01-01')")
        conn.commit()
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        restore_database(corrupt, database)
    assert verify_database(database) == 1
    assert not list(tmp_path.glob("*.pre-restore-*.db"))
