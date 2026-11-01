"""Contract and offline end-to-end tests for the reference "github" pack.

Everything runs against a local ``git init`` workspace and a local bare repo used
as ``origin``. No network, no GitHub account, no ``gh`` dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import pytest

from manager.bridge_gate import BridgeGate
from manager.bridges import BridgeRegistry, validate_bridge_spec, validate_persona_manifest
from manager.engine import Engine
from manager.metrics import MetricsStore
from manager.notifications import NotificationManager
from manager.operations import OperationLedger
from manager.packs import github, github_flow, registry as packs
from manager.resource_locks import ResourceLocks
from manager.store import Store
from manager.work_record import WorkRecordManager

PACK_DIR = Path(__file__).resolve().parents[1] / "bridge_packs" / "github"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@localhost",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@localhost",
    "GIT_CONFIG_NOSYSTEM": "1",
}


class QuietProviders:
    models = [{"id": "worker", "provider": "test", "execution_supported": True}]

    def available(self):
        return [{"id": "test", "available": True, "executable": "test-only"}]

    async def run(self, model_id, prompt, *, workspace, allow_writes, timeout,
                  on_event, effort=""):
        return {"output": "ok", "model_id": model_id, "status": "completed", "usage": {}}


def git(cwd, *args, check=True):
    completed = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               env=GIT_ENV)
    if check and completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr}")
    return completed


@pytest.fixture()
def repo(tmp_path):
    """A real repository with one commit and a local bare remote as origin."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    git(bare, "init", "--bare", "-q")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init", "-q", "-b", "main")
    (workspace / "README.md").write_text("# proje\n", encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "ilk commit")
    git(workspace, "remote", "add", "origin", str(bare))
    return workspace, bare


@asynccontextmanager
async def stack(tmp_path):
    store = Store(tmp_path / "runs.sqlite")
    await store.open()
    work = WorkRecordManager(tmp_path / "work.sqlite")
    await work.open()
    notifications = NotificationManager(tmp_path / "notifications.sqlite")
    await notifications.open()
    ledger = OperationLedger(tmp_path / "operations.sqlite")
    await ledger.open()
    metrics = MetricsStore(tmp_path / "metrics.sqlite")
    await metrics.open()
    locks = ResourceLocks(tmp_path / "locks.sqlite")
    await locks.open()
    gate = BridgeGate(token_store=ledger)
    assert packs.register_shipped(gate.registry) == ["office", "github"]
    try:
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
            engine = Engine(store, saver, QuietProviders(), tmp_path)
            engine.attach_infrastructure(
                work=work, notifications=notifications, ledger=ledger,
                metrics=metrics, resource_locks=locks, bridge_gate=gate)
            try:
                yield engine, work, ledger
            finally:
                await engine.close()
    finally:
        await locks.close()
        await metrics.close()
        await ledger.close()
        await notifications.close()
        await work.close()
        await store.close()


# ------------------------------------------------------------------- contract


def test_manifest_is_a_complete_contract_without_dangerous():
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    spec = validate_bridge_spec(manifest)
    assert spec["id"] == "github"
    assert spec["levels"] == ["read", "draft", "apply"]
    assert "dangerous" not in spec["levels"], "merge/silme bu pakette tanımsız olmalı"
    assert "dangerous" not in spec["approval"]
    assert spec["idempotency"] == {"type": "keyed", "key": "idempotency_key"}
    assert spec["evidence"]["format"] == "github-evidence"
    assert set(spec["evidence"]["fields"]) == {
        "branch", "commit_sha", "pr_url", "test_summary", "source"}
    assert spec["approval"]["apply"] == "per_call_token"


def test_error_taxonomy_separates_the_three_classes():
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    errors = validate_bridge_spec(manifest)["errors"]
    assert "network-error" in errors["retryable"]
    assert "approval-required" in errors["needs-user"]
    assert "gh-unavailable" in errors["external-down"]
    assert not set(errors["retryable"]) & set(errors["external-down"])
    assert not set(errors["needs-user"]) & set(errors["external-down"])


