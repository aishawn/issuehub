import json

import teamai_cli
import teamai_transport as transport


def test_issues_cli_supports_update_with_field_merge(monkeypatch, capsys):
    record = {
        "id": 21, "kind": "issue", "title": "Deploy timeout", "content": "Pull image failed",
        "project": "demo", "work_type": "模型部署", "status": "triage",
        "updated_at": "2026-01-01T00:00:00+00:00", "tags": ["source::cli", "type::issue", "urgent"],
        "tech_stack": ["Docker"],
    }
    calls = []

    def fake_request(method, path, payload=None, username=None):
        calls.append((method, path, payload, username))
        if method == "GET" and path == "/v1/issues/21":
            return record.copy()
        if method == "PUT":
            return {**record, **payload, "id": 21}
        return {"id": 21}

    monkeypatch.setattr(teamai_cli, "request", fake_request)
    monkeypatch.setenv("TEAMAI_USER", "alice")

    monkeypatch.setattr("sys.argv", ["teamai", "issues", "update", "21", "--title", "Revised timeout", "--content", "Retry with mirror"])
    teamai_cli.main()
    updated = json.loads(capsys.readouterr().out)
    assert updated["title"] == "Revised timeout"
    assert updated["content"] == "Retry with mirror"
    assert calls[-2] == ("GET", "/v1/issues/21", None, "alice")
    assert calls[-1][0:2] == ("PUT", "/v1/records/21")
    assert calls[-1][2]["expected_updated_at"] == record["updated_at"]
    assert calls[-1][2]["title"] == "Revised timeout"
    assert calls[-1][2]["content"] == "Retry with mirror"
    assert calls[-1][2]["project"] == "demo"
    assert calls[-1][2]["work_type"] == "模型部署"
    assert calls[-1][2]["tags"] == ["urgent"]
    assert calls[-1][2]["tech_stack"] == ["Docker"]
    assert calls[-1][3] == "alice"

    monkeypatch.setattr("sys.argv", ["teamai", "issues", "update", "21", "--project", "prod", "--work-type", "项目支撑"])
    teamai_cli.main()
    assert calls[-1][2]["title"] == "Deploy timeout"
    assert calls[-1][2]["project"] == "prod"
    assert calls[-1][2]["work_type"] == "项目支撑"


def test_issues_cli_create_and_resolve_print_hub_view_hints(monkeypatch, capsys):
    def fake_request(method, path, payload=None, username=None):
        if path == "/v1/issues":
            return {"id": 42, "kind": "issue", "title": "Build failed"}
        if path.endswith("/resolve"):
            return {
                "id": 42, "kind": "issue", "status": "resolved",
                "knowledge_record_id": 99, "knowledge_status": "published",
            }
        return {"id": 42}

    monkeypatch.setattr(teamai_cli, "request", fake_request)
    monkeypatch.setattr(transport, "API", "http://10.110.66.11:8787")
    monkeypatch.setenv("TEAMAI_USER", "alice")

    monkeypatch.setattr("sys.argv", ["teamai", "issue", "Build failed"])
    teamai_cli.main()
    created = capsys.readouterr()
    assert json.loads(created.out)["id"] == 42
    assert "已保存问题 #42" in created.err
    assert "http://10.110.66.11:8787/#record=42" in created.err

    monkeypatch.setattr(
        "sys.argv",
        ["teamai", "resolve", "42", "--solution", "Retry", "--verification", "Passed twice"],
    )
    teamai_cli.main()
    resolved = capsys.readouterr()
    assert json.loads(resolved.out)["knowledge_record_id"] == 99
    assert "已保存问题 #42" in resolved.err
    assert "已发布知识 #99：http://10.110.66.11:8787/#record=99" in resolved.err
