# TeamAI Issue Hub

TeamAI Issue Hub is the issue and experience capability in the TeamAI team-context tool. The AI capture Skill + MCP are the primary low-friction entry point; the Web UI supports review and curation, and this repository's `teamai-cli` client is optional. Resolved issues can be curated into separate, searchable team-knowledge articles linked to their source issue. Compose production deployments use PostgreSQL; local development may use SQLite. GitLab CE remains an optional destination for source issues.

## Quick start

For the configured Windows PostgreSQL development instance and management UI, see [LOCAL_DEV.md](LOCAL_DEV.md).

Requires Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8787
```

Open http://127.0.0.1:8787.

Set `DATABASE_URL` to a PostgreSQL connection URL to use PostgreSQL instead of SQLite, for example `postgresql://teamai:password@localhost:5432/teamai`. URL-encode reserved characters in usernames and passwords. TeamAI creates or upgrades its tables at startup. Other services should use the TeamAI HTTP API URL rather than connecting directly to the database.

Copy `.env.example` to `.env` to enable GitLab publishing. Install the Skill at `skills/teamai-capture/SKILL.md` in the AI client's Skill location and configure `python mcp_server.py` as a stdio MCP server. The Skill searches existing context before capture and never publishes a knowledge article without explicit human approval. The optional command line client is available via `python teamai_cli.py --help`.

For a shared deployment, set `TEAMAI_API_KEY` and send it as the `X-TeamAI-Key` header. Put the service behind company OIDC/LDAP or an internal reverse proxy before exposing it to a team network. GitLab failures are retained as local records with `sync_error` for later retry.

Docker Compose runs TeamAI with PostgreSQL. Set `POSTGRES_PASSWORD` in `.env` before `docker compose up -d`; `POSTGRES_DB` and `POSTGRES_USER` default to `teamai`. PostgreSQL stays on the private Compose network; TeamAI's HTTP API is exposed on port 8787.

Compose also runs a private MinIO object store for attachments. Set `MINIO_ROOT_PASSWORD` in `.env` before startup. Files are stored in the `teamai-objects` volume; PostgreSQL stores attachment metadata and record links. Uploads are limited to 20 MiB each and 30 attachments per record. Attachments are downloaded through the authenticated TeamAI API; MinIO is not exposed on the host. Back up the object volume along with PostgreSQL, and restore both from a consistent maintenance point.

To copy records from an existing SQLite database, stop TeamAI and run `python teamai_migrate.py <sqlite-file> --database-url <postgresql-url>`. The script opens SQLite read-only, checks its integrity, preserves Issue/knowledge relationships where their target rows map unambiguously, and reports same-ID, content-hash, and unresolved relationship conflicts. It leaves SQLite intact. The SQLite backup helper does not back up PostgreSQL; use `pg_dump` for PostgreSQL backups.

CLI and MCP clients use `TEAMAI_API` (default `http://127.0.0.1:8787`) and `TEAMAI_API_KEY`. The API URL must be an HTTP(S) base URL without embedded credentials, query, or fragment; client keys must use visible ASCII characters without spaces. For a LAN Issue Hub, point clients at the shared host (for example `TEAMAI_API=http://10.110.66.11:8787`). After `note` / `issue` / `capture` / `resolve`, the CLI keeps JSON on stdout and prints a Chinese view hint on stderr with the Hub home and a `#record=<id>` deep link. If the Web UI is served from a different base URL than the API, set optional `TEAMAI_WEB_URL` for those links.

The CLI supports username-scoped note CRUD. Set `TEAMAI_USER` once or pass `--username` on a command: `python teamai_cli.py note "内容" --project demo --work-type 工作日常 --visibility private`, `python teamai_cli.py notes list --project demo --work-type 工作日常`, `python teamai_cli.py notes get 12`, `python teamai_cli.py notes update 12 --title "新标题"`, and `python teamai_cli.py notes delete 12`. Add `--username alice` to `notes` commands when no `TEAMAI_USER` is set. Listing and reading include team-shared notes plus personal notes owned by that username; only the owner can edit, delete, or add attachments to a note. Web and API use the same ownership rule. Issue resolution also records the self-reported resolver name: Web uses the selected username, while CLI accepts `TEAMAI_USER` or `teamai_cli.py resolve <id> --username alice --solution ... --verification ...`.

Use `python teamai_backup.py backup <new-file>` to create a consistent SQLite snapshot for local development. Compose production uses PostgreSQL: create host-readable, verified archives with `python teamai_postgres_backup.py`; restore with `python teamai_postgres_restore.py <archive> --yes` during a maintenance window as documented in [PRODUCTION.md](PRODUCTION.md).

When `TEAMAI_API_KEY` is configured, `POST /v1/sync` requires that key and retries pending GitLab publishes. `GET /health` reports `pending_sync` for operations monitoring.

When GitLab has pending records, a background worker retries up to 10 per pass every `SYNC_INTERVAL_SECONDS` (60 seconds by default). Set it to `0` to disable automatic retries. The Web library also shows a “重试同步” action and reports the retry outcome and remaining queue count; local records stay available if GitLab is unreachable.

Compose sets `APP_ENV=production`: incomplete GitLab settings fail startup, and any configured API key must be at least 32 characters. Request logs include a validated request ID and timing but omit credentials and request bodies.

GitLab publishes use a stable `teamai-sync-<hash>` label. If GitLab creates an issue but the response is lost, the retry looks up that label and links the existing issue instead of posting a second one. A short SQLite lease prevents concurrent sync requests from claiming the same local record.

Issue lifecycle endpoints include `POST /v1/issues/{id}/append-solution`, `/resolve`, and `/reopen`. `PUT /v1/records/{id}` edits note, solution, and open issue content/metadata; resolved issues must be reopened first, while published knowledge is changed only through source issue review. Resolving requires verification evidence and **auto-publishes** a `knowledge` record linked by `parent_issue_id` (no manual review by default). Admins listed in `TEAMAI_ADMIN_USERS` can still open leftover review candidates, retry GitLab sync, and view user activity. Re-opening a source issue hides its article from search until the issue is resolved again (auto-republish). The Web UI supports keyword/type/status filters, knowledge browsing, incremental loading, and admin-only sync/user pages.

Notes can be marked `shared` or `private` (`visibility` in the record payload); old records default to `shared`. The Web UI asks for a username and remembers it in that browser. API/MCP/CLI clients pass the same context as `X-TeamAI-User` (the CLI also accepts `--username` or `TEAMAI_USER`). Private notes and their attachments are filtered from other username contexts and are excluded from GitLab sync and team knowledge review. Username-only mode does not authenticate identity: anyone can claim another username, so “private” is a convenience view, not secure storage. Do not put sensitive content there. Issues and published team knowledge remain shared.

In development, the reviewer's submitted name is an audit label. Production startup requires configured trusted-proxy identity and group headers, and publishing checks the authenticated reviewer against the required approver group. Configure the proxy to overwrite these headers and keep the API unreachable except through that proxy. See [ARCHITECTURE.html](ARCHITECTURE.html) for the product boundary and data flow.

SQLite uses WAL with a 10-second busy timeout and closes request connections. `APP_DB` parent directories are created at startup; `/health` reports sync state without disclosing the database path. If a lifecycle update would duplicate another record, the API returns HTTP 409 and leaves the record unchanged. Common GitHub, GitLab, AWS access-key, bearer, and key/value credentials are redacted before storage.
