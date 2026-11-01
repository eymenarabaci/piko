"""Tests for the public-repo leak detector."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "check_public_repo.py"

GIT_IDENTITY = [
    "-c",
    "user.name=Public Repo Test",
    "-c",
    "user.email=public-repo-test@example.com",
]


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *GIT_IDENTITY, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    return repo


def write_and_stage(repo: Path, relpath: str, content: str = "x\n") -> None:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", "--", relpath)


def run_tool(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(repo)],
        capture_output=True,
        text=True,
    )


def test_clean_repository_passes(tmp_path):
    repo = make_repo(tmp_path)
    write_and_stage(repo, "README.md", "hello\n")

    result = run_tool(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "clean" in result.stdout.lower()


def test_private_roadmap_filename_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    write_and_stage(repo, "PIKO-YOL-HARITASI.md", "internal plan\n")

    result = run_tool(repo)

    assert result.returncode == 1
    assert "PIKO-YOL-HARITASI.md" in result.stdout


def test_env_file_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    write_and_stage(repo, "config/app.env", "API_KEY=1\n")

    result = run_tool(repo)

    assert result.returncode == 1
    assert "config/app.env" in result.stdout


def test_private_key_and_secret_names_are_rejected(tmp_path):
    repo = make_repo(tmp_path)
    write_and_stage(repo, "id_rsa", "key\n")
    write_and_stage(repo, "src/client_secret.py", "value = 1\n")

    result = run_tool(repo)

    assert result.returncode == 1
    assert "id_rsa" in result.stdout
    assert "client_secret.py" in result.stdout


def test_token_name_outside_tests_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    write_and_stage(repo, "src/token_store.py", "value = 1\n")

    result = run_tool(repo)

    assert result.returncode == 1
    assert "token_store.py" in result.stdout


def test_token_and_secret_names_in_tests_are_allowed(tmp_path):
    repo = make_repo(tmp_path)
    write_and_stage(repo, "tests/test_token_flow.py", "def test_x():\n    assert True\n")
    write_and_stage(repo, "fixtures/secret_values.json", "{}\n")

    result = run_tool(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_absolute_user_path_in_source_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    absolute_path = "C:" + "\\" + "Users" + "\\" + "alice" + "\\" + "Desktop" + "\\" + "notes.txt"
    write_and_stage(repo, "src/main.py", "# data lives in " + absolute_path + "\n")

    result = run_tool(repo)

    assert result.returncode == 1
    assert "src/main.py" in result.stdout
    assert "absolute user path" in result.stdout


def test_untracked_files_are_ignored(tmp_path):
    repo = make_repo(tmp_path)
    write_and_stage(repo, "README.md", "hello\n")
    (repo / "PIKO-YOL-HARITASI.md").write_text("not staged\n", encoding="utf-8")

    result = run_tool(repo)

    assert result.returncode == 0, result.stdout + result.stderr
