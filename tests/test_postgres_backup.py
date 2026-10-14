from __future__ import annotations

import subprocess

import pytest

import teamai_postgres_backup


def test_backup_streams_archive_to_host_and_verifies_before_publish(tmp_path):
    calls = []

    class DumpProcess:
        returncode = 0

        def __init__(self, command, *, cwd, stdout, stderr):
            calls.append((command, cwd))
            stdout.write(b"postgres-custom-archive")

        def communicate(self):
            return b"", b""

    def verify(command, *, cwd, stdin, stdout, stderr, check):
        calls.append((command, cwd))
        assert stdin.read() == b"postgres-custom-archive"
        assert stdout == subprocess.DEVNULL
        assert check is False
        return subprocess.CompletedProcess(command, 0)

    destination = tmp_path / "backups" / "verified.dump"
    result = teamai_postgres_backup.backup(destination, popen=DumpProcess, run=verify)
    assert result == destination.resolve()
    assert destination.read_bytes() == b"postgres-custom-archive"
    assert len(calls) == 2
    assert "pg_dump" in calls[0][0][-1]
    assert calls[1][0][-1] == "--list"
    assert not list(destination.parent.glob("*.tmp"))


def test_failed_dump_removes_partial_archive_and_does_not_publish(tmp_path):
    class FailedDump:
        returncode = 1

        def __init__(self, _command, *, stdout, **_kwargs):
            stdout.write(b"partial")

        def communicate(self):
            return b"", b"database password must not be printed"

    destination = tmp_path / "failed.dump"
    with pytest.raises(RuntimeError, match="exit code 1"):
        teamai_postgres_backup.backup(destination, popen=FailedDump, run=lambda *_args, **_kwargs: None)
    assert not destination.exists()
    assert not list(tmp_path.iterdir())


def test_backup_refuses_to_overwrite_existing_archive(tmp_path):
    destination = tmp_path / "existing.dump"
    destination.write_bytes(b"keep")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        teamai_postgres_backup.backup(destination, popen=lambda *_args, **_kwargs: pytest.fail("must not run"))
    assert destination.read_bytes() == b"keep"
