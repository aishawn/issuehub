from __future__ import annotations

import sqlite3

import pytest

import teamai_migrate


def test_migration_marks_legacy_resolved_issues_pending_review():
    row = {"kind": "issue", "status": "resolved", "content_hash": "legacy-hash"}
    source_columns = {"kind", "status", "content_hash"}
    assert teamai_migrate.migrated_value(row, source_columns, "knowledge_status") == "pending_review"
    assert teamai_migrate.migrated_value(row, source_columns, "sync_key") == "legacy-hash"
    assert teamai_migrate.migrated_value(row, source_columns, "parent_issue_id") is None
    assert teamai_migrate.migrated_value({"kind": "knowledge", "status": "resolved", "content_hash": "article-hash"}, source_columns, "knowledge_status") == "published"


def test_migration_requires_target_url_before_opening_source(tmp_path):
    with pytest.raises(ValueError, match="target URL"):
        teamai_migrate.migrate(tmp_path / "missing.db", "")


def test_migration_rejects_invalid_sqlite_source_before_target_connection(tmp_path):
    source = tmp_path / "invalid.db"
    source.write_text("not a database", encoding="utf-8")
    with pytest.raises(sqlite3.DatabaseError):
        teamai_migrate.migrate(source, "postgresql://unused")


def test_migration_cli_has_explicit_source_and_target_options():
    import subprocess
    import sys

    result = subprocess.run([sys.executable, "teamai_migrate.py", "--help"], capture_output=True, text=True, check=True)
    assert "--database-url" in result.stdout
    assert "source" in result.stdout
