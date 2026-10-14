# Local development and UI review

The current Windows development instance uses a dedicated PostgreSQL 18 cluster and database. It is separate from other PostgreSQL services on the machine and listens on loopback only.

- UI/API: <http://127.0.0.1:8787/>
- Health: <http://127.0.0.1:8787/health>
- PostgreSQL: `127.0.0.1:55432`, database `issuehub_dev`, role `teamai_dev`
- Cluster data: `%LOCALAPPDATA%\TeamAI\IssueHub-Postgres-Dev`
- Existing local SQLite sample data was copied to PostgreSQL; the `teamai.db` source remains unchanged.

## Start PostgreSQL

Run in PowerShell if the dedicated cluster is stopped:

```powershell
$pgData = Join-Path $env:LOCALAPPDATA 'TeamAI\IssueHub-Postgres-Dev'
$pgLog = Join-Path (Split-Path $pgData) 'issuehub-postgres-dev.log'
& 'C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe' -D $pgData -l $pgLog -o '-h 127.0.0.1 -p 55432' start
```

The cluster was initialized with trust authentication for local development. Keep it bound to `127.0.0.1`; do not expose this development database to the LAN.

## Start the API

From the repository root in another PowerShell window:

```powershell
$env:DATABASE_URL = 'postgresql://teamai_dev@127.0.0.1:55432/issuehub_dev'
$env:APP_ENV = 'development'
$env:SYNC_INTERVAL_SECONDS = '0'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
```

Open <http://127.0.0.1:8787/>. The management page supports search/filter, detail view, editing notes/solutions/open issues, issue lifecycle actions, and the knowledge review flow. Editing a resolved issue requires reopening it first. Published knowledge is edited through the source issue review flow.

Stop the API with `Ctrl+C`. Stop the dedicated database when finished:

```powershell
$pgData = Join-Path $env:LOCALAPPDATA 'TeamAI\IssueHub-Postgres-Dev'
& 'C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe' -D $pgData stop
```

## Test against PostgreSQL

```powershell
$env:TEST_DATABASE_URL = 'postgresql://teamai_dev@127.0.0.1:55432/issuehub_dev'
python -m pytest -q
```

The PostgreSQL integration tests create isolated schemas and remove them after each test. The application data in the `public` schema remains available to the UI.
