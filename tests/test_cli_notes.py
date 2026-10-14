import json

import teamai_cli


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
        return {"id": 17, "owner_username": username}

    monkeypatch.setattr(teamai_cli, "request", fake_request)
    monkeypatch.setenv("TEAMAI_USER", "alice")

    monkeypatch.setattr("sys.argv", ["teamai", "note", "New content", "--project", "demo", "--work-type", "工作日常", "--visibility", "private"])
    teamai_cli.main()
    created = json.loads(capsys.readouterr().out)
    assert created["owner_username"] == "alice"
    assert calls[-1][0:2] == ("POST", "/v1/notes")
    assert calls[-1][2]["work_type"] == "工作日常"

    monkeypatch.setattr("sys.argv", ["teamai", "notes", "list", "--project", "demo", "--work-type", "算法调研"])
    teamai_cli.main()
    assert calls[-1][3] == "alice"
    assert "kind=note" in calls[-1][1]

    monkeypatch.setattr("sys.argv", ["teamai", "notes", "update", "17", "--title", "Revised"])
    teamai_cli.main()
    assert calls[-2][1] == "/v1/notes/17"
    assert calls[-1][0:2] == ("PUT", "/v1/records/17")
    assert calls[-1][2]["title"] == "Revised"

    monkeypatch.setattr("sys.argv", ["teamai", "notes", "delete", "17"])
    teamai_cli.main()
    assert calls[-1] == ("DELETE", "/v1/notes/17", None, "alice")
