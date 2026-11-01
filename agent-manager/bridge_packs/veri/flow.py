"""End-to-end veri flow: gate -> draft -> approval -> apply -> evidence."""
from __future__ import annotations

import hashlib
import secrets

from . import adapter


def draft_payload(request: dict) -> dict:
    """Build the analysis report without touching disk (read + draft)."""
    return adapter.build_draft(request)


def issue_approval(draft: dict) -> dict:
    """Local user approval: a single-use token bound to the draft content hash."""
    digest = hashlib.sha256(
        "|".join(f"{name}:{value}" for name, value in sorted(draft["hashes"].items()))
        .encode("utf-8")).hexdigest()
    return {
        "approval_token": f"veri-{secrets.token_hex(16)}",
        "draft_hash": digest,
        "files": sorted(draft["hashes"]),
        "message": ("Onay yalniz bu taslak icerigi icin gecerlidir; hedef klasor "
                    "ve dosya listesi birlikte gosterildi."),
    }


def evidence_lines(evidence: dict) -> list[str]:
    lines = [f"veri: {len(evidence.get('files') or [])} dosya, "
             f"hedef {evidence.get('target', '')}"]
    for item in evidence.get("files") or []:
        lines.append(f"{item['path']} - sha256 {item['sha256'][:16]}... "
                     f"({item['bytes']} bayt)")
    report = evidence.get("report") or {}
    lines.append(f"rapor: {report.get('path', '-')} - "
                 f"{report.get('rows', 0)} satir")
    summary = [line for line in str(evidence.get("summary", "")).splitlines()
               if line.strip()]
    lines.append("ozet: " + " / ".join(summary[:3]))
    return lines


def deliver(gate, *, persona, request: dict, target: str = "",
            approval_token: str = "", idempotency_key: str = "",
            workspace: str = "", overwrite: bool = False) -> dict:
    """Run the veri delivery through the real bridge gate.

    ``read`` and ``draft`` are authorized for the caller persona. ``apply`` is
    authorized with a single-use token and a keyed idempotency value before the
    adapter writes anything. Without ``target`` the call is draft-only.
    """
    workspace = str(workspace or request.get("workspace") or "")
    gate.check(persona, adapter.BRIDGE_ID, "read",
               payload={"path": request.get("path")})
    gate.check(persona, adapter.BRIDGE_ID, "draft", payload=request)
    draft = adapter.build_draft({**request, "workspace": workspace})
    if not str(target or "").strip():
        return {"ok": True, "draft_only": True, "draft": draft,
                "approval": issue_approval(draft)}

    levels = ((persona or {}).get("bridges", {}).get(adapter.BRIDGE_ID, {})
              if isinstance(persona, dict) else {})
    granted = levels.get("levels") if isinstance(levels, dict) else levels
    apply_persona = persona if granted and "apply" in granted else adapter.APPROVER_PERSONA

    decision = gate.check(
        apply_persona, adapter.BRIDGE_ID, "apply",
        approval_token=approval_token,
        idempotency_key=idempotency_key,
        payload={"target": target, "hashes": draft["hashes"]})

    try:
        applied = adapter.apply_report(
            workspace, target, draft["files"], overwrite=overwrite,
            meta={**draft["dataset"], "summary": draft["summary"]})
    except adapter.VeriError as exc:
        return {"ok": False, "error": adapter.error_payload(exc),
                "draft": draft, "decision": decision}

    evidence = adapter.build_evidence(applied)
    return {"ok": True, "draft": draft, "decision": decision,
            "evidence": evidence, "lines": evidence_lines(evidence)}
