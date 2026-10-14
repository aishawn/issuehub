# TeamAI Issue Hub

TeamAI Issue Hub captures team notes, AI coding-session problems, and verified solutions. It runs locally with SQLite and can publish issues to GitLab CE when configured.

## Quick start

Requires Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8787
```

Open http://127.0.0.1:8787.

Copy `.env.example` to `.env` to enable GitLab publishing. Run `python teamai_cli.py --help` for CLI commands, or `python mcp_server.py` as a stdio MCP server.

For a shared deployment, set `TEAMAI_API_KEY` and send it as the `X-TeamAI-Key` header. Put the service behind company OIDC/LDAP or an internal reverse proxy before exposing it to a team network. GitLab failures are retained as local records with `sync_error` for later retry.

CLI and MCP clients use `TEAMAI_API` (default `http://127.0.0.1:8787`) and `TEAMAI_API_KEY`. The API URL must be an HTTP(S) base URL without embedded credentials, query, or fragment; client keys must use visible ASCII characters without spaces.

Use `python teamai_backup.py backup <new-file>` to create a consistent SQLite snapshot and `python teamai_backup.py verify <file>` to validate it. Restore only after stopping the service; the restore command validates the snapshot and keeps an automatic pre-restore backup.

When `TEAMAI_API_KEY` is configured, `POST /v1/sync` requires that key and retries pending GitLab publishes. `GET /health` reports `pending_sync` for operations monitoring.

When GitLab has pending records, a background worker retries up to 10 per pass every `SYNC_INTERVAL_SECONDS` (60 seconds by default). Set it to `0` to disable automatic retries. The Web library also shows a “重试同步” action and reports the retry outcome and remaining queue count; local records stay available if GitLab is unreachable.

Compose sets `APP_ENV=production`: incomplete GitLab settings fail startup, and any configured API key must be at least 32 characters. Request logs include a validated request ID and timing but omit credentials and request bodies.

GitLab publishes use a stable `teamai-sync-<hash>` label. If GitLab creates an issue but the response is lost, the retry looks up that label and links the existing issue instead of posting a second one. A short SQLite lease prevents concurrent sync requests from claiming the same local record.

Issue lifecycle endpoints include `POST /v1/issues/{id}/append-solution`, `/resolve`, and `/reopen`. The Web library supports keyword/type/source/status filters, incremental loading, GitLab sync visibility, and reopening resolved records.

SQLite uses WAL with a 10-second busy timeout and closes request connections. `APP_DB` parent directories are created at startup; `/health` reports sync state without disclosing the database path. If a lifecycle update would duplicate another record, the API returns HTTP 409 and leaves the record unchanged. Common GitHub, GitLab, AWS access-key, bearer, and key/value credentials are redacted before storage.
