"""Pack-independent evidence inspection for delivery cards and acceptance.

The bridge contract fixes the evidence *shape* per pack, but the work record
stores evidence as human-readable lines. This module turns those lines into a
small, comparable object the UI can render and the acceptance gate can trust:

* every bridge pack declares which facts are mandatory (files, sha256,
  summary, branch, commit, recipients, ...)
* a deliverable is acceptable only when none of those facts is missing
* the card shows short hashes, the summary and any link/branch verbatim

Nothing here talks to a pack adapter, so a new pack only needs a rule entry.
"""
from __future__ import annotations

import re

BRIDGE_ORDER = ("office", "github", "communication")

_SHA256 = re.compile(r"\b[0-9a-f]{12,64}\b", re.IGNORECASE)
_LINK = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
_BRANCH = re.compile(r"branch[:\s]+([^\s·,]+)", re.IGNORECASE)


def evidence_lines(deliverable: dict | None) -> list[str]:
    if not deliverable:
        return []
    return [str(line).strip() for line in deliverable.get("evidence") or []
            if str(line).strip()]


def bridge_for(deliverable: dict | None) -> str:
    """Resolve the pack behind a deliverable, including older office records."""
    if not deliverable:
        return ""
    bridge = str(deliverable.get("bridge_id") or "").strip()
    if bridge:
        return bridge
    kind = str(deliverable.get("kind") or "").strip().lower()
    for name in BRIDGE_ORDER:
        if kind.startswith(name):
            return name
    return ""


def _short_hashes(lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        for match in _SHA256.findall(line):
            short = match.lower()[:12]
            if short not in found:
                found.append(short)
    return found


def _links(lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        for match in _LINK.findall(line):
            if match not in found:
                found.append(match)
    return found


def _branch(lines: list[str]) -> str:
    for line in lines:
        match = _BRANCH.search(line)
        if match:
            return match.group(1)
    return ""


def _missing(bridge: str, deliverable: dict, lines: list[str]) -> list[str]:
    text = "\n".join(lines).lower()
    note = str(deliverable.get("note") or "").strip()
    files = [str(item) for item in deliverable.get("files") or [] if str(item).strip()]
    missing: list[str] = []

    if bridge == "office":
        if not files:
            missing.append("files")
        if "sha256" not in text:
            missing.append("sha256")
        if not note and "özet:" not in text and "ozet:" not in text:
            missing.append("summary")
    elif bridge == "github":
        if "branch" not in text:
            missing.append("branch")
        if "commit" not in text:
            missing.append("commit_sha")
        if not re.search(r"\bpr\b", text):
            missing.append("pr_url")
        if "github:" not in text:
            missing.append("source")
    elif bridge == "communication":
        if not re.search(r"\b(mail|meeting|toplantı|toplanti):\s*\S+", text):
            missing.append("message_id")
        if "alıcılar" not in text and "alicilar" not in text:
            missing.append("recipients")
        if "sha256" not in text:
            missing.append("preview_sha256")
        if not re.search(r"\b(mail|meeting|toplantı|toplanti):", text):
            missing.append("kind")
    elif not lines:
        missing.append("evidence")
    return missing


def inspect(deliverable: dict | None) -> dict:
    """Return the normalized evidence view used by cards and the accept gate."""
    lines = evidence_lines(deliverable)
    bridge = bridge_for(deliverable)
    missing = _missing(bridge, deliverable or {}, lines)
    summary = str((deliverable or {}).get("note") or "").strip()
    if not summary:
        for line in lines:
            lowered = line.lower()
            if lowered.startswith("özet:") or lowered.startswith("ozet:"):
                summary = line.split(":", 1)[1].strip()
                break
    if not summary and lines:
        summary = lines[0]
    return {
        "bridge": bridge,
        "complete": not missing,
        "missing": missing,
        "lines": lines,
        "summary": summary,
        "short_hashes": _short_hashes(lines),
        "links": _links(lines),
        "branch": _branch(lines),
    }
