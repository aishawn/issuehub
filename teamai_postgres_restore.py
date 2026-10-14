"""Verify and restore a PostgreSQL custom-format archive through Docker Compose."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESTORE = (
    'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --no-password --clean --if-exists '
    '--exit-on-error --single-transaction '
    '--no-owner --no-privileges -h 127.0.0.1 -U "$POSTGRES_USER" '
    '-d "$POSTGRES_DB"'
)


def restore(source: Path, *, run=None, popen=None) -> None:
    run = run or subprocess.run
    popen = popen or subprocess.Popen
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Backup archive not found: {source}")
    if source.stat().st_size == 0:
        raise RuntimeError("Backup archive is empty; target database was not changed")

    # Avoid restoring while the application can continue writing. The explicit
    # stop/start steps in the runbook still apply to other database clients.
    stopped = run(
        ["docker", "compose", "ps", "--status", "running", "--services"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if stopped.returncode != 0:
        raise RuntimeError("Could not verify Compose service state; stop TeamAI before restoring")
    if "teamai" in (stopped.stdout or "").splitlines():
        raise RuntimeError("TeamAI service is running; stop it before restoring the database")

    with source.open("rb") as archive:
        checked = run(
            ["docker", "compose", "exec", "-T", "postgres", "pg_restore", "--list"],
            cwd=ROOT,
            stdin=archive,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    if checked.returncode != 0:
        raise RuntimeError("PostgreSQL rejected the backup archive; target database was not changed")

    with source.open("rb") as archive:
        process = popen(
            ["docker", "compose", "exec", "-T", "postgres", "sh", "-ec", RESTORE],
            cwd=ROOT,
            stdin=archive,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _stdout, _stderr = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"PostgreSQL restore failed (exit code {process.returncode})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore PostgreSQL from a TeamAI custom-format archive.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--yes", action="store_true", help="confirm that the target database will be replaced")
    args = parser.parse_args()
    if not args.yes:
        parser.error("restore replaces database objects; stop TeamAI and pass --yes after creating a fresh backup")
    try:
        restore(args.archive)
        print("PostgreSQL restore completed. Start TeamAI and verify /health and representative records.")
        return 0
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(f"PostgreSQL restore failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
