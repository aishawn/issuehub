"""Create and verify a PostgreSQL custom-format backup through Docker Compose."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PG_DUMP = (
    'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump --no-password '
    '-h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'
)


def backup(destination: Path, *, popen=None, run=None) -> Path:
    popen = popen or subprocess.Popen
    run = run or subprocess.run
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite backup: {destination}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as archive:
            process = popen(
                ["docker", "compose", "exec", "-T", "postgres", "sh", "-ec", PG_DUMP],
                cwd=ROOT,
                stdout=archive,
                stderr=subprocess.PIPE,
            )
            _stdout, _stderr = process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"pg_dump failed (exit code {process.returncode})")
            archive.flush()
            os.fsync(archive.fileno())

        with temporary.open("rb") as archive:
            verification = run(
                ["docker", "compose", "exec", "-T", "postgres", "pg_restore", "--list"],
                cwd=ROOT,
                stdin=archive,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
        if verification.returncode != 0:
            raise RuntimeError("PostgreSQL rejected the generated backup archive")

        # A hard link publishes the verified archive atomically and never replaces
        # a backup created concurrently with the same destination name.
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up PostgreSQL from the TeamAI Compose service.")
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        help="New archive path (defaults to backups/teamai-<UTC timestamp>.dump)",
    )
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.destination or ROOT / "backups" / f"teamai-{stamp}.dump"
    try:
        print(backup(destination))
        return 0
    except (FileExistsError, OSError, RuntimeError) as exc:
        print(f"PostgreSQL backup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
