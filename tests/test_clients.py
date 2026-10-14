from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path

import pytest
import uvicorn


def test_shared_transport_sends_api_key_and_json_payload(monkeypatch):
    import teamai_transport as transport

    received = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_POST(self):
            received["path"] = self.path
            received["key"] = self.headers.get("X-TeamAI-Key")
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            encoded = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(transport, "API", f"http://127.0.0.1:{server.server_port}/teamai/")
    monkeypatch.setattr(transport, "API_KEY", "local-test-key")
    try:
        assert transport.request("POST", "/v1/notes", {"content": "中文"}) == {"ok": True}
        assert received == {
            "path": "/teamai/v1/notes",
            "key": "local-test-key",
            "body": {"content": "中文"},
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_transport_http_error_does_not_echo_response_body(monkeypatch):
    import teamai_transport as transport

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            encoded = b'{"detail":"private response content"}'
            self.send_response(422)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(transport, "API", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(transport, "API_KEY", "")
    try:
        with pytest.raises(transport.TeamAIRequestError) as raised:
            transport.request("GET", "/v1/search")
        assert str(raised.value) == "TeamAI API returned HTTP 422"
        assert "private response content" not in str(raised.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_transport_does_not_forward_api_key_across_redirects(monkeypatch):
    import teamai_transport as transport

    redirected = []
    original_auth = []

    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            redirected.append(self.headers.get("X-TeamAI-Key"))
            self.send_response(200)
            self.end_headers()

    destination = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            original_auth.append(self.headers.get("X-TeamAI-Key"))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/capture")
            self.end_headers()

    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = [Thread(target=server.serve_forever, daemon=True) for server in (source, destination)]
    for thread in threads:
        thread.start()
    monkeypatch.setattr(transport, "API", f"http://127.0.0.1:{source.server_port}")
    monkeypatch.setattr(transport, "API_KEY", "redirect-test-key")
    try:
        with pytest.raises(transport.TeamAIRequestError, match="HTTP 302"):
            transport.request("GET", "/v1/search")
    finally:
        for server in (source, destination):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert original_auth == ["redirect-test-key"]
    assert redirected == []


def test_transport_rejects_embedded_url_credentials_without_echoing_them(monkeypatch):
    import teamai_transport as transport

    monkeypatch.setattr(transport, "API", "https://user:secret@example.invalid")
    with pytest.raises(transport.TeamAIRequestError) as raised:
        transport.request("GET", "/v1/search")
    assert "user" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_transport_rejects_invalid_api_ports(monkeypatch):
    import teamai_transport as transport

    monkeypatch.setattr(transport, "API_KEY", "")
    for value in ("http://127.0.0.1:not-a-port", "http://127.0.0.1:0", "https://["):
        monkeypatch.setattr(transport, "API", value)
        with pytest.raises(transport.TeamAIRequestError, match="Invalid TEAMAI_API"):
            transport.request("GET", "/v1/search")


def test_transport_rejects_non_header_safe_api_keys(monkeypatch):
    import teamai_transport as transport

    monkeypatch.setattr(transport, "API", "http://127.0.0.1:8787")
    for value in ("key with spaces", "key\nvalue", "密钥"):
        monkeypatch.setattr(transport, "API_KEY", value)
        with pytest.raises(transport.TeamAIRequestError, match="TEAMAI_API_KEY"):
            transport.request("GET", "/v1/search")


def test_cli_and_mcp_never_echo_api_url_credentials(monkeypatch, capsys):
    import teamai_transport as transport

    monkeypatch.setattr(transport, "API", "https://user:secret@example.invalid")
    import teamai_cli
    with pytest.raises(SystemExit) as raised:
        teamai_cli.request("GET", "/v1/search")
    assert "user" not in str(raised.value)
    assert "secret" not in str(raised.value)

    import mcp_server
    importlib.reload(mcp_server)
    monkeypatch.setattr(transport, "API", "https://user:secret@example.invalid")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "teamai_search", "arguments": {"query": "test"}}}) + "\n"),
    )
    mcp_server.main()
    response = json.loads(capsys.readouterr().out)
    error = response["error"]["message"]
    assert "user" not in error
    assert "secret" not in error
    assert "Invalid TEAMAI_API" in error


def test_cli_and_mcp_create_records_through_authenticated_api(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB", str(tmp_path / "clients.db"))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("TEAMAI_API_KEY", "integration-test-key-0123456789")
    for name in ("GITLAB_URL", "GITLAB_TOKEN", "GITLAB_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    import app.main as main
    importlib.reload(main)

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    api_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="critical", lifespan="on", ws="none"))
    server_thread = Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
    server_thread.start()

    project_root = Path(__file__).resolve().parents[1]
    child_env = os.environ.copy()
    child_env["TEAMAI_API"] = api_url
    child_env["TEAMAI_API_KEY"] = "integration-test-key-0123456789"
    try:
        deadline = time.monotonic() + 5
        while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "local API server did not start"

        cli = subprocess.run(
            [sys.executable, "teamai_cli.py", "note", "CLI shared-store integration"],
            cwd=project_root,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert cli.returncode == 0, cli.stderr
        cli_record = json.loads(cli.stdout)
        assert cli_record["source"] == "teamai-cli"

        mcp_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "teamai_create_issue", "arguments": {"content": "MCP shared-store integration"}},
        }
        mcp = subprocess.run(
            [sys.executable, "mcp_server.py"],
            cwd=project_root,
            env=child_env,
            input=json.dumps(mcp_request) + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert mcp.returncode == 0, mcp.stderr
        mcp_response = json.loads(mcp.stdout)
        mcp_record = json.loads(mcp_response["result"]["content"][0]["text"])
        assert mcp_record["source"] == "mcp"

        with main.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
        listener.close()
    assert not server_thread.is_alive(), "local API server did not shut down cleanly"
