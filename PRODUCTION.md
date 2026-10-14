# Production checklist

## Quick start for a controlled LAN test

This path is for a small internal test on a trusted private network. Docker Compose is required, and the host firewall must allow TCP/8787 only from the tester subnet. The Compose default remains loopback-only.

```powershell
Copy-Item .env.example .env
# Edit .env: set TEAMAI_BIND_ADDRESS=0.0.0.0
# Keep TEAMAI_API_KEY empty only when the firewall restricts access to the tester subnet.
# Leave all three GitLab values empty, or configure GITLAB_URL, GITLAB_TOKEN,
# and GITLAB_PROJECT_ID together.
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 teamai
Invoke-RestMethod http://127.0.0.1:8787/health
```

Open `http://<test-host-LAN-IP>:8787/` from a tester machine. Confirm creating, searching, resolving, reopening, and restarting the service without losing records. If GitLab is configured, also verify one successful publish and one retry after temporarily making GitLab unavailable. Do not expose this direct HTTP test endpoint to an untrusted network; the browser UI does not send `TEAMAI_API_KEY` itself, so use an authenticated reverse proxy to test with API-key enforcement.

Create and verify a backup in the mounted backup volume:

```powershell
$backup = "/backups/teamai-$((Get-Date).ToString('yyyyMMdd-HHmmss')).db"
docker compose exec teamai python teamai_backup.py backup $backup
docker compose exec teamai python teamai_backup.py verify $backup
```

`docker compose down` preserves the named data and backup volumes. Do not use `docker compose down -v` for a test environment whose records or backups must be retained.

## Required before team access

- Set a long random `TEAMAI_API_KEY` or put the service behind the company OIDC/LDAP reverse proxy.
- The browser UI does not store an API key. For shared UI access, either have a trusted OIDC proxy inject the service key after user authentication, or leave the app key unset and make the proxy/network the only route to the service. Never expose the unauthenticated upstream directly.
- Use HTTPS at the reverse proxy and restrict the service to the internal network.
- Set `APP_DB` to a persistent volume. Back up the SQLite file while the service is stopped or with a consistent snapshot.
- Use `teamai_backup.py` for SQLite online backups and integrity checks; periodically copy `/backups` off the Docker host and test a restore.
- Backups contain issue content and potentially sensitive context; encrypt the off-host destination, restrict access, and define retention/restore objectives.
- Interim retention policy: keep records and backups indefinitely. TeamAI has no record deletion/purge API or automatic cleanup task; revisit this policy if compliance or operational requirements change, and include active databases, local safety copies, and off-host backups in any future erasure plan.
- Configure `GITLAB_URL`, `GITLAB_TOKEN`, and `GITLAB_PROJECT_ID` if GitLab is the system of record.
- Monitor `/health`; alert when `pending_sync` is non-zero for longer than the retry window.
- GitLab failures retry automatically every 60 seconds by default, up to 10 records per pass. Set `SYNC_INTERVAL_SECONDS=0` to disable the worker; `/v1/sync` and the Web retry button remain available.
- Rotate GitLab and TeamAI credentials according to company policy.
- Container runs with `APP_ENV=production`: partial GitLab configuration fails startup, and a configured `TEAMAI_API_KEY` must contain at least 32 characters. Leave the key unset only when access control is enforced by a trusted proxy/network.
- Requests log request ID, method, path, status, and duration without logging credentials or request bodies; unexpected errors return a generic response with the correlation ID.
- The container runs as UID 10001, uses a read-only root filesystem, and binds the published port to host loopback by default. Keep the proxy on the same host/network or change the binding deliberately.

## Verification commands

```powershell
pip install -r requirements-dev.txt
pytest -q
python -m py_compile app\main.py teamai_cli.py mcp_server.py teamai_backup.py teamai_transport.py
python -m uvicorn app.main:app --host 0.0.0.0 --port 8787
```

## Browser smoke test

For a release candidate or deployment change, run the optional Chromium smoke test. It starts a local TeamAI process against a temporary SQLite database, exercises the create/resolve/reopen and keyboard-focus flows, checks desktop/mobile overflow, and stops the process afterward. It never reads or modifies the configured production database.

```powershell
pip install -r requirements-e2e.txt
python -m playwright install chromium
python scripts\browser_smoke.py
```

To keep desktop and mobile screenshots for review, add `--screenshots-dir <directory>`. Playwright browser binaries are version-specific; after changing the pinned Playwright version, install its matching Chromium build again.

Docker deployment is provided in `Dockerfile` and `docker-compose.yml`. Docker was not available in the development environment, so image build, Compose validation, named-volume ownership, and startup still need to be verified in CI or on the deployment host.

## Backup and restore

Local backup and verification:

```powershell
python teamai_backup.py backup .\backups\teamai-2026-10-14.db
python teamai_backup.py verify .\backups\teamai-2026-10-14.db
```

The backup command uses SQLite's online backup API and refuses to overwrite an existing file. For restore, stop the API first. Restore validates the source, checkpoints the current WAL, writes a safety copy when a database already exists, and atomically installs the snapshot (including on a fresh host):

```powershell
python teamai_backup.py restore .\backups\teamai-2026-10-14.db --yes
```

In Compose, backups go to the separate `teamai-backups` volume mounted at `/backups`. Copy that volume to off-host storage on a schedule. To restore, stop the service, run the one-off restore container, then start the service:

```powershell
docker compose stop teamai
docker compose run --rm --no-deps teamai python teamai_backup.py restore /backups/teamai-2026-10-14.db --yes
docker compose start teamai
```

Restore has a data replacement step; run it only during a planned maintenance window after confirming the backup file and stopping all application writers.

## Known scale boundary

SQLite is appropriate for a small internal team and low write volume. Move metadata to PostgreSQL before sustained concurrent writes, high availability, or multiple application replicas are required. GitLab remains the issue system of record when configured.
