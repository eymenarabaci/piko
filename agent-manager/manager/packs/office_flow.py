"""End-to-end office flow: gate -> ledger -> adapter -> evidence -> delivery."""
from __future__ import annotations

import hashlib
import secrets

from . import office


def draft_payload(request: dict) -> dict:
    """Build the draft without touching disk (manager level: read + draft)."""
    return office.build_draft(request)


def issue_approval(draft: dict) -> dict:
    """Local user approval: a single-use token bound to the draft content hash."""
    digest = hashlib.sha256(
        "|".join(f"{name}:{value}" for name, value in sorted(draft["hashes"].items()))
        .encode("utf-8")).hexdigest()
    token = f"office-{secrets.token_hex(16)}"
    return {
        "approval_token": token,
        "draft_hash": digest,
        "files": sorted(draft["hashes"]),
        "message": ("Onay yalnız bu taslak içeriği için geçerlidir; hedef klasör "
                    "ve dosya listesi birlikte gösterildi."),
    }


def evidence_lines(applied: dict) -> list[str]:
    evidence = office.build_evidence(applied)
    lines = [f"office: {len(evidence['files'])} dosya, hedef {evidence['target']}"]
    for item in evidence["files"]:
        lines.append(f"{item['path']} · sha256 {item['sha256'][:16]}… "
                     f"({item['bytes']} bayt)")
    summary = [line for line in str(evidence["summary"]).splitlines() if line.strip()]
    lines.append("özet: " + " / ".join(summary[:3]))
    return lines


async def deliver(engine, *, persona, request: dict, target: str,
                  approval_token: str = "", idempotency_key: str = "",
                  work_id: str = "", run_id: str = "", workspace: str = "") -> dict:
    """Run the approved office delivery through the real bridge gate.

    1. ``draft`` is authorized for the caller persona (manager: read+draft).
    2. ``apply`` is authorized with a single-use token and recorded in the ledger.
    3. The adapter writes the files; evidence is built.
    4. A versioned deliverable is stored on the work record.
    """
    draft_decision = await engine.authorize_bridge(
        persona, office.BRIDGE_ID, "draft", payload=request)
    draft = draft_payload(request)

    apply_persona = persona
    levels = (persona or {}).get("bridges", {}).get(office.BRIDGE_ID, {}) \
        if isinstance(persona, dict) else {}
    granted = levels.get("levels") if isinstance(levels, dict) else levels
    if not granted or "apply" not in granted:
        apply_persona = office.APPROVER_PERSONA

    decision = await engine.authorize_bridge(
        apply_persona, office.BRIDGE_ID, "apply",
        approval_token=approval_token, idempotency_key=idempotency_key,
        payload={"target": target, "hashes": draft["hashes"]})

    if decision.get("replayed") or decision.get("should_execute") is False:
        existing = None
        if engine.work is not None and work_id:
            existing = await engine.work.find_deliverable(work_id, idempotency_key)
        return {"replayed": True, "draft": draft, "decision": decision,
                "deliverable": existing,
                "evidence": (existing or {}).get("evidence", [])}

    try:
        applied = office.adapter().apply(workspace or engine.default_workspace,
                                         target, draft["files"])
    except Exception as exc:
        payload = office.error_payload(exc)
        await engine.finish_bridge(decision, error=payload["message"])
        return {"ok": False, "error": payload, "draft": draft, "decision": decision}
    else:
        evidence = office.build_evidence(applied)

    result = await engine.finish_bridge(decision, result=evidence)
    deliverable = None
    if engine.work is not None and work_id:
        deliverable = await engine.work.add_deliverable(
            work_id, run_id=run_id,
            title=f"Office raporu · {draft['title']}",
            kind="office-report",
            summary=f"{len(evidence['files'])} dosya yazıldı: "
                    + ", ".join(item["path"] for item in evidence["files"]),
            requirements=[draft["title"]],
            evidence=evidence_lines(applied),
            open_points=[],
            files=[item["path"] for item in evidence["files"]],
            idempotency_key=idempotency_key or f"office:{draft['title']}",
            note=evidence["summary"])
    return {"ok": True, "draft": draft, "decision": decision, "evidence": evidence,
            "deliverable": deliverable, "operation": result,
            "draft_decision": draft_decision}