def test_error_payloads_map_to_distinct_classes():
    assert github.error_payload(github.RetryableGithubError("x"))["error_class"] == "retryable"
    assert github.error_payload(github.ExternalGithubError("x"))["error_class"] == "external-down"
    assert github.error_payload(github.GithubError("x"))["error_class"] == "needs-user"
    assert github._classify("fatal: unable to access repo: Could not resolve host",
                            action="push").error_class == "retryable"
    assert github._classify("fatal: Authentication failed for 'https://example'",
                            action="push").error_class == "external-down"
    assert github._classify("! [rejected] main -> main (non-fast-forward)",
                            action="push").error_class == "needs-user"


def test_manager_persona_is_read_and_draft_only():
    validated = validate_persona_manifest(github.MANAGER_PERSONA, "scheduler")
    assert validated["bridges"]["github"] == ["read", "draft"]

    bridge_registry = BridgeRegistry()
    github.register(bridge_registry)
    gate = BridgeGate(bridge_registry)
    gate.check(github.MANAGER_PERSONA, "github", "read")
    gate.check(github.MANAGER_PERSONA, "github", "draft")
    with pytest.raises(PermissionError):
        gate.check(github.MANAGER_PERSONA, "github", "apply",
                   idempotency_key="k1", approval_token="t1")


def test_coder_persona_reads_but_cannot_draft():
    bridge_registry = BridgeRegistry()
    github.register(bridge_registry)
    gate = BridgeGate(bridge_registry)
    gate.check(github.CODER_PERSONA, "github", "read")
    with pytest.raises(PermissionError):
        gate.check(github.CODER_PERSONA, "github", "draft")


def test_apply_needs_a_single_use_token():
    bridge_registry = BridgeRegistry()
    github.register(bridge_registry)
    gate = BridgeGate(bridge_registry)
    with pytest.raises(PermissionError):
        gate.check(github.APPROVER_PERSONA, "github", "apply", idempotency_key="k1")
    gate.check(github.APPROVER_PERSONA, "github", "apply",
               idempotency_key="k1", approval_token="tok-once")
    with pytest.raises(PermissionError):
        gate.check(github.APPROVER_PERSONA, "github", "apply",
                   idempotency_key="k2", approval_token="tok-once")


def test_dangerous_level_is_not_defined():
    bridge_registry = BridgeRegistry()
    github.register(bridge_registry)
    gate = BridgeGate(bridge_registry)
    with pytest.raises(PermissionError):
        gate.check(github.APPROVER_PERSONA, "github", "dangerous",
                   idempotency_key="k1", approval_token="t1")


# ------------------------------------------------------------------ read level


def test_history_and_diff_are_read_from_git(repo):
    workspace, _bare = repo
    (workspace / "app.py").write_text("print('x')\n", encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "uygulama eklendi")

    log = github.history(workspace, limit=5)
    assert log["source"] == "git-only"
    assert [item["subject"] for item in log["commits"]][0] == "uygulama eklendi"
    assert log["summary"].splitlines()[0].endswith("2 commit · main")

    diff = github.diff_summary(workspace, base="HEAD~1")
    assert diff["source"] == "git-only"
    assert {item["path"] for item in diff["files"]} == {"app.py"}
    assert diff["summary"].startswith("HEAD~1..HEAD: 1 dosya")


def test_issue_listing_falls_back_to_git_merge_commits(repo):
    workspace, _bare = repo
    git(workspace, "checkout", "-q", "-b", "feature")
    (workspace / "f.txt").write_text("f\n", encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "özellik")
    git(workspace, "checkout", "-q", "main")
    git(workspace, "merge", "-q", "--no-ff", "feature",
        "-m", "Merge pull request #7 from test/feature")

    listing = github.list_issues_and_prs(workspace)
    expected = "gh" if github.gh_available() else "git-only"
    assert listing["source"] == expected
    if expected == "git-only":
        assert listing["issues"] == []
        assert [item["number"] for item in listing["pull_requests"]] == [7]
        assert "gh CLI yok" in listing["note"]


def test_read_refuses_a_directory_that_is_not_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(github.GithubError):
        github.history(plain)


# ----------------------------------------------------------------- draft level


