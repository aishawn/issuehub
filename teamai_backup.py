"""Safe SQLite backup, validation, and offline restore utilities."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("APP_DB", str(ROOT / "teamai.db")))
# Base columns required by the application. Sync bookkeeping columns are
# intentionally optional because init_db() adds them when an older snapshot is
# restored and the service starts.
REQUIRED_COLUMNS = {
    "id", "kind", "source", "author_id", "session_id", "title", "content",
    "tags", "status", "external_system", "external_id", "external_url",
    "content_hash", "created_at", "updated_at",
}


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def verify_database(path: Path) -> int:
    """Return the record count or raise when this is not a healthy TeamAI database."""
    if not path.is_file():
        raise FileNotFoundError(f"Database does not exist: {path}")
    with closing(_readonly_connection(path)) as conn:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise sqlite3.DatabaseError("SQLite integrity check failed: " + "; ".join(integrity[:5]))
        table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone()
        if not table:
            raise sqlite3.DatabaseError("TeamAI records table is missing")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise sqlite3.DatabaseError(f"TeamAI records table is missing columns: {', '.join(sorted(missing))}")
        return int(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])


def _publish_backup(stage: Path, destination: Path) -> None:
    try:
        os.link(stage, destination)
    except FileExistsError:
        raise FileExistsError(f"Refusing to overwrite backup: {destination}") from None


def backup_database(source: Path, destination: Path) -> int:
    """Create a consistent backup with SQLite's online backup API; never overwrite."""
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("Backup destination must differ from APP_DB")
    if not source.is_file():
        raise FileNotFoundError(f"Database does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite backup: {destination}")
    fd, stage_name = tempfile.mkstemp(prefix=f".{destination.name}.backup-", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    stage = Path(stage_name)
    try:
        with closing(sqlite3.connect(source, timeout=10)) as source_conn:
            with closing(sqlite3.connect(stage, timeout=10)) as target_conn:
                source_conn.backup(target_conn)
        count = verify_database(stage)
        _publish_backup(stage, destination)
        return count
    finally:
        stage.unlink(missing_ok=True)


def restore_database(source: Path, destination: Path) -> tuple[int, Path | None]:
    """Restore a validated snapshot, preserving an automatic pre-restore backup."""
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("Restore source must differ from APP_DB")
    verify_database(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety_backup = destination.with_name(f"{destination.name}.pre-restore-{stamp}.db")
    fd, stage_name = tempfile.mkstemp(prefix=f".{destination.name}.restore-", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    stage = Path(stage_name)

    try:
        with closing(sqlite3.connect(source, timeout=10)) as source_conn:
            with closing(sqlite3.connect(stage, timeout=10)) as stage_conn:
                source_conn.backup(stage_conn)
        restored_count = verify_database(stage)

        if destination.is_file():
            with closing(sqlite3.connect(destination, timeout=10)) as current_conn:
                journal_mode = current_conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
                if journal_mode == "wal":
                    checkpoint = current_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if checkpoint and checkpoint[0] != 0:
                        raise sqlite3.OperationalError("Could not checkpoint current WAL; stop the service and retry")

            backup_database(destination, safety_backup)
            for suffix in ("-wal", "-shm"):
                Path(str(destination) + suffix).unlink(missing_ok=True)
        else:
            safety_backup = None
            if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
                raise sqlite3.DatabaseError("Database sidecar exists without the database file")
        os.replace(stage, destination)
        return restored_count, safety_backup
    finally:
        stage.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up, verify, or restore the TeamAI SQLite database")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup", help="Create a consistent SQLite backup")
    backup_parser.add_argument("output", type=Path, help="New backup file path (must not already exist)")
    verify_parser = subparsers.add_parser("verify", help="Check database integrity and TeamAI schema")
    verify_parser.add_argument("path", type=Path, nargs="?", default=DB_PATH)
    restore_parser = subparsers.add_parser("restore", help="Restore a backup; requires the service to be stopped")
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("--yes", action="store_true", help="Confirm downtime and database replacement")
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            count = backup_database(DB_PATH, args.output)
            print(f"Backup verified: {args.output} ({count} records)")
        elif args.command == "verify":
            count = verify_database(args.path)
            print(f"Database is healthy: {args.path} ({count} records)")
        else:
            if not args.yes:
                parser.error("restore replaces APP_DB; stop TeamAI and rerun with --yes after confirming downtime")
            count, safety = restore_database(args.source, DB_PATH)
            if safety:
                print(f"Restored {count} records. Previous database preserved at: {safety}")
            else:
                print(f"Restored {count} records into a new database file.")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"teamai_backup: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
