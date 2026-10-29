"""Fast-forward updates and data preservation, using only local Git repos."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

from manager.app import create_app
from manager.updates import Updater


def git(folder: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    return subprocess.check_output(["git", *args], cwd=folder, env=env, text=True).strip()


def test_fast_forward_preserves_sqlite_data(tmp_path: Path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=main")
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "--initial-branch=main")
    (seed / ".gitignore").write_text("data/\n", encoding="utf-8")
    (seed / "app.txt").write_text("one", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "initial")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "main")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(remote), str(checkout))
    data = checkout / "data"
    data.mkdir()
    with sqlite3.connect(data / "runs.sqlite") as db:
        db.execute("CREATE TABLE keep_me (value TEXT)")
        db.execute("INSERT INTO keep_me VALUES ('retained')")
    (seed / "app.txt").write_text("two", encoding="utf-8")
    git(seed, "add", "app.txt")
    git(seed, "commit", "-m", "update")
    git(seed, "push", "origin", "main")

    updater = Updater(checkout, data)
    updater._repo_name = lambda: "local/test"  # Only the GitHub URL check is replaced.
    status = asyncio.run(updater.status(refresh=True))
    assert status["eligible"] and status["behind"] == 1
    result = asyncio.run(updater.apply())
    assert result["restart_required"] and (checkout / "app.txt").read_text() == "two"
    with sqlite3.connect(data / "runs.sqlite") as db:
        assert db.execute("SELECT value FROM keep_me").fetchone() == ("retained",)
    backups = list((data / "backups").glob("before-update-*.zip"))
    assert len(backups) == 1
    with zipfile.ZipFile(backups[0]) as archive:
        assert "runs.sqlite" in archive.namelist()


def test_dirty_checkout_blocks_update(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    (repo / "app.txt").write_text("one", encoding="utf-8")
    git(repo, "add", "app.txt")
    git(repo, "commit", "-m", "initial")
    (repo / "app.txt").write_text("local edit", encoding="utf-8")
    updater = Updater(repo, repo / "data")
    updater._repo_name = lambda: "local/test"
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    result = asyncio.run(updater.status())
    assert result["dirty"] and not result["eligible"]


def test_remote_peer_cannot_apply_update(tmp_path: Path):
    app = create_app(data_dir=tmp_path / "data")
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/update/apply")
    scope = {"type": "http", "method": "POST", "path": "/api/update/apply",
             "headers": [(b"host", b"127.0.0.1:8765")],
             "client": ("100.111.13.55", 50000), "server": ("0.0.0.0", 8765),
             "scheme": "http", "query_string": b""}
    with pytest.raises(HTTPException) as blocked:
        asyncio.run(endpoint(Request(scope)))
    assert blocked.value.status_code == 403
