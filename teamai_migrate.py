from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

COLUMNS = (
    "id", "kind", "source", "author_id", "session_id", "title", "content", "tags", "status",
    "external_system", "external_id", "external_url", "sync_error", "content_hash", "sync_key",
    "sync_lock_until", "created_at", "updated_at", "project", "tech_stack", "knowledge_status",
    "parent_issue_id", "knowledge_record_id", "reviewed_by", "work_type", "visibility", "owner_username",
)


def migrated_value(row: sqlite3.Row, source_columns: set[str], column: str):
    if column in source_columns:
        return row[column]
    if column == "tags":
        return "[]"
    if column == "sync_key":
        return row["content_hash"]
    if column == "project":
        return ""
    if column == "work_type":
        return "项目支撑"
    if column == "visibility":
        return "shared"
    if column == "owner_username":
        return ""
    if column == "tech_stack":
        return "[]"
    if column == "knowledge_status":
        if row["kind"] == "knowledge":
            return "published"
        if row["kind"] == "issue" and row["status"] == "resolved":
            return "pending_review"
        return "not_applicable"
    if column in {"parent_issue_id", "knowledge_record_id", "reviewed_by", "author_id", "session_id", "external_system", "external_id", "external_url", "sync_error", "sync_lock_until"}:
        return None
    raise ValueError(f"No migration default is defined for missing column: {column}")


def migrate(source_path: Path, database_url: str) -> tuple[int, int, int, int, int]:
    if not database_url.strip():
        raise ValueError("PostgreSQL target URL is required")
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    with sqlite3.connect(f"file:{source_path.resolve().as_posix()}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        try:
            columns_in_source = {row[1] for row in source.execute("PRAGMA table_info(records)")}
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("SQLite source does not contain a readable records table") from exc
        if not columns_in_source:
            raise RuntimeError("SQLite source does not contain a records table")
        required = {"id", "kind", "source", "title", "content", "content_hash", "status", "created_at", "updated_at"}
        if not required.issubset(columns_in_source):
            raise RuntimeError("SQLite records table is missing required columns")
        columns = list(COLUMNS)
        rows = source.execute("SELECT * FROM records ORDER BY id").fetchall()

    inserted = 0
    skipped = 0
    id_conflicts = 0
    content_conflicts = 0
    relation_conflicts = 0
    os.environ["DATABASE_URL"] = database_url
    import app.main as main
    main = importlib.reload(main)
    main.init_db()

    # One transaction covers the full import so an interrupted migration cannot
    # leave a partially imported dataset that looks like a completed run.
    with psycopg.connect(database_url) as target:
        with target.cursor() as cursor:
            # Source rows may refer to an article with an ID greater than its issue.
            # Insert rows without the relationship first, then restore links in one pass.
            relation_columns = {"parent_issue_id", "knowledge_record_id"}
            row_relations = []
            id_map: dict[int, int] = {}
            write_columns = [column for column in columns if column not in relation_columns]
            write_statement = f"INSERT INTO records ({', '.join(write_columns)}) VALUES ({', '.join(['%s'] * len(write_columns))}) ON CONFLICT DO NOTHING"
            for row in rows:
                row_relations.append((row["id"], row["parent_issue_id"] if "parent_issue_id" in columns_in_source else None, row["knowledge_record_id"] if "knowledge_record_id" in columns_in_source else None))
                cursor.execute("SELECT content_hash FROM records WHERE id = %s", (row["id"],))
                id_conflict = cursor.fetchone()
                cursor.execute("SELECT id FROM records WHERE content_hash = %s ORDER BY id LIMIT 1", (row["content_hash"],))
                hash_conflict = cursor.fetchone()
                cursor.execute(write_statement, tuple(migrated_value(row, columns_in_source, column) for column in write_columns))
                inserted += cursor.rowcount
                skipped += 1 - cursor.rowcount
                if cursor.rowcount == 1:
                    id_map[row["id"]] = row["id"]
                else:
                    id_conflicts += int(id_conflict is not None)
                    content_conflicts += int(hash_conflict is not None)
                    if id_conflict is not None and id_conflict[0] == row["content_hash"]:
                        id_map[row["id"]] = row["id"]
            relation_fields = [field for field in ("parent_issue_id", "knowledge_record_id") if field in columns_in_source]
            for record_id, parent_id, knowledge_id in row_relations:
                mapped_id = id_map.get(record_id)
                mapped_parent = id_map.get(parent_id) if parent_id is not None else None
                mapped_knowledge = id_map.get(knowledge_id) if knowledge_id is not None else None
                if mapped_id is None or (parent_id is not None and mapped_parent is None) or (knowledge_id is not None and mapped_knowledge is None):
                    relation_conflicts += 1
                    continue
                if relation_fields:
                    values = {"parent_issue_id": mapped_parent, "knowledge_record_id": mapped_knowledge}
                    assignments = ", ".join(f"{field} = %s" for field in relation_fields)
                    cursor.execute(f"UPDATE records SET {assignments} WHERE id = %s", tuple(values[field] for field in relation_fields) + (mapped_id,))
            cursor.execute("SELECT setval(pg_get_serial_sequence('records', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM records")
    return inserted, skipped, id_conflicts, content_conflicts, relation_conflicts


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate a SQLite TeamAI database into PostgreSQL.")
    parser.add_argument("source", nargs="?", default=os.getenv("APP_DB", "teamai.db"), help="SQLite database path (opened read-only)")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""), help="PostgreSQL target URL (defaults to DATABASE_URL)")
    args = parser.parse_args()
    if not args.database_url.strip():
        parser.error("set DATABASE_URL or pass --database-url")
    inserted, skipped, id_conflicts, content_conflicts, relation_conflicts = migrate(Path(args.source), args.database_url.strip())
    print(f"Migrated {inserted}; skipped {skipped} (same ID: {id_conflicts}, same content hash: {content_conflicts}); unresolved relationships: {relation_conflicts}.")


if __name__ == "__main__":
    main()