def test_draft_creates_branch_and_commit_but_never_pushes(repo):
    workspace, bare = repo
    draft = github.draft(
        workspace, branch="piko/rapor", commit_message="Rapor dosyası eklendi",
        files=[{"path": "rapor.md", "content": "# Rapor\n"}],
        pr_title="Rapor ekle", test_summary="12 passed, 1 warning in 0.5s")
    assert draft["branch"] == "piko/rapor"
    assert len(draft["commit_sha"]) == 40
    assert draft["test_summary"] == "12 passed, 1 warning in 0.5s"
    assert "rapor.md" in draft["pr_body"]
    assert (workspace / "rapor.md").is_file()
    # Nothing was pushed: the bare remote has no such branch.
    branches = git(bare, "branch", "--list", "piko/rapor").stdout.strip()
    assert branches == ""


def test_draft_rejects_unsafe_branches_and_empty_commits(repo):
    workspace, _bare = repo
    for branch in ("main", "master", "../evil", "a b", "origin/main"):
        with pytest.raises(github.GithubError):
            github.draft(workspace, branch=branch, commit_message="x",
                         files=[{"path": "a.txt", "content": "a"}])
    with pytest.raises(github.GithubError):
        github.draft(workspace, branch="piko/bos", commit_message="Boş")


def test_draft_refuses_files_outside_the_workspace(repo, tmp_path):
    workspace, _bare = repo
    with pytest.raises(github.GithubError):
        github.draft(workspace, branch="piko/kotu", commit_message="x",
                     files=[{"path": "../kacis.txt", "content": "x"}])


# ------------------------------------------------------------------ end to end


async def test_github_delivery_flow_end_to_end(tmp_path, repo):
    workspace, bare = repo
    async with stack(tmp_path) as (engine, work, ledger):
        item = await work.create("Kod değişikliğini yayınla", title="GitHub değişikliği",
                                 workspace=str(workspace))
        request = {
            "branch": "piko/ozellik", "commit_message": "Özellik dosyası eklendi",
            "files": [{"path": "ozellik.py", "content": "def run():\n    return 1\n"}],
            "pr_title": "Özellik ekle",
            "pr_body": "## Özet\nYeni özellik.\n",
            "test_summary": "5 passed in 0.31s",
        }

        # 1. Manager drafts: read+draft allowed, nothing pushed.
        draft = await github_flow.deliver(
            engine, persona=github.MANAGER_PERSONA, request=request,
            idempotency_key="gh-1", workspace=str(workspace), create_pr=False,
            draft_only=True)
        assert draft["ok"] is True and draft["draft_only"] is True
        committed = draft["draft"]
        assert committed["branch"] == "piko/ozellik"
        assert git(bare, "branch", "--list", "piko/ozellik").stdout.strip() == ""
        assert await ledger.list() == [], "taslak deftere yazmamalı"

        # 2. Approval is bound to the reviewed branch and commit.
        approval = github_flow.issue_approval(committed)
        assert approval["approval_token"].startswith("github-")
        assert approval["commit_sha"] == committed["commit_sha"]

        # A manager without the apply grant cannot use the approved token.
        with pytest.raises(PermissionError):
            await engine.authorize_bridge(
                {"role": "scheduler", "bridges": {"github": {"levels": ["read", "draft"]}}},
                "github", "apply", approval_token=approval["approval_token"],
                idempotency_key="gh-denied", payload={"branch": committed["branch"]})
        assert git(bare, "branch", "--list", "piko/ozellik").stdout.strip() == ""

        # 3. Approved delivery pushes to the local bare remote and records evidence.
        delivered = await github_flow.deliver(
            engine, persona=github.MANAGER_PERSONA, request=request,
            approval_token=approval["approval_token"], idempotency_key="gh-2",
            work_id=item["id"], run_id="run-gh-1", workspace=str(workspace),
            create_pr=False)
        assert delivered["ok"] is True, delivered
        evidence = delivered["evidence"]
        assert evidence["format"] == "github-evidence"
        assert evidence["branch"] == "piko/ozellik"
        assert evidence["commit_sha"] == committed["commit_sha"]
        assert evidence["test_summary"] == "5 passed in 0.31s"
        assert evidence["pr_url"] == ""
        assert evidence["pr_error"]  # gh yoksa dürüstçe raporlanır
        assert evidence["source"] in {"gh", "git-only"}
        assert delivered["operation"]["status"] == "applied"

        # The branch really exists on the local bare remote with the same commit.
        remote_sha = git(bare, "rev-parse", "piko/ozellik").stdout.strip()
        assert remote_sha == committed["commit_sha"]

        deliverable = delivered["deliverable"]
        assert deliverable["version"] == 1
        assert deliverable["kind"] == "github-change"
        assert deliverable["bridge_id"] == "github"
        assert committed["branch"] in deliverable["files"]
        assert committed["commit_sha"] in deliverable["files"]
        joined = " ".join(deliverable["evidence"])
        assert "branch piko/ozellik" in joined
        assert "5 passed in 0.31s" in joined

        # 4. Replaying the same key does not push again nor create a new version.
        before = git(bare, "rev-parse", "piko/ozellik").stdout.strip()
        replay = await github_flow.deliver(
            engine, persona=github.MANAGER_PERSONA, request=request,
            approval_token="unused-token", idempotency_key="gh-2",
            work_id=item["id"], run_id="run-gh-1", workspace=str(workspace),
            create_pr=False)
        assert replay["replayed"] is True
        assert git(bare, "rev-parse", "piko/ozellik").stdout.strip() == before
        assert len(await work.list_deliverables(item["id"])) == 1
        assert [op["status"] for op in await ledger.list()] == ["applied"]

        # 5. The developer card reads the manifest and the last github delivery.
        latest = await work.latest_deliverable(bridge_id="github")
        assert latest["bridge_id"] == "github"
        assert latest["evidence"]


