from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from teamai_transport import TeamAIRequestError, request as transport_request


def request(method: str, path: str, payload: dict | None = None):
    try:
        return transport_request(method, path, payload)
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
    capture = sub.add_parser("capture")
    capture.add_argument("content", nargs="?")
    capture.add_argument("--type", choices=("auto", "note", "issue"), default="auto")
    capture.add_argument("--title")
    capture.add_argument("--source", default="teamai-cli")
    capture.add_argument("--tag", action="append", default=[])
    search = sub.add_parser("search")
    search.add_argument("query")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("id", type=int)
    resolve.add_argument("--solution", required=True)
    resolve.add_argument("--source", default="teamai-cli")
    args = parser.parse_args()

    if args.command in ("note", "issue"):
        path = "/v1/notes" if args.command == "note" else "/v1/issues"
        result = request("POST", path, {"content": args.content, "title": args.title, "source": args.source, "tags": args.tag})
    elif args.command == "capture":
        content = args.content or sys.stdin.read().strip()
        if not content:
            raise SystemExit("capture requires content or stdin")
        kind = args.type
        if kind == "auto":
            kind = request("POST", "/v1/classify", {"content": content})["kind"]
        path = {"note": "/v1/notes", "issue": "/v1/issues", "solution": "/v1/solutions"}[kind]
        result = request("POST", path, {"content": content, "title": args.title, "source": args.source, "tags": args.tag})
    elif args.command == "search":
        result = request("GET", f"/v1/search?q={urllib.parse.quote(args.query)}")
    else:
        result = request("POST", f"/v1/issues/{args.id}/resolve", {"solution": args.solution, "source": args.source})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
