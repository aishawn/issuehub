# Production checklist

## Quick start for a controlled LAN test

This path is for a small internal test on a trusted private network. Docker Compose is required, and the host firewall must allow TCP/8787 only from the tester subnet. The Compose default remains loopback-only.

```powershell
Copy-Item .env.example .env
# Edit .env: set POSTGRES_PASSWORD to a strong raw password; URL-encode that password only inside DATABASE_URL; set TEAMAI_BIND_ADDRESS=0.0.0.0
# Keep TEAMAI_API_KEY empty only when the firewall restricts access to the tester subnet.
# Set reviewer identity/group headers to values overwritten by the SSO proxy before granting review access.
# Leave all three GitLab values empty, or configure GITLAB_URL, GITLAB_TOKEN,
# and GITLAB_PROJECT_ID together.
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 teamai
Invoke-RestMethod http://127.0.0.1:8787/health
```

Open `http://<test-host-LAN-IP>:8787/` from a tester machine. Confirm creating, searching, editing a note/open issue, resolving, reopening, and restarting the service without losing records. Resolved issues must be reopened before editing so verification status cannot be bypassed; published knowledge is edited through source issue review. If GitLab is configured, also verify one successful publish and one retry after temporarily making GitLab unavailable. Do not expose this direct HTTP test endpoint to an untrusted network; the browser UI does not send `TEAMAI_API_KEY` itself, so use an authenticated reverse proxy to test with API-key enforcement.

Create and verify a PostgreSQL archive on the deployment host:

```powershell
python teamai_postgres_backup.py
```

The command prints the path of a custom-format archive under `backups/`. It streams data from the PostgreSQL container as the default `postgres` user, verifies the archive with `pg_restore --list`, writes a temporary file with restrictive permissions, atomically publishes it, and refuses to overwrite an existing file. The host backup directory is outside Docker volumes and survives `docker compose down`; `docker compose down -v` still deletes the PostgreSQL data volume. Set host filesystem permissions and encryption according to your deployment platform.

## Required before team access

- Set a long random `TEAMAI_API_KEY` or put the service behind the company OIDC/LDAP reverse proxy.
- Production startup requires a PostgreSQL `DATABASE_URL`; SQLite is accepted only for development and local migration checks.
- PostgreSQL schema DDL and one-time data repair run as a transactionally recorded migration under a transaction-scoped advisory lock. Once applied, the API does not perform implicit schema changes on every start; an application older than the database schema fails fast.
- The browser UI does not store an API key. For shared UI access, either have a trusted OIDC proxy inject the service key after user authentication, or leave the app key unset and make the proxy/network the only route to the service. Never expose the unauthenticated upstream directly.
- Set `TEAMAI_REVIEWER_ID_HEADER`, `TEAMAI_REVIEWER_GROUPS_HEADER`, and `TEAMAI_REVIEWER_REQUIRED_GROUP` to the identity and group claim headers supplied by the company identity proxy. Production startup refuses to run without this review contract.
- Configure the proxy to remove any client-supplied copies of those headers and replace them with authenticated identity/group claims. Restrict the upstream port so clients cannot bypass the proxy; the API trusts these headers only because of that network boundary.
- Use HTTPS at the reverse proxy and restrict the service to the internal network.
- Username-only personal views do not authenticate users and are not a confidentiality boundary. Do not store sensitive content in personal notes until a verified identity provider is integrated.
- PostgreSQL stores live data in the `teamai-postgres` volume. Verified backup files are written to the host's `backups/` directory by `teamai_postgres_backup.py`.
- MinIO stores attachment objects in the `teamai-objects` volume. Keep MinIO private; TeamAI proxies attachment downloads after API authentication. Back up and restore the object volume together with PostgreSQL during a maintenance window. MinIO root credentials must be unique and protected like the database credentials.
- `DATABASE_URL` must use URL-encoded credentials. Its username/password/database must match `POSTGRES_USER`, the URL-decoded `POSTGRES_PASSWORD`, and `POSTGRES_DB`; Compose deliberately does not synthesize a URL by interpolating arbitrary password characters. For example, `POSTGRES_PASSWORD=p@ss:word` pairs with `DATABASE_URL=postgresql://teamai:p%40ss%3Aword@postgres:5432/teamai`.
- Schedule `python teamai_postgres_backup.py` on the Docker host (for example, with cron or Windows Task Scheduler), copy the resulting `backups/*.dump` files to encrypted off-host storage, and monitor free disk space. Backups are never automatically pruned because the current retention policy is indefinite.
- Periodically restore an archive in an isolated PostgreSQL environment using `teamai_postgres_restore.py`; a successful `pg_restore --list` checks readability but is not a substitute for a restore drill.
- Backups contain issue content and potentially sensitive context; encrypt the off-host destination, restrict access, and define retention/restore objectives.
- Interim retention policy: keep records and backups indefinitely. TeamAI has no record deletion/purge API or automatic cleanup task; revisit this policy if compliance or operational requirements change, and include active databases, local safety copies, and off-host backups in any future erasure plan.
- Configure `GITLAB_URL`, `GITLAB_TOKEN`, and `GITLAB_PROJECT_ID` if GitLab is the system of record.
- Monitor `/health`; alert when `pending_sync` is non-zero for longer than the retry window.
- GitLab failures retry automatically every 60 seconds by default, up to 10 records per pass. Set `SYNC_INTERVAL_SECONDS=0` to disable the worker; `/v1/sync` and the Web retry button remain available.
- Rotate GitLab and TeamAI credentials according to company policy.
- In development, the reviewer name is a user-supplied audit label. In production, the publish endpoint ignores that label and uses the trusted proxy identity only after checking the configured approver group.
- TeamAI knowledge articles are stored separately from source issues in PostgreSQL. GitLab synchronization applies only to source issues. Reopening a source issue withdraws its article from normal knowledge search until a reviewer re-approves the updated article.
- Container runs with `APP_ENV=production`: partial GitLab configuration fails startup, and a configured `TEAMAI_API_KEY` must contain at least 32 characters. Leave the key unset only when access control is enforced by a trusted proxy/network.
- Requests log request ID, method, path, status, and duration without logging credentials or request bodies; unexpected errors return a generic response with the correlation ID.
- The container runs as UID 10001, uses a read-only root filesystem, and binds the published port to host loopback by default. Keep the proxy on the same host/network or change the binding deliberately.

