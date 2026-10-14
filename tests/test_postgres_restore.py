from __future__ import annotations

import subprocess

import pytest

import teamai_postgres_restore


def test_restore_validates_archive_before_changing_database(tmp_path):
    archive = tmp_path / "valid.dump"
    archive.write_bytes(b"custom archive")
    calls = []

    def verify(command, *, stdin, **_kwargs):
        calls.append("verify")
        assert stdin.read() == b"custom archive"
        return subprocess.CompletedProcess(command, 0)

    def state(command, **_kwargs):
        calls.append("state")
        return subprocess.CompletedProcess(command, 0, stdout="postgres\n", stderr="")

    class RestoreProcess:
        returncode = 0

        def __init__(self, _command, *, stdin, **_kwargs):
            calls.append("restore")
            assert stdin.read() == b"custom archive"

        def communicate(self):
            return b"", b""

    def run(command, **kwargs):
        return state(command, **kwargs) if "ps" in command else verify(command, **kwargs)

    teamai_postgres_restore.restore(archive, run=run, popen=RestoreProcess)
    assert calls == ["state", "verify", "restore"]


def test_invalid_archive_never_invokes_destructive_restore(tmp_path):
    archive = tmp_path / "invalid.dump"
    archive.write_bytes(b"not a postgres archive")

    def reject(command, **_kwargs):
        if "ps" in command:
            return subprocess.CompletedProcess(command, 0, stdout="postgres\n", stderr="")
        return subprocess.CompletedProcess(command, 1)

    with pytest.raises(RuntimeError, match="target database was not changed"):
        teamai_postgres_restore.restore(
            archive,
            run=reject,
            popen=lambda *_args, **_kwargs: pytest.fail("restore must not run for an invalid archive"),
        )


def test_restore_refuses_while_teamai_service_is_running(tmp_path):
    archive = tmp_path / "valid.dump"
    archive.write_bytes(b"custom archive")

    def running(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="teamai\npostgres\n", stderr="")

    with pytest.raises(RuntimeError, match="stop it before restoring"):
        teamai_postgres_restore.restore(
            archive,
            run=running,
            popen=lambda *_args, **_kwargs: pytest.fail("restore must not run while app service is active"),
        )


def test_restore_refuses_empty_archive_before_docker(tmp_path):
    archive = tmp_path / "empty.dump"
    archive.touch()

    with pytest.raises(RuntimeError, match="archive is empty"):
        teamai_postgres_restore.restore(
            archive,
            run=lambda *_args, **_kwargs: pytest.fail("empty archive must be rejected locally"),
        )


def test_restore_requires_an_existing_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        teamai_postgres_restore.restore(tmp_path / "missing.dump")
