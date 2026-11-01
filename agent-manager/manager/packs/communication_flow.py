"""End-to-end communication flow: gate -> ledger -> draft -> approval -> send -> delivery."""
from __future__ import annotations

import hashlib
import secrets

from . import communication


def draft_payload(request: dict) -> dict:
    """Build the preview locally (draft). No send happens here."""
    if not isinstance(request, dict):
        raise communication.CommunicationError("Taslak isteği bir nesne olmalı.")
    return {
        "kind": str(request.get("kind") or "mail"),
        "to": list(request.get("to") or request.get("attendees") or []),
        "subject": str(request.get("subject") or request.get("title") or ""),
        "body": str(request.get("body") or request.get("agenda") or ""),
        "start_at": str(request.get("start_at") or ""),
        "end_at": str(request.get("end_at") or ""),
    }


def _preview(request: dict) -> tuple[str, str]:
    draft = draft_payload(request)
    recipients = list(dict.fromkeys(str(item).strip() for item in draft["to"]
                                    if str(item).strip()))
    if draft["kind"] == "meeting":
        preview = "\n".join([f"Kime: {', '.join(recipients)}",
                             f"Konu: {draft['subject']}",
                             f"Zaman: {draft['start_at']} → {draft['end_at']}",
                             "", draft["body"]]).strip()
    else:
        preview = "\n".join([f"Kime: {', '.join(recipients)}",
                             f"Konu: {draft['subject']}", "", draft["body"]]).strip()
    return preview, hashlib.sha256(preview.encode("utf-8")).hexdigest()


def issue_approval(request: dict) -> dict:
    """Approval bound to the exact rendered preview the user reviewed."""
    preview, digest = _preview(request)
    draft = draft_payload(request)
    return {
        "approval_token": f"communication-{secrets.token_hex(16)}",
        "draft_hash": digest,
        "kind": draft["kind"],
        "recipients": [item for item in draft["to"] if str(item).strip()],
        "preview": preview,
        "message": ("Onay yalnız bu önizleme için geçerlidir; alıcı listesi ve "
                    "içerik birlikte gösterildi."),
    }


def evidence_lines(evidence: dict) -> list[str]:
    lines = [f"{evidence['kind']}: {evidence['message_id']}",
             f"alıcılar: {', '.join(evidence['recipients'])}",
             f"sha256(önizleme): {evidence['preview_sha256'][:16]}…"]
    if evidence.get("duplicate"):
        lines.append("not: aynı anahtar tekrar kullanıldı, mükerrer gönderim yok.")
    return lines


async def deliver(engine, *, persona, request: dict, approval_token: str = "",
                  idempotency_key: str = "", work_id: str = "", run_id: str = "",
                  workspace: str = "", draft_only: bool = False,
                  fixture_db: str = "") -> dict:
    """Run the approved communication delivery through the real bridge gate."""
    await engine.authorize_bridge(persona, communication.BRIDGE_ID, "draft",
                                  payload=request)
    draft = draft_payload(request)
    if draft_only:
        preview, digest = _preview(request)
        return {"ok": True, "draft_only": True, "draft": draft,
                "preview": preview, "preview_sha256": digest,
                "approval": issue_approval(request)}

    levels = ((persona or {}).get("bridges", {}).get(communication.BRIDGE_ID, {})
              if isinstance(persona, dict) else {})
    granted = levels.get("levels") if isinstance(levels, dict) else levels
    apply_persona = (persona if granted and "apply" in granted
                     else communication.APPROVER_PERSONA)

    decision = await engine.authorize_bridge(
        apply_persona, communication.BRIDGE_ID, "apply",
        approval_token=approval_token,
        idempotency_key=idempotency_key or f"communication:{draft['subject']}",
        payload={"kind": draft["kind"], "to": draft["to"],
                 "preview_sha256": _preview(request)[1]})

    if decision.get("replayed") or decision.get("should_execute") is False:
        existing = None
        if engine.work is not None and work_id:
            existing = await engine.work.find_deliverable(
                work_id, idempotency_key or f"communication:{draft['subject']}")
        return {"replayed": True, "draft": draft, "decision": decision,
                "deliverable": existing,
                "evidence": (existing or {}).get("evidence", [])}

    adapter = communication.adapter()
    try:
        if draft["kind"] == "meeting":
            applied = await adapter.apply(
                kind="meeting", workspace=workspace, title=draft["subject"],
                start_at=draft["start_at"], end_at=draft["end_at"],
                attendees=draft["to"],
                idempotency_key=idempotency_key or f"communication:{draft['subject']}",
                override=fixture_db)
        else:
            applied = await adapter.apply(
                kind="mail", workspace=workspace, to=draft["to"],
                subject=draft["subject"], body=draft["body"],
                idempotency_key=idempotency_key or f"communication:{draft['subject']}",
                override=fixture_db)
    except Exception as exc:
        payload = communication.error_payload(exc)
        await engine.finish_bridge(decision, error=payload["message"])
        return {"ok": False, "error": payload, "draft": draft, "decision": decision}

    evidence = communication.build_evidence(applied)
    result = await engine.finish_bridge(decision, result=evidence)
    deliverable = None
    if engine.work is not None and work_id:
        deliverable = await engine.work.add_deliverable(
            work_id, run_id=run_id,
            title=f"İletişim · {draft['subject']}",
            kind="communication-message", bridge_id=communication.BRIDGE_ID,
            summary=f"{draft['kind']} gönderildi · {len(evidence['recipients'])} alıcı",
            requirements=[draft["subject"]],
            evidence=evidence_lines(evidence),
            open_points=[],
            files=[evidence["message_id"]],
            idempotency_key=idempotency_key or f"communication:{draft['subject']}",
            note=applied.get("summary", ""))
    return {"ok": True, "draft": draft, "decision": decision, "evidence": evidence,
            "deliverable": deliverable, "operation": result, "applied": applied}