## Verification commands

```powershell
pip install -r requirements-dev.txt
pytest -q
python -m py_compile app\main.py teamai_cli.py mcp_server.py teamai_backup.py teamai_migrate.py teamai_transport.py teamai_postgres_backup.py teamai_postgres_restore.py
python -m uvicorn app.main:app --host 0.0.0.0 --port 8787
```

## Browser smoke test

For a release candidate or deployment change, run the optional Chromium smoke test. It starts a local TeamAI process against a temporary SQLite database, exercises capture, verified resolve, knowledge review/publication, source linking, reopen and keyboard focus, checks desktop/mobile overflow, and stops the process afterward. It never reads or modifies the configured production database.

```powershell
pip install -r requirements-e2e.txt
python -m playwright install chromium
python scripts\browser_smoke.py
```

To keep desktop and mobile screenshots for review, add `--screenshots-dir <directory>`. Playwright browser binaries are version-specific; after changing the pinned Playwright version, install its matching Chromium build again.

Docker deployment is provided in `Dockerfile` and `docker-compose.yml`. Docker was not available in the development environment, so image build, Compose startup, and PostgreSQL runtime integration still need to be verified in CI or on the deployment host.

## Local SQLite backup and restore

Local backup and verification:

```powershell
python teamai_backup.py backup .\backups\teamai-2026-10-14.db
python teamai_backup.py verify .\backups\teamai-2026-10-14.db
```

The backup command uses SQLite's online backup API and refuses to overwrite an existing file. For restore, stop the API first. Restore validates the source, checkpoints the current WAL, writes a safety copy when a database already exists, and atomically installs the snapshot (including on a fresh host):

```powershell
python teamai_backup.py restore .\backups\teamai-2026-10-14.db --yes
```

These `teamai_backup.py` commands are for local SQLite development. Compose production uses PostgreSQL:

```powershell
python teamai_postgres_backup.py
```

Before restoring, create a fresh backup and stop TeamAI. The restore script independently checks that the Compose `teamai` service is stopped, rejects empty/invalid archives, applies the restore in one transaction, and requires `--yes`:

```powershell
docker compose stop teamai
python teamai_postgres_restore.py .\backups\teamai-<timestamp>.dump --yes
docker compose start teamai
```

PostgreSQL restore replaces objects in the target database; run it during a planned maintenance window after confirming the archive and stopping all application writers. Prefer validating the archive in an isolated database before production recovery.

## Known scale boundary

Compose production uses PostgreSQL. SQLite remains available for local development and one-time migration via `teamai_migrate.py`. GitLab remains the external collaboration system for source issues when configured.