async def test_github_apply_without_approval_token_does_not_push(tmp_path, repo):
    workspace, bare = repo
    async with stack(tmp_path) as (engine, work, ledger):
        request = {
            "branch": "piko/onaysiz", "commit_message": "Onaysız",
            "files": [{"path": "x.txt", "content": "x"}],
            "pr_title": "Onaysız deneme", "test_summary": "1 passed",
        }
        with pytest.raises(PermissionError):
            await github_flow.deliver(
                engine, persona=github.MANAGER_PERSONA, request=request,
                idempotency_key="gh-denied", workspace=str(workspace),
                create_pr=False)
        assert git(bare, "branch", "--list", "piko/onaysiz").stdout.strip() == ""
        assert await ledger.list() == []


async def test_github_apply_reports_a_missing_remote_as_external_down(tmp_path):
    workspace = tmp_path / "solo"
    workspace.mkdir()
    git(workspace, "init", "-q", "-b", "main")
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "ilk")

    async with stack(tmp_path) as (engine, work, ledger):
        item = await work.create("Uzak olmadan yayınla", workspace=str(workspace))
        request = {"branch": "piko/uzak-yok", "commit_message": "Deneme",
                   "files": [{"path": "b.txt", "content": "b"}],
                   "pr_title": "Deneme", "test_summary": "1 passed"}
        approval = github_flow.issue_approval(github.draft(workspace, **request))
        result = await github_flow.deliver(
            engine, persona=github.MANAGER_PERSONA, request=request,
            approval_token=approval["approval_token"], idempotency_key="gh-noremote",
            work_id=item["id"], workspace=str(workspace), create_pr=False)
        assert result["ok"] is False
        assert result["error"]["error_class"] == "external-down"
        assert "remote-unavailable" in result["error"]["message"]
        assert [op["status"] for op in await ledger.list()] == ["failed"]
        assert await work.find_deliverable(item["id"], "gh-noremote") is None


async def test_github_draft_refuses_an_empty_change(tmp_path, repo):
    workspace, _bare = repo
    async with stack(tmp_path) as (engine, work, _ledger):
        request = {"branch": "piko/bos", "commit_message": "Boş",
                   "pr_title": "Boş"}
        result = await github_flow.deliver(
            engine, persona=github.MANAGER_PERSONA, request=request,
            idempotency_key="gh-empty", workspace=str(workspace), create_pr=False)
        assert result["ok"] is False
        assert result["error"]["error_class"] == "needs-user"
