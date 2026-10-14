from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from teamai_transport import TeamAIRequestError, request as transport_request


def request(method: str, path: str, payload: dict | None = None, username: str | None = None):
    try:
        return transport_request(method, path, payload, username=username)
    except TeamAIRequestError as exc:
        raise SystemExit(str(exc)) from None


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
    capture.add_argument("--tech", action="append", default=[])
    capture.add_argument("--environment", default="")
    capture.add_argument("--reproduction", default="")
    search = sub.add_parser("search")
    search.add_argument("query")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("id", type=int)
    resolve.add_argument("--solution", required=True)
    resolve.add_argument("--verification", required=True, help="How the fix was tested and the observed result")
    resolve.add_argument("--source", default="teamai-cli")
    knowledge = sub.add_parser("knowledge", help="Review and publish shared team knowledge")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", required=True)
    knowledge_sub.add_parser("candidates", help="List resolved issues awaiting knowledge review")
    publish = knowledge_sub.add_parser("publish", help="Publish a reviewed article linked to a resolved issue")
    publish.add_argument("id", type=int)
    publish.add_argument("--title", required=True)
    publish.add_argument("--content", required=True)
    publish.add_argument("--reviewer", required=True, help="Reviewer audit label (not authenticated in shared-key mode)")
    args = parser.parse_args()

    if args.command in ("note", "issue"):
        path = "/v1/notes" if args.command == "note" else "/v1/issues"
        payload = {"content": args.content, "title": args.title, "source": args.source, "tags": args.tag, "project": args.project, "tech_stack": args.tech}
        if args.command == "note":
            payload["visibility"] = args.visibility
        if args.command == "issue":
            payload.update(environment=args.environment, reproduction=args.reproduction)
        result = request("POST", path, payload, username=args.username if args.command == "note" else None)
    elif args.command == "capture":
        content = args.content or sys.stdin.read().strip()
        if not content:
            raise SystemExit("capture requires content or stdin")
        kind = args.type
        if kind == "auto":
            kind = request("POST", "/v1/classify", {"content": content})["kind"]
        path = {"note": "/v1/notes", "issue": "/v1/issues", "solution": "/v1/solutions"}[kind]
        payload = {"content": content, "title": args.title, "source": args.source, "tags": args.tag, "project": args.project, "tech_stack": args.tech, "environment": args.environment, "reproduction": args.reproduction}
        if kind == "note": payload["visibility"] = args.visibility
        result = request("POST", path, payload, username=args.username if kind == "note" else None)
    elif args.command == "search":
        result = request("GET", f"/v1/search?q={urllib.parse.quote(args.query)}")
    elif args.command == "resolve":
        result = request("POST", f"/v1/issues/{args.id}/resolve", {"solution": args.solution, "verification": args.verification, "source": args.source})
    elif args.knowledge_command == "candidates":
        result = request("GET", "/v1/knowledge/candidates")
    else:
        result = request("POST", f"/v1/issues/{args.id}/publish-knowledge", {"title": args.title, "content": args.content, "reviewer": args.reviewer})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
