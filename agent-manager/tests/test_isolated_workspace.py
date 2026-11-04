"""Isolated file snapshots merge only changes against a preserved baseline."""
import json
import os
from pathlib import Path

import pytest

from manager import isolated_workspace as isolation
from manager.isolated_workspace import IsolatedWorkspace, WorkspaceConflict


@pytest.fixture
def tree(tmp_path):
    source = tmp_path / "project"
    source.mkdir()
    (source / "a.txt").write_text("original-a", encoding="utf-8")
    (source / "b.txt").write_text("original-b", encoding="utf-8")
    return source, tmp_path / "isolated"


def create(tree, identity="step-a"):
    return IsolatedWorkspace.create(*tree, identity)


def test_disjoint_parallel_copies_merge_without_overwriting_sibling(tree):
    first, second = create(tree, "first"), create(tree, "second")
    (first.path / "a.txt").write_text("first", encoding="utf-8")
    (second.path / "b.txt").write_text("second", encoding="utf-8")
    assert first.merge()["changed"] == ["a.txt"]
    assert second.merge()["changed"] == ["b.txt"]
    assert (tree[0] / "a.txt").read_text() == "first"
    assert (tree[0] / "b.txt").read_text() == "second"
    assert first.cleanup() and second.cleanup()
    assert not first.path.exists() and not second.path.exists()


def test_all_conflicts_are_preflighted_before_any_canonical_file_is_written(tree):
    copy = create(tree)
    (copy.path / "a.txt").write_text("change-a", encoding="utf-8")
    (copy.path / "b.txt").write_text("change-b", encoding="utf-8")
    (copy.path / "new.txt").write_text("new", encoding="utf-8")
    (tree[0] / "b.txt").write_text("user-edit", encoding="utf-8")
    with pytest.raises(WorkspaceConflict) as error:
        copy.merge()
    assert error.value.conflicts == ["b.txt"]
    assert (tree[0] / "a.txt").read_text() == "original-a"
    assert (tree[0] / "b.txt").read_text() == "user-edit"
    assert not (tree[0] / "new.txt").exists()
    assert copy.cleanup() is False and copy.path.is_dir()


def test_same_identity_reuses_modified_copy_and_original_baseline(tree):
    original = create(tree)
    (original.path / "a.txt").write_text("branch-change", encoding="utf-8")
    saved = original.metadata.read_bytes()
    (tree[0] / "a.txt").write_text("user-change", encoding="utf-8")
    retry = create(tree)
    assert retry.path == original.path
    assert retry.metadata.read_bytes() == saved
    assert (retry.path / "a.txt").read_text() == "branch-change"
    with pytest.raises(WorkspaceConflict):
        retry.merge()


def test_delete_conflict_preserves_user_edit_and_successful_delete_is_idempotent(tree):
    copy = create(tree)
    (copy.path / "a.txt").unlink()
    (tree[0] / "a.txt").write_text("user-change", encoding="utf-8")
    with pytest.raises(WorkspaceConflict):
        copy.merge()
    assert (tree[0] / "a.txt").read_text() == "user-change"
    (tree[0] / "a.txt").write_text("original-a", encoding="utf-8")
    assert copy.merge()["deleted"] == ["a.txt"]
    assert not (tree[0] / "a.txt").exists()
    assert copy.merge()["unchanged"] == ["a.txt"]


def test_new_file_conflicts_with_user_file_but_identical_content_is_idempotent(tree):
    copy = create(tree)
    (copy.path / "new.txt").write_text("branch", encoding="utf-8")
    (tree[0] / "new.txt").write_text("user", encoding="utf-8")
    with pytest.raises(WorkspaceConflict):
        copy.merge()
    (tree[0] / "new.txt").write_text("branch", encoding="utf-8")
    assert copy.merge()["unchanged"] == ["new.txt"]
    assert copy.merge()["changed"] == []


def test_excluded_directories_and_secrets_never_copy_or_merge(tree):
    source = tree[0]
    for directory in [".git", ".venv", "node_modules", "data", "work", ".cache", "__pycache__"]:
        (source / directory).mkdir()
        (source / directory / "private.txt").write_text("source-only", encoding="utf-8")
    for secret in [".env", ".env.local", "credentials.json", "service-account.json", "private.key"]:
        (source / secret).write_text("source-secret", encoding="utf-8")
    copy = create(tree)
    assert sorted(file.name for file in copy.path.iterdir()) == ["a.txt", "b.txt"]
    (copy.path / ".env").write_text("do-not-merge", encoding="utf-8")
    (copy.path / "data").mkdir()
    (copy.path / "data" / "new.txt").write_text("do-not-merge", encoding="utf-8")
    assert copy.merge()["changed"] == []
    assert (source / ".env").read_text() == "source-secret"
    assert not (source / "data" / "new.txt").exists()


