"""The "github" reference pack: code and version control, local-first.

Levels
------
``read``  — commit history, diff summary, issue/PR listing. ``gh`` is used when
   it is installed and authenticated; otherwise a git-only fallback is used and
   the chosen source is reported explicitly in the result and evidence.
``draft`` — create a local branch, write files, commit, and draft a PR title/body.
   Nothing leaves the machine.
``apply`` — push the branch to the remote and open a PR when ``gh`` exists. The
   operation ledger key is per push, and a single-use approval token is required.

There is deliberately **no** ``dangerous`` level: merge and repository deletion
are not defined for this pack.

Evidence: branch name, commit sha, PR link (when available), the pytest summary
line and the data source (``gh`` or ``git-only``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BRIDGE_ID = "github"
EVIDENCE_FORMAT = "github-evidence"
GIT_TIMEOUT = 30
PUSH_TIMEOUT = 90
SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$")
FORBIDDEN_BRANCH = re.compile(r"(^|/)\.\.|//|~|\^|:|\?|\*|\[|\\|@\{|\s")


class GithubError(ValueError):
    error_class = "needs-user"


class RetryableGithubError(GithubError):
    error_class = "retryable"


class ExternalGithubError(GithubError):
    error_class = "external-down"


def error_payload(exc: Exception) -> dict:
    return {"bridge": BRIDGE_ID,
            "error_class": getattr(exc, "error_class", "needs-user"),
            "message": str(exc)[:800]}


NETWORK_MARKERS = (
    "could not resolve host", "unable to access", "connection timed out",
    "operation timed out", "network is unreachable", "connection reset",
    "temporary failure in name resolution", "early eof", "rpc failed",
)
AUTH_MARKERS = (
    "authentication failed", "could not read username", "permission denied",
    "invalid username or password", "403 forbidden",
)


def _classify(stderr: str, *, action: str) -> Exception:
    text = (stderr or "").lower()
    if any(marker in text for marker in AUTH_MARKERS):
        return ExternalGithubError(f"{action}: kimlik doğrulama gerekli. {stderr.strip()[:400]}")
    if any(marker in text for marker in NETWORK_MARKERS):
        return RetryableGithubError(f"{action}: ağ hatası. {stderr.strip()[:400]}")
    if "gh-unavailable" in text:
        return ExternalGithubError(f"{action}: gh CLı kullanılamıyor.")
    return GithubError(f"{action} başarısız. {stderr.strip()[:400]}")


def _run(argv, *, cwd, timeout=GIT_TIMEOUT, env=None, action="komut"):
    try:
        completed = subprocess.run(
            argv, cwd=str(cwd), timeout=timeout, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env)
    except FileNotFoundError as exc:
        raise ExternalGithubError(f"{action}: '{argv[0]}' bulunamadı.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RetryableGithubError(f"{action}: zaman aşımı.") from exc
    if completed.returncode != 0:
        raise _classify(completed.stderr or completed.stdout, action=action)
    return completed.stdout


def gh_available() -> bool:
    return shutil.which("gh") is not None


def _root(workspace) -> Path:
    if not str(workspace or "").strip():
        raise GithubError("Çalışma alanı gerekli.")
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise GithubError("Çalışma alanı mevcut bir dizin olmalı.")
    return root


def _git_dir(root: Path) -> None:
    try:
        toplevel = _run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                        action="git deposu doğrulama").strip()
    except GithubError as exc:
        raise GithubError("Çalışma alanı bir git deposu değil.") from exc
    if not toplevel or Path(toplevel).resolve() != root.resolve():
        # Being inside a parent repository is not the same as owning one.
        raise GithubError("Çalışma alanı kendi git deposunun kökü olmalı.")


def _identity_env() -> dict:
    env = dict(os.environ)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    env.update({
        "GIT_AUTHOR_NAME": env.get("PIKO_GIT_AUTHOR", "Piko Agent"),
        "GIT_AUTHOR_EMAIL": env.get("PIKO_GIT_EMAIL", "piko@localhost"),
        "GIT_COMMITTER_NAME": env.get("PIKO_GIT_AUTHOR", "Piko Agent"),
        "GIT_COMMITTER_EMAIL": env.get("PIKO_GIT_EMAIL", "piko@localhost"),
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    })
    return env


def _validate_branch(branch: str, remote: str = "origin") -> str:
    branch = str(branch or "").strip()
    if not branch or not SAFE_BRANCH.fullmatch(branch) or FORBIDDEN_BRANCH.search(branch):
        raise GithubError("Geçersiz veya güvenli olmayan branch adı.")
    if branch in {"main", "master"} or branch.startswith(f"{remote}/"):
        raise GithubError("Ana dala doğrudan yazılmaz; ayrı bir branch gerekli.")
    return branch


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _three_line(lines, fallback: str) -> str:
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    return "\n".join(cleaned[:3]) if cleaned else fallback


def _summary_line(text: str) -> str:
    for line in reversed(str(text or "").splitlines()):
        if "passed" in line or "failed" in line or "error" in line.lower():
            return line.strip()[:300]
    return ""


# --------------------------------------------------------------------------- read


def history(workspace, *, limit: int = 20) -> dict:
    root = _root(workspace)
    _git_dir(root)
    limit = max(1, min(int(limit), 200))
    raw = _run(["git", "log", f"-{limit}",
                "--pretty=format:%H%x1f%an%x1f%ad%x1f%s", "--date=iso-strict",
                "--no-color"], cwd=root, action="git log")
    commits = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        commits.append({"sha": parts[0], "author": parts[1],
                        "date": parts[2], "subject": parts[3]})
    branch = _branch_name(root)
    return {"bridge": BRIDGE_ID, "action": "history", "branch": branch,
            "commits": commits, "source": "git-only",
            "summary": _three_line(
                [f"{len(commits)} commit · {branch}"] +
                [f"{item['sha'][:8]} {item['subject']}" for item in commits],
                "Commit geçmişi boş.")}


def _branch_name(root: Path) -> str:
    try:
        return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=root, action="git branch").strip() or "(detached)"
    except GithubError:
        return "(bilinmiyor)"


def diff_summary(workspace, *, base: str = "", head: str = "HEAD") -> dict:
    root = _root(workspace)
    _git_dir(root)
    base = str(base or "").strip()
    head = str(head or "HEAD").strip() or "HEAD"
    for ref in (head, base):
        if ref and ref.startswith("-"):
            raise GithubError("Geçersiz git referansı.")
    target = f"{base}..{head}" if base else head
    stat = _run(["git", "diff", "--stat", "--no-color", target],
                cwd=root, action="git diff")
    name_only = _run(["git", "diff", "--name-status", "--no-color", target],
                     cwd=root, action="git diff")
    files = []
    for line in name_only.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            files.append({"status": parts[0].strip(), "path": parts[1].strip()})
    stat_lines = [line for line in stat.splitlines() if line.strip()]
    return {"bridge": BRIDGE_ID, "action": "diff", "range": target,
            "files": files, "stat": stat_lines[-1] if stat_lines else "",
            "source": "git-only",
            "summary": _three_line(
                [f"{target}: {len(files)} dosya"] + stat_lines[-2:],
                "Değişiklik yok.")}


def list_issues_and_prs(workspace, *, limit: int = 20, state: str = "open") -> dict:
    """Use ``gh`` when present; otherwise parse merge commits as a git-only fallback."""
    root = _root(workspace)
    _git_dir(root)
    limit = max(1, min(int(limit), 100))
    state = str(state or "open").strip().lower()
    if state not in {"open", "closed", "all"}:
        raise GithubError("Geçersiz issue durumu.")

    if gh_available():
        issues_raw = _run(["gh", "issue", "list", "--json", "number,title,state,url",
                           "--limit", str(limit), "--state", state],
                          cwd=root, action="gh issue list")
        prs_raw = _run(["gh", "pr", "list", "--json", "number,title,state,url,headRefName",
                        "--limit", str(limit), "--state", state],
                       cwd=root, action="gh pr list")
        issues = json.loads(issues_raw or "[]")
        pull_requests = json.loads(prs_raw or "[]")
        source = "gh"
    else:
        issues = []
        pull_requests = []
        raw = _run(["git", "log", "--merges", f"-{limit}",
                    "--pretty=format:%H%x1f%s", "--no-color"],
                   cwd=root, action="git log --merges")
        for line in raw.splitlines():
            parts = line.split("\x1f", 1)
            if len(parts) != 2:
                continue
            match = re.search(r"Merge pull request #(\d+)", parts[1])
            pull_requests.append({
                "number": int(match.group(1)) if match else None,
                "title": parts[1], "state": "merged", "url": "",
                "sha": parts[0], "source": "git-only"})
        source = "git-only"

    return {"bridge": BRIDGE_ID, "action": "issues", "source": source,
            "issues": issues, "pull_requests": pull_requests,
            "note": ("gh CLI kullanıldı." if source == "gh" else
                     "gh CLI yok; yalnız git merge commit'leri liste olarak "
                     "kullanıldı. Issue listesi boş."),
            "summary": _three_line(
                [f"kaynak: {source}", f"{len(issues)} issue", f"{len(pull_requests)} PR"],
                "Kayıt yok.")}


# -------------------------------------------------------------------------- draft


def _write_file(root: Path, relative: str, content: str) -> str:
    relative = str(relative or "").strip()
    if not relative:
        raise GithubError("Dosya yolu boş olamaz.")
    target = (root / relative).resolve()
    if not _inside(root, target) or Path(relative).is_absolute():
        raise GithubError(f"Güvenli olmayan dosya yolu: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(content), encoding="utf-8")
    return str(target.relative_to(root)).replace("\\", "/")


def draft(workspace, *, branch: str, commit_message: str, files=None,
          pr_title: str = "", pr_body: str = "", stage_all: bool = False,
          test_summary: str = "", base: str = "") -> dict:
    """Create a local branch, commit and a PR draft. Never pushes."""
    root = _root(workspace)
    _git_dir(root)
    branch = _validate_branch(branch)
    commit_message = str(commit_message or "").strip()
    if not commit_message:
        raise GithubError("Commit mesajı gerekli.")
    if len(commit_message) > 500:
        raise GithubError("Commit mesajı çok uzun.")
    files = files or []
    if not isinstance(files, list):
        raise GithubError("'files' bir liste olmalı.")
    if not files and not stage_all:
        raise GithubError("Commit için dosya veya 'stage_all' gerekli.")

    existing = _run(["git", "branch", "--list", branch], cwd=root, action="git branch")
    if existing.strip():
        # Re-drafting the same branch is allowed; it must not need a new base.
        base_branch = str(base or "").strip() or _branch_name(root)
        _run(["git", "switch", branch], cwd=root, action="git switch")
    else:
        base_branch = str(base or "").strip() or _branch_name(root)
        if base_branch == branch:
            raise GithubError("Hedef branch mevcut daldan farklı olmalı.")
        _run(["git", "switch", "-c", branch], cwd=root, action="git switch -c")

    written = []
    for item in files:
        if not isinstance(item, dict):
            raise GithubError("Her dosya kaydı nesne olmalı.")
        written.append(_write_file(root, item.get("path", ""),
                                   item.get("content", "")))

    if stage_all:
        _run(["git", "add", "-A"], cwd=root, action="git add")
    else:
        _run(["git", "add", "--", *written], cwd=root, action="git add")

    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=root,
                  action="git diff --cached").strip()
    if not staged:
        # Idempotent re-draft: the exact commit already exists on this branch.
        subject = _run(["git", "log", "-1", "--pretty=%s"], cwd=root,
                       action="git log").strip()
        if subject == commit_message.splitlines()[0][:120]:
            sha = _run(["git", "rev-parse", "HEAD"], cwd=root,
                       action="git rev-parse").strip()
            title = str(pr_title or "").strip() or subject
            return {
                "bridge": BRIDGE_ID, "action": "draft", "branch": branch,
                "base_branch": base_branch, "commit_sha": sha, "files": written,
                "pr_title": title[:200], "pr_body": str(pr_body or "").strip()[:8000],
                "test_summary": _summary_line(test_summary) or str(test_summary or "").strip()[:300],
                "source": "git-only", "reused": True,
                "summary": _three_line(
                    [f"branch: {branch}", f"commit: {sha[:8]} (mevcut)",
                     f"PR taslağı: {title}"], "Mevcut draft kullanıldı."),
            }
        raise GithubError("Commit edilecek değişiklik yok.")

    env = _identity_env()
    _run(["git", "commit", "-m", commit_message], cwd=root, env=env,
         action="git commit")
    sha = _run(["git", "rev-parse", "HEAD"], cwd=root, action="git rev-parse").strip()
    subject = commit_message.splitlines()[0][:120]
    title = str(pr_title or "").strip() or subject
    body = str(pr_body or "").strip() or _default_pr_body(
        written or staged.splitlines(), commit_message, test_summary)
    return {
        "bridge": BRIDGE_ID,
        "action": "draft",
        "branch": branch,
        "base_branch": base_branch,
        "commit_sha": sha,
        "files": written,
        "pr_title": title[:200],
        "pr_body": body[:8000],
        "test_summary": _summary_line(test_summary) or str(test_summary or "").strip()[:300],
        "source": "git-only",
        "summary": _three_line(
            [f"branch: {branch}", f"commit: {sha[:8]} · {subject}",
             f"PR taslağı: {title}"],
            "Draft tamamlandı."),
    }


def _default_pr_body(files, message: str, test_summary: str) -> str:
    lines = ["## Özet", "", str(message).strip(), "", "## Değişen dosyalar", ""]
    lines.extend(f"- `{name}`" for name in list(dict.fromkeys(files))[:30])
    summary = _summary_line(test_summary) or str(test_summary or "").strip()
    if summary:
        lines.extend(["", "## Test", "", f"```\n{summary}\n```"])
    return "\n".join(lines)


# -------------------------------------------------------------------------- apply


def apply(workspace, *, branch: str, remote: str = "origin", pr_title: str = "",
          pr_body: str = "", test_summary: str = "", create_pr: bool = True,
          force: bool = False) -> dict:
    """Push the branch, then open a PR when ``gh`` is available."""
    root = _root(workspace)
    _git_dir(root)
    branch = _validate_branch(branch, remote)
    remote = str(remote or "origin").strip()
    if not remote or FORBIDDEN_BRANCH.search(remote):
        raise GithubError("Geçersiz remote adı.")
    remotes = _run(["git", "remote"], cwd=root, action="git remote").split()
    if remote not in remotes:
        raise ExternalGithubError(f"remote-unavailable: '{remote}' tanımlı değil.")

    current = _branch_name(root)
    if current != branch:
        raise GithubError(f"Önce '{branch}' dalına geçin (şu an: {current}).")
    sha = _run(["git", "rev-parse", "HEAD"], cwd=root, action="git rev-parse").strip()

    push = ["git", "push", "--set-upstream", remote, branch]
    if force:
        push.insert(2, "--force-with-lease")
    _run(push, cwd=root, timeout=PUSH_TIMEOUT, action="git push")

    pr_url = ""
    pr_error = ""
    source = "git-only"
    if create_pr and gh_available():
        source = "gh"
        title = str(pr_title or "").strip() or _last_subject(root)
        body = str(pr_body or "").strip()
        try:
            out = _run(["gh", "pr", "create", "--head", branch, "--title", title,
                        "--body", body or title], cwd=root, action="gh pr create")
            pr_url = _extract_url(out.strip())
        except GithubError as exc:
            # The push already happened; report the PR failure honestly.
            pr_error = str(exc)[:600]
    elif create_pr:
        pr_error = "gh yok; PR açılmadı (yalnız push yapıldı)."
    else:
        pr_error = "PR istenmedi (create_pr=False)."

    return {
        "bridge": BRIDGE_ID,
        "action": "apply",
        "branch": branch,
        "remote": remote,
        "commit_sha": sha,
        "pr_url": pr_url,
        "pr_error": pr_error,
        "test_summary": _summary_line(test_summary) or str(test_summary or "").strip()[:300],
        "source": source,
        "summary": _three_line(
            [f"push: {remote}/{branch}", f"commit: {sha[:8]}",
             f"PR: {pr_url or pr_error or 'yok'}"],
            "Push tamamlandı."),
    }


def _last_subject(root: Path) -> str:
    try:
        return _run(["git", "log", "-1", "--pretty=%s"], cwd=root,
                    action="git log").strip() or "Piko değişikliği"
    except GithubError:
        return "Piko değişikliği"


def _extract_url(text: str) -> str:
    match = re.search(r"https?://\S+", text or "")
    return match.group(0).strip() if match else ""


def build_evidence(applied: dict) -> dict:
    return {
        "format": EVIDENCE_FORMAT,
        "bridge": BRIDGE_ID,
        "ok": bool(applied.get("commit_sha")),
        "branch": applied.get("branch", ""),
        "commit_sha": applied.get("commit_sha", ""),
        "pr_url": applied.get("pr_url", ""),
        "test_summary": applied.get("test_summary", ""),
        "source": applied.get("source", "git-only"),
        "remote": applied.get("remote", ""),
        "pr_error": applied.get("pr_error", ""),
        "summary": applied.get("summary", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


class GithubAdapter:
    bridge_id = BRIDGE_ID
    levels = ("read", "draft", "apply")

    def read(self, workspace, *, mode: str = "history", **kwargs) -> dict:
        if mode == "diff":
            return diff_summary(workspace, **kwargs)
        if mode == "issues":
            return list_issues_and_prs(workspace, **kwargs)
        return history(workspace, **kwargs)

    def draft(self, workspace, **kwargs) -> dict:
        return draft(workspace, **kwargs)

    def apply(self, workspace, **kwargs) -> dict:
        return apply(workspace, **kwargs)


def adapter() -> GithubAdapter:
    return GithubAdapter()


def manifest() -> dict:
    path = Path(__file__).resolve().parents[2] / "bridge_packs" / BRIDGE_ID / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def register(registry) -> dict:
    return registry.register(manifest(), replace=True)


MANAGER_PERSONA = {"role": "scheduler",
                   "bridges": {BRIDGE_ID: {"levels": ["read", "draft"]}}}
APPROVER_PERSONA = {"role": "implementer",
                    "bridges": {BRIDGE_ID: {"levels": ["read", "draft", "apply"]}}}
CODER_PERSONA = {"role": "reviewer", "bridges": {BRIDGE_ID: {"levels": ["read"]}}}
