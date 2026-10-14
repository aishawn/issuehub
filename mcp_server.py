from __future__ import annotations

import json
import sys
import urllib.parse
from teamai_transport import request as transport_request


def call(method: str, path: str, payload: dict | None = None):
    return transport_request(method, path, payload)


TOOLS = [
    {"name": "teamai_create_note", "description": "Create a team note.", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}, "title": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content"]}},
    {"name": "teamai_create_issue", "description": "Create a structured team issue.", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}, "title": {"type": "string"}, "source": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content"]}},
    {"name": "teamai_create_solution", "description": "Create a verified solution record.", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}, "title": {"type": "string"}, "source": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content"]}},
    {"name": "teamai_search", "description": "Search notes and issues.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "teamai_get_issue", "description": "Get one record by id.", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "teamai_append_solution", "description": "Append a solution to an issue.", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "solution": {"type": "string"}}, "required": ["id", "solution"]}},
    {"name": "teamai_resolve", "description": "Resolve an issue with a verified solution.", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "solution": {"type": "string"}}, "required": ["id", "solution"]}},
]


def result(value):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]}


def handle(message: dict):
    method = message.get("method")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "teamai-capture", "version": "0.1.0"}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = message["params"]["name"]
        args = message["params"].get("arguments", {})
        if name == "teamai_create_note":
            return result(call("POST", "/v1/notes", {**args, "source": args.get("source", "mcp")}))
        if name == "teamai_create_issue":
            return result(call("POST", "/v1/issues", {**args, "source": args.get("source", "mcp")}))
        if name == "teamai_create_solution":
            return result(call("POST", "/v1/solutions", {**args, "source": args.get("source", "mcp")}))
        if name == "teamai_search":
            return result(call("GET", f"/v1/search?q={urllib.parse.quote(args['query'])}"))
        if name == "teamai_get_issue":
            return result(call("GET", f"/v1/issues/{args['id']}"))
        if name == "teamai_append_solution":
            return result(call("POST", f"/v1/issues/{args['id']}/append-solution", {"solution": args["solution"], "source": "mcp"}))
        if name == "teamai_resolve":
            return result(call("POST", f"/v1/issues/{args['id']}/resolve", {"solution": args["solution"], "source": "mcp"}))
        raise ValueError(f"Unknown tool: {name}")
    return {}


def main() -> None:
    for line in sys.stdin:
        message = {}
        try:
            message = json.loads(line)
            response = handle(message)
            if response is not None and "id" in message:
                print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": response}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32000, "message": str(exc)}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
