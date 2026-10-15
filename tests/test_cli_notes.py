import json

import teamai_cli
import teamai_transport as transport


def test_notes_cli_supports_username_scoped_crud(monkeypatch, capsys):
    record = {
        "id": 17, "kind": "note", "title": "Draft", "content": "Body", "project": "demo",
        "work_type": "算法调研", "visibility": "private", "owner_username": "alice",
        "updated_at": "2026-01-01T00:00:00+00:00", "tags": ["source::cli", "type::note", "topic"],
        "tech_stack": [],
    }
    calls = []

    def fake_request(method, path, payload=None, username=None):
        calls.append((method, path, payload, username))
        if method == "GET" and path == "/v1/notes/17":
            return record.copy()
        if method == "GET" and path.startswith("/v1/search?"):
            return [record.copy()]
        if method == "PUT":
            return payload
        if method == "DELETE":
            return {"deleted": True, "id": 17}
        return {"id": 17, "kind": "note", "owner_username": username}

    monkeypatch.setattr(teamai_cli, "request", fake_request)
    monkeypatch.setenv("TEAMAI_USER", "alice")
    monkeypatch.delenv("TEAMAI_WEB_URL", raising=False)
    monkeypatch.setattr(transport, "API", "http://10.110.66.11:8787")

    monkeypatch.setattr("sys.argv", ["teamai", "note", "New content", "--project", "demo", "--work-type", "工作日常", "--visibility", "private"])
    teamai_cli.main()
    captured = capsys.readouterr()
    created = json.loads(captured.out)
    assert created["owner_username"] == "alice"
    assert calls[-1][0:2] == ("POST", "/v1/notes")
    assert calls[-1][2]["work_type"] == "工作日常"
    assert "已保存随手记 #17" in captured.err
    assert "http://10.110.66.11:8787/#record=17" in captured.err
    assert "Issue Hub「随手记」页" in captured.err

    monkeypatch.setattr("sys.argv", ["teamai", "notes", "list", "--project", "demo", "--work-type", "算法调研"])
    teamai_cli.main()
    assert calls[-1][3] == "alice"
    assert "kind=note" in calls[-1][1]
    assert "#record=" not in capsys.readouterr().err

    monkeypatch.setattr("sys.argv", ["teamai", "notes", "update", "17", "--title", "Revised"])
    teamai_cli.main()
    assert calls[-2][1] == "/v1/notes/17"
    assert calls[-1][0:2] == ("PUT", "/v1/records/17")
    assert calls[-1][2]["title"] == "Revised"

    monkeypatch.setattr("sys.argv", ["teamai", "notes", "delete", "17"])
    teamai_cli.main()
    assert calls[-1] == ("DELETE", "/v1/notes/17", None, "alice")


def test_cli_view_hint_prefers_teamai_web_url(monkeypatch, capsys):
    def fake_request(method, path, payload=None, username=None):
        return {"id": 9, "kind": "issue", "title": "Timeout"}

    monkeypatch.setattr(teamai_cli, "request", fake_request)
    monkeypatch.setattr(transport, "API", "http://127.0.0.1:8787")
    monkeypatch.setenv("TEAMAI_WEB_URL", "http://10.110.66.11:8787")
    monkeypatch.setattr("sys.argv", ["teamai", "issue", "Deploy failed"])
    teamai_cli.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["id"] == 9
    assert "已保存问题 #9" in captured.err
    assert "http://10.110.66.11:8787/#record=9" in captured.err
    assert "http://127.0.0.1:8787" not in captured.err
    assert "Issue Hub「问题」页" in captured.err


def test_cli_view_hint_skips_invalid_web_url(monkeypatch, capsys):
    def fake_request(method, path, payload=None, username=None):
        return {"id": 3, "kind": "note"}

    monkeypatch.setattr(teamai_cli, "request", fake_request)
    monkeypatch.setenv("TEAMAI_USER", "alice")
    monkeypatch.setenv("TEAMAI_WEB_URL", "https://user:secret@example.invalid")
    monkeypatch.setattr(transport, "API", "not-a-url")
    monkeypatch.setattr("sys.argv", ["teamai", "note", "Quiet save"])
    teamai_cli.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["id"] == 3
    assert captured.err == ""
