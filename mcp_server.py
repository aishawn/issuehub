from __future__ import annotations

import json
import sys
import urllib.parse
from teamai_transport import TeamAIRequestError, request as transport_request


def call(method: str, path: str, payload: dict | None = None, username: str | None = None):
    return transport_request(method, path, payload, username=username)


def call_for_user(method: str, path: str, payload: dict | None = None, username: str | None = None):
    if username:
        return call(method, path, payload, username=username)
    if payload is None:
        return call(method, path)
    return call(method, path, payload)


TOOLS = [
    {"name": "teamai_create_note", "description": "Create a team note. Provide username and visibility (shared or private); username-only personal visibility is weak and not secure.", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}, "title": {"type": "string"}, "username": {"type": "string"}, "visibility": {"type": "string", "enum": ["shared", "private"]}, "project": {"type": "string"}, "work_type": {"type": "string", "enum": ["算法调研", "算法预研", "模型部署", "组件开发", "项目支撑", "工作日常"]}, "tech_stack": {"type": "array", "items": {"type": "string"}}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content", "username", "project", "work_type", "visibility"]}},
    {"name": "teamai_create_issue", "description": "Create a structured team issue.", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}, "title": {"type": "string"}, "source": {"type": "string"}, "project": {"type": "string"}, "tech_stack": {"type": "array", "items": {"type": "string"}}, "environment": {"type": "string"}, "reproduction": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content"]}},
    {"name": "teamai_create_solution", "description": "Capture a solution note. This alone does not assert verification or publish shared knowledge; link verified fixes to an issue and use teamai_resolve with evidence.", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}, "title": {"type": "string"}, "source": {"type": "string"}, "project": {"type": "string"}, "tech_stack": {"type": "array", "items": {"type": "string"}}, "environment": {"type": "string"}, "reproduction": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content"]}},
    {"name": "teamai_search", "description": "Search published team knowledge, notes, and issues. Private notes require the user's username context. Search before creating a new record.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "username": {"type": "string"}, "kind": {"type": "string", "enum": ["knowledge", "issue", "note", "solution"]}, "status": {"type": "string", "enum": ["triage", "in-progress", "resolved"]}, "source": {"type": "string"}, "knowledge_status": {"type": "string", "enum": ["pending_review", "needs_review", "review"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "offset": {"type": "integer", "minimum": 0}}, "required": ["query"]}},
    {"name": "teamai_get_issue", "description": "Get one record by id; provide username to access owned personal notes.", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "username": {"type": "string"}}, "required": ["id"]}},
    {"name": "teamai_list_attachments", "description": "List files attached to a TeamAI record; provide username for personal notes.", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "username": {"type": "string"}}, "required": ["id"]}},
    {"name": "teamai_append_solution", "description": "Append a solution to an issue.", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "solution": {"type": "string"}}, "required": ["id", "solution"]}},
    {"name": "teamai_resolve", "description": "Resolve an issue only after the fix is verified. Include the fix, test evidence, and the resolver's display name (self-reported, not authenticated).", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "solution": {"type": "string"}, "verification": {"type": "string"}, "username": {"type": "string", "description": "Name to record as the resolver; this is self-reported and not authenticated."}}, "required": ["id", "solution", "verification", "username"]}},
    {"name": "teamai_knowledge_candidates", "description": "List resolved issues awaiting human review for promotion into team knowledge.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "teamai_publish_knowledge", "description": "Publish a curated knowledge article linked to a resolved issue. Call only after explicit human approval. In production, the API requires an authenticated proxy identity in the approver group.", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "title": {"type": "string"}, "content": {"type": "string"}, "reviewer": {"type": "string", "description": "Development-mode audit label; production derives identity from the trusted proxy."}}, "required": ["id", "title", "content"]}},
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
            username = args.get("username", "")
            payload = {key: value for key, value in args.items() if key != "username"}
            return result(call_for_user("POST", "/v1/notes", {**payload, "source": args.get("source", "mcp")}, username=username))
        if name == "teamai_create_issue":
            return result(call("POST", "/v1/issues", {**args, "source": args.get("source", "mcp")}))
        if name == "teamai_create_solution":
            return result(call("POST", "/v1/solutions", {**args, "source": args.get("source", "mcp")}))
        if name == "teamai_search":
            params = {"q": args["query"]}
            for key in ("kind", "status", "source", "knowledge_status"):
                if args.get(key):
                    params[key] = args[key]
            if "limit" in args:
                if isinstance(args["limit"], bool) or not isinstance(args["limit"], int) or not 1 <= args["limit"] <= 200:
                    raise ValueError("limit must be an integer between 1 and 200")
                params["limit"] = args["limit"]
            if "offset" in args:
                if isinstance(args["offset"], bool) or not isinstance(args["offset"], int) or args["offset"] < 0:
                    raise ValueError("offset must be a non-negative integer")
                params["offset"] = args["offset"]
            return result(call_for_user("GET", f"/v1/search?{urllib.parse.urlencode(params)}", username=args.get("username")))
        if name == "teamai_get_issue":
            return result(call_for_user("GET", f"/v1/issues/{args['id']}", username=args.get("username")))
        if name == "teamai_list_attachments":
            return result(call_for_user("GET", f"/v1/issues/{args['id']}", username=args.get("username")))
        if name == "teamai_append_solution":
            return result(call("POST", f"/v1/issues/{args['id']}/append-solution", {"solution": args["solution"], "source": "mcp"}))
        if name == "teamai_resolve":
            return result(call_for_user("POST", f"/v1/issues/{args['id']}/resolve", {"solution": args["solution"], "verification": args["verification"], "source": "mcp"}, username=args["username"]))
        if name == "teamai_knowledge_candidates":
            return result(call("GET", "/v1/knowledge/candidates"))
        if name == "teamai_publish_knowledge":
            return result(call("POST", f"/v1/issues/{args['id']}/publish-knowledge", {"title": args["title"], "content": args["content"], "reviewer": args.get("reviewer", "")}))
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
            safe_message = str(exc) if isinstance(exc, TeamAIRequestError) else "TeamAI MCP request failed"
            print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32000, "message": safe_message}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
