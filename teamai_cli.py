from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from teamai_transport import (
    TeamAIRequestError,
    hub_base_url,
    record_view_url,
    request as transport_request,
)


def request(method: str, path: str, payload: dict | None = None, username: str | None = None):
    try:
        return transport_request(method, path, payload, username=username)
    except TeamAIRequestError as exc:
        raise SystemExit(str(exc)) from None


_KIND_LABELS = {
    "note": "随手记",
    "issue": "问题",
    "solution": "方案",
    "knowledge": "知识",
}
_KIND_PAGES = {
    "note": "随手记",
    "issue": "问题",
    "solution": "问题",
    "knowledge": "问题",
}


def print_view_hint(result: object) -> None:
    """Print where to view a newly saved record on Issue Hub (stderr only)."""
    if not isinstance(result, dict) or "id" not in result:
        return
    detail = record_view_url(result["id"])
    if not detail:
        return
    kind = str(result.get("kind") or "")
    label = _KIND_LABELS.get(kind, "记录")
    page = _KIND_PAGES.get(kind)
    hub = hub_base_url()
    lines = [f"已保存{label} #{result['id']}", f"查看详情：{detail}"]
    if page and hub:
        lines.append(f"也可在 Issue Hub「{page}」页浏览：{hub}/")
    knowledge_id = result.get("knowledge_record_id")
    if knowledge_id:
        knowledge_url = record_view_url(knowledge_id)
        if knowledge_url:
            lines.append(f"已发布知识 #{knowledge_id}：{knowledge_url}")
    print("\n".join(lines), file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(prog="teamai", description="TeamAI notes and issue capture")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("note", "issue"):
        command = sub.add_parser(name)
        command.add_argument("content")
        command.add_argument("--title")
        command.add_argument("--source", default="teamai-cli")
        command.add_argument("--tag", action="append", default=[])
        command.add_argument("--project", default="")
        if name == "note":
            command.add_argument("--username", default=os.getenv("TEAMAI_USER", ""))
            command.add_argument("--visibility", choices=("shared", "private"), default="shared")
        command.add_argument("--work-type", choices=("算法调研", "算法预研", "模型部署", "组件开发", "项目支撑", "工作日常"), default="项目支撑")
        command.add_argument("--tech", action="append", default=[])
        if name == "issue":
            command.add_argument("--environment", default="")
            command.add_argument("--reproduction", default="")
    capture = sub.add_parser("capture")
    capture.add_argument("content", nargs="?")
    capture.add_argument("--type", choices=("auto", "note", "issue", "solution"), default="auto")
    capture.add_argument("--title")
    capture.add_argument("--source", default="teamai-cli")
    capture.add_argument("--tag", action="append", default=[])
    capture.add_argument("--project", default="")
    capture.add_argument("--username", default=os.getenv("TEAMAI_USER", ""))
    capture.add_argument("--visibility", choices=("shared", "private"), default="shared")
    capture.add_argument("--work-type", choices=("算法调研", "算法预研", "模型部署", "组件开发", "项目支撑", "工作日常"), default="项目支撑")
    capture.add_argument("--tech", action="append", default=[])
    capture.add_argument("--environment", default="")
    capture.add_argument("--reproduction", default="")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--username", default=os.getenv("TEAMAI_USER", ""))
    notes = sub.add_parser("notes", help="List, read, update, or delete notes")
    notes.add_argument("--username", default=os.getenv("TEAMAI_USER", ""))
    notes_sub = notes.add_subparsers(dest="notes_command", required=True)
    notes_list = notes_sub.add_parser("list", help="List notes visible to this username")
    notes_list.add_argument("--query", default="")
    notes_list.add_argument("--project")
    notes_list.add_argument("--work-type")
    notes_list.add_argument("--visibility", choices=("shared", "private"))
    notes_list.add_argument("--username", default=argparse.SUPPRESS)
    notes_get = notes_sub.add_parser("get", help="Read a note by ID")
    notes_get.add_argument("id", type=int)
    notes_get.add_argument("--username", default=argparse.SUPPRESS)
    notes_update = notes_sub.add_parser("update", help="Update a note you own")
    notes_update.add_argument("id", type=int)
    notes_update.add_argument("--title")
    notes_update.add_argument("--content")
    notes_update.add_argument("--project")
    notes_update.add_argument("--work-type", choices=("算法调研", "算法预研", "模型部署", "组件开发", "项目支撑", "工作日常"))
    notes_update.add_argument("--visibility", choices=("shared", "private"))
    notes_update.add_argument("--username", default=argparse.SUPPRESS)
    notes_delete = notes_sub.add_parser("delete", help="Delete a note you own")
    notes_delete.add_argument("id", type=int)
    notes_delete.add_argument("--username", default=argparse.SUPPRESS)
    issues = sub.add_parser("issues", help="List, read, update, delete, append, or reopen issues")
    issues.add_argument("--username", default=os.getenv("TEAMAI_USER", ""))
    issues_sub = issues.add_subparsers(dest="issues_command", required=True)
    issues_list = issues_sub.add_parser("list", help="List issues")
    issues_list.add_argument("--query", default="")
    issues_list.add_argument("--status", choices=("triage", "in-progress", "resolved"))
    issues_list.add_argument("--project")
    issues_list.add_argument("--work-type", choices=("算法调研", "算法预研", "模型部署", "组件开发", "项目支撑", "工作日常"))
    issues_list.add_argument("--username", default=argparse.SUPPRESS)
    issues_get = issues_sub.add_parser("get", help="Read an issue by ID")
    issues_get.add_argument("id", type=int)
    issues_get.add_argument("--username", default=argparse.SUPPRESS)
    issues_update = issues_sub.add_parser("update", help="Update an open issue")
    issues_update.add_argument("id", type=int)
    issues_update.add_argument("--title")
    issues_update.add_argument("--content")
    issues_update.add_argument("--project")
    issues_update.add_argument("--work-type", choices=("算法调研", "算法预研", "模型部署", "组件开发", "项目支撑", "工作日常"))
    issues_update.add_argument("--username", default=argparse.SUPPRESS)
    issues_delete = issues_sub.add_parser("delete", help="Delete an issue")
    issues_delete.add_argument("id", type=int)
    issues_delete.add_argument("--username", default=argparse.SUPPRESS)
    issues_append = issues_sub.add_parser("append", help="Append a solution to an issue")
    issues_append.add_argument("id", type=int)
    issues_append.add_argument("--solution", required=True)
    issues_append.add_argument("--username", default=argparse.SUPPRESS)
    issues_reopen = issues_sub.add_parser("reopen", help="Reopen a resolved issue")
    issues_reopen.add_argument("id", type=int)
    issues_reopen.add_argument("--solution", required=True, help="Reason for reopening")
    issues_reopen.add_argument("--username", default=argparse.SUPPRESS)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("id", type=int)
    resolve.add_argument("--solution", required=True)
    resolve.add_argument("--verification", required=True, help="How the fix was tested and the observed result")
    resolve.add_argument("--source", default="teamai-cli")
    resolve.add_argument("--username", default=os.getenv("TEAMAI_USER", ""), help="Name to record as the person who resolved the issue (or set TEAMAI_USER)")
    knowledge = sub.add_parser("knowledge", help="Admin leftover knowledge review queue (resolve auto-publishes by default)")
    knowledge.add_argument("--username", default=os.getenv("TEAMAI_USER", ""))
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", required=True)
    knowledge_candidates = knowledge_sub.add_parser("candidates", help="List leftover review candidates (admin)")
    knowledge_candidates.add_argument("--username", default=argparse.SUPPRESS)
    publish = knowledge_sub.add_parser("publish", help="Manually publish a leftover review candidate (admin)")
    publish.add_argument("id", type=int)
    publish.add_argument("--title", required=True)
    publish.add_argument("--content", required=True)
    publish.add_argument("--reviewer", default="", help="Development-mode audit label; defaults to --username")
    publish.add_argument("--username", default=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.command in ("note", "notes", "search", "issues", "knowledge") and not getattr(args, "username", "").strip():
        raise SystemExit(f"{args.command} commands require --username or TEAMAI_USER")
    if args.command == "resolve" and not args.username.strip():
        if sys.stdin.isatty():
            args.username = input("解决人姓名：").strip()
        if not args.username:
            raise SystemExit("resolve requires --username or TEAMAI_USER so the resolver can be recorded")
    if args.command in ("note", "issue"):
        path = "/v1/notes" if args.command == "note" else "/v1/issues"
        payload = {"content": args.content, "title": args.title, "source": args.source, "tags": args.tag, "project": args.project, "work_type": args.work_type, "tech_stack": args.tech}
        if args.command == "note":
            payload.update(visibility=args.visibility, work_type=args.work_type)
        if args.command == "issue":
            payload.update(environment=args.environment, reproduction=args.reproduction)
        result = request("POST", path, payload, username=args.username if args.command == "note" else getattr(args, "username", None))
        print_view_hint(result)
    elif args.command == "capture":
        content = args.content or sys.stdin.read().strip()
        if not content:
            raise SystemExit("capture requires content or stdin")
        kind = args.type
        if kind == "auto":
            kind = request("POST", "/v1/classify", {"content": content})["kind"]
        if kind == "note" and not args.username.strip():
            raise SystemExit("note capture requires --username or TEAMAI_USER")
        path = {"note": "/v1/notes", "issue": "/v1/issues", "solution": "/v1/solutions"}[kind]
        payload = {"content": content, "title": args.title, "source": args.source, "tags": args.tag, "project": args.project, "work_type": args.work_type, "tech_stack": args.tech, "environment": args.environment, "reproduction": args.reproduction}
        if kind == "note": payload["visibility"] = args.visibility
        result = request("POST", path, payload, username=args.username if kind in {"note", "issue"} else None)
        print_view_hint(result)
    elif args.command == "search":
        result = request("GET", f"/v1/search?q={urllib.parse.quote(args.query)}", username=args.username)
    elif args.command == "notes":
        if args.notes_command == "list":
            params = {"kind": "note", "q": args.query}
            if args.project: params["project"] = args.project
            if args.work_type: params["work_type"] = args.work_type
            if args.visibility: params["visibility"] = args.visibility
            result = request("GET", "/v1/search?" + urllib.parse.urlencode(params), username=args.username)
        elif args.notes_command == "get":
            result = request("GET", f"/v1/notes/{args.id}", username=args.username)
        elif args.notes_command == "update":
            existing = request("GET", f"/v1/notes/{args.id}", username=args.username)
            payload = {
                "expected_updated_at": existing["updated_at"],
                "title": args.title if args.title is not None else existing["title"],
                "content": args.content if args.content is not None else existing["content"],
                "project": args.project if args.project is not None else existing["project"],
                "work_type": args.work_type if args.work_type is not None else existing["work_type"],
                "visibility": args.visibility if args.visibility is not None else existing["visibility"],
                "tags": [tag for tag in existing["tags"] if not tag.startswith(("source::", "type::"))],
                "tech_stack": existing["tech_stack"],
            }
            result = request("PUT", f"/v1/records/{args.id}", payload, username=args.username)
        else:
            result = request("DELETE", f"/v1/notes/{args.id}", username=args.username)
    elif args.command == "issues":
        if args.issues_command == "list":
            params = {"kind": "issue", "q": args.query}
            if args.status: params["status"] = args.status
            if args.project: params["project"] = args.project
            if args.work_type: params["work_type"] = args.work_type
            result = request("GET", "/v1/search?" + urllib.parse.urlencode(params), username=args.username)
        elif args.issues_command == "get":
            result = request("GET", f"/v1/issues/{args.id}", username=args.username)
        elif args.issues_command == "update":
            existing = request("GET", f"/v1/issues/{args.id}", username=args.username)
            payload = {
                "expected_updated_at": existing["updated_at"],
                "title": args.title if args.title is not None else existing["title"],
                "content": args.content if args.content is not None else existing["content"],
                "project": args.project if args.project is not None else existing["project"],
                "work_type": args.work_type if args.work_type is not None else existing["work_type"],
                "tags": [tag for tag in existing["tags"] if not tag.startswith(("source::", "type::"))],
                "tech_stack": existing["tech_stack"],
            }
            result = request("PUT", f"/v1/records/{args.id}", payload, username=args.username)
        elif args.issues_command == "delete":
            result = request("DELETE", f"/v1/issues/{args.id}", username=args.username)
        elif args.issues_command == "append":
            result = request("POST", f"/v1/issues/{args.id}/append-solution", {"solution": args.solution, "source": "teamai-cli"}, username=args.username)
        else:
            result = request("POST", f"/v1/issues/{args.id}/reopen", {"solution": args.solution, "source": "teamai-cli"}, username=args.username)
    elif args.command == "resolve":
        result = request("POST", f"/v1/issues/{args.id}/resolve", {"solution": args.solution, "verification": args.verification, "source": args.source}, username=args.username)
        print_view_hint(result)
    elif args.knowledge_command == "candidates":
        result = request("GET", "/v1/knowledge/candidates", username=args.username)
    else:
        result = request("POST", f"/v1/issues/{args.id}/publish-knowledge", {"title": args.title, "content": args.content, "reviewer": args.reviewer or args.username}, username=args.username)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