def test_partial_io_failure_rolls_back_all_written_and_deleted_files(tree, monkeypatch):
    copy = create(tree)
    (copy.path / "a.txt").unlink()
    (copy.path / "b.txt").write_text("change-b", encoding="utf-8")
    original = isolation._atomic_bytes
    def fail_second_canonical_write(path, content):
        if path == tree[0] / "b.txt" and content == b"change-b":
            raise OSError("injected disk error")
        return original(path, content)
    monkeypatch.setattr(isolation, "_atomic_bytes", fail_second_canonical_write)
    with pytest.raises(OSError, match="injected disk error"):
        copy.merge()
    assert (tree[0] / "a.txt").read_text() == "original-a"
    assert (tree[0] / "b.txt").read_text() == "original-b"
    assert copy.cleanup() is False
    assert any(copy.container.glob("backup-*"))
    monkeypatch.setattr(isolation, "_atomic_bytes", original)
    assert copy.merge()["deleted"] == ["a.txt"]


def _symlink_or_skip(link, target):
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"OS does not permit test symlinks: {error}")


def test_source_symlink_is_rejected_without_copying_external_data(tree, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("external-secret", encoding="utf-8")
    _symlink_or_skip(tree[0] / "linked.txt", outside)
    with pytest.raises(ValueError, match="Linked"):
        create(tree)
    assert outside.read_text() == "external-secret"


def test_branch_symlink_blocks_merge_before_any_write(tree, tmp_path):
    copy = create(tree)
    (copy.path / "a.txt").write_text("branch", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("external", encoding="utf-8")
    _symlink_or_skip(copy.path / "linked.txt", outside)
    with pytest.raises(ValueError, match="Linked"):
        copy.merge()
    assert (tree[0] / "a.txt").read_text() == "original-a"
    assert outside.read_text() == "external"


def test_canonical_parent_symlink_blocks_merge(tree, tmp_path):
    copy = create(tree)
    (copy.path / "newdir").mkdir()
    (copy.path / "newdir" / "new.txt").write_text("branch", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_or_skip(tree[0] / "newdir", outside)
    with pytest.raises(ValueError, match="Linked"):
        copy.merge()
    assert not (outside / "new.txt").exists()


def test_cleanup_requires_success_and_refuses_post_merge_edits(tree):
    copy = create(tree)
    assert copy.cleanup() is False
    (copy.path / "a.txt").write_text("branch", encoding="utf-8")
    copy.merge()
    (copy.path / "a.txt").write_text("new-unmerged-evidence", encoding="utf-8")
    assert copy.cleanup() is False and copy.path.exists()


def test_overlap_and_metadata_path_traversal_are_rejected(tree):
    with pytest.raises(ValueError, match="overlap"):
        IsolatedWorkspace.create(tree[0], tree[0] / "data", "step")
    copy = create(tree)
    data = json.loads(copy.metadata.read_text(encoding="utf-8"))
    data["baseline"]["../escape.txt"] = "0" * 64
    copy.metadata.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="relative"):
        copy.merge()


def test_canonical_parent_file_is_a_conflict_not_partial_directory_creation(tree):
    copy = create(tree)
    (copy.path / "newdir").mkdir()
    (copy.path / "newdir" / "new.txt").write_text("branch", encoding="utf-8")
    (tree[0] / "newdir").write_text("user-file", encoding="utf-8")
    with pytest.raises(WorkspaceConflict):
        copy.merge()
    assert (tree[0] / "newdir").read_text() == "user-file"


def test_git_worktree_pointer_and_auth_dotfiles_are_excluded(tree):
    source = tree[0]
    for name in [".git", ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".envrc"]:
        (source / name).write_text("private-config", encoding="utf-8")
    copy = create(tree)
    assert sorted(file.name for file in copy.path.iterdir()) == ["a.txt", "b.txt"]
    (copy.path / ".git").write_text("gitdir: external-location", encoding="utf-8")
    assert copy.merge()["changed"] == []
    assert (source / ".git").read_text() == "private-config"


@pytest.mark.parametrize("deleted", [False, True])
def test_rollback_preserves_external_edit_after_apply(tree, monkeypatch, deleted):
    copy = create(tree)
    if deleted:
        (copy.path / "a.txt").unlink()
    else:
        (copy.path / "a.txt").write_text("branch-a", encoding="utf-8")
    (copy.path / "b.txt").write_text("branch-b", encoding="utf-8")
    original = isolation._atomic_bytes
    def external_edit_then_fail(path, content):
        if path == tree[0] / "b.txt" and content == b"branch-b":
            (tree[0] / "a.txt").write_text("external-user-edit", encoding="utf-8")
            raise OSError("injected error after external change")
        return original(path, content)
    monkeypatch.setattr(isolation, "_atomic_bytes", external_edit_then_fail)
    with pytest.raises(OSError, match="external change"):
        copy.merge()
    assert (tree[0] / "a.txt").read_text() == "external-user-edit"
    assert (tree[0] / "b.txt").read_text() == "original-b"
    metadata = json.loads(copy.metadata.read_text(encoding="utf-8"))
    assert metadata["state"] == "rollback_failed"
    assert metadata["rollback_errors"] == ["a.txt"]
    assert copy.cleanup() is False
    assert any(copy.container.glob("backup-*"))
