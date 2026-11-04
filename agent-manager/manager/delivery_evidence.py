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
import json
from pathlib import Path

def _manifests() -> dict[str, dict]:
    # Read contract metadata only. Never import or instantiate a domain adapter.
    from .bridges import DEFAULT_REGISTRY, validate_bridge_spec
    found = {spec['id']: spec for spec in DEFAULT_REGISTRY.list()}
    root = Path(__file__).resolve().parents[1] / 'bridge_packs'
    for path in root.glob('*/manifest.json'):
        try:
            spec = validate_bridge_spec(json.loads(path.read_text(encoding='utf-8')))
            found.setdefault(spec['id'], spec)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found

_SHA256 = re.compile(r"\b[0-9a-f]{12,64}\b", re.IGNORECASE)
_LINK = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
_BRANCH = re.compile(r"branch[:\s]+([^\s·,]+)", re.IGNORECASE)


def evidence_lines(deliverable: dict | None) -> list[str]:
    if not deliverable:
        return []
    return [json.dumps(line, ensure_ascii=False) if isinstance(line, dict) else str(line).strip()
            for line in deliverable.get("evidence") or [] if str(line).strip()]


def bridge_for(deliverable: dict | None) -> str:
    """Resolve the pack behind a deliverable, including older office records."""
    if not deliverable:
        return ""
    bridge = str(deliverable.get("bridge_id") or "").strip()
    if bridge:
        return bridge
    kind = str(deliverable.get("kind") or "").strip().lower()
    for name in _manifests():
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

    if not lines:
        missing.append('evidence')
    if not bridge:
        return missing
    manifest = _manifests().get(bridge)
    if manifest is None:
        return [*missing, 'bridge_manifest']
    facts = {}
    for value in deliverable.get('evidence') or []:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            if value.get('format') not in {None, manifest['evidence']['format']}:
                missing.append('evidence_format')
            if value.get('ok') is False:
                missing.append('evidence_failed')
            facts.update(value)
    # Older packs emit descriptive evidence lines. Keep those explicit facts
    # readable while deriving the required field set from the manifest.
    legacy = {
        'files': bool(files),
        'sha256': bool(re.search(r'sha256\s+[0-9a-f]{12,64}', text)),
        'summary': bool(note or re.search(r'(?:özet|ozet|summary):\s*\S', text)),
        'branch': bool(_branch(lines)),
        'commit_sha': bool(re.search(r'commit\s+[0-9a-f]{12,40}', text)),
        'pr_url': bool(re.search(r'\bpr\s+\S', text)),
        'test_summary': bool(re.search(r'\btest:\s*\S', text)),
        'source': bool(re.search(r'github:\s*\S', text)),
        'message_id': bool(re.search(r'\b(?:mail|meeting|toplantı|toplanti):\s*\S+', text)),
        'recipients': bool(re.search(r'(?:alıcılar|alicilar):\s*\S', text)),
        'preview_sha256': bool(re.search(r'sha256\(önizleme\):\s*[0-9a-f]{12,64}', text)),
        'kind': bool(re.search(r'\b(?:mail|meeting|toplantı|toplanti):', text)),
    }
    for field in manifest['evidence']['fields']:
        value = facts.get(field)
        present = value is not None and value != '' and value != [] and value != {}
        if present and ('sha256' in field):
            values = value.values() if isinstance(value, dict) else [value]
            present = bool(values) and all(re.fullmatch(r'[0-9a-f]{64}', str(v), re.I) for v in values)
        if not present:
            present = legacy.get(field, False)
        if not present and 'sha256' not in field:
            # New packs may emit field-labelled lines without a JSON object.
            present = bool(re.search(r'(?m)^' + re.escape(field) + r':\s*\S', text))
        if not present:
            missing.append(field)
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
