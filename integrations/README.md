# Client integration

1. Start the API with `python -m uvicorn app.main:app --host 127.0.0.1 --port 8787`.
2. Copy `mcp-config.example.json` into the MCP configuration used by Claude Code or Qwen Code.
3. Replace the example path with the absolute path to `mcp_server.py`.
4. Install or link `skills/teamai-capture/SKILL.md` in the client skill directory.

For a remote deployment, change `TEAMAI_API` to the internal HTTPS URL and put authentication in front of the API before exposing it beyond localhost.
