"""Read-only startup recovery: one decision question per unknown operation.

Recovery never replays, repairs or re-runs an external effect on its own. A crash
can leave an operation whose result is unknown; the only safe next step is to ask
the user one question so they can decide what actually happened in the outside
system. Wiring the answer back to the ledger is an explicit user action.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


ANSWER_OPTIONS = ["applied", "not_applied", "manual_check"]
ANSWER_LABELS = {
    "applied": "Dış sistemde uygulandı; 'applied' olarak işaretle.",
    "not_applied": "Dış sistemde uygulanmadı; 'failed' olarak işaretle.",
    "manual_check": "Kontrol edeceğim; şimdilik hiçbir şeyi değiştirme.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_result(value):
    if isinstance(value, str):
        try:
            return json.loads(value) if value else None
        except ValueError:
            return value
    return value


def format_question(operation: dict) -> str:
    """Exactly one question, so the user is never asked to resolve a cascade."""
    kind = str(operation.get("kind") or "dış işlem")
    key = str(operation.get("idempotency_key") or operation.get("id") or "").strip()
    suffix = f" (anahtar: {key})" if key else ""
    return (f"Kapanıştan önce '{kind}' işlemi{suffix} dış sistemde uygulandı mı? "
            "Yalnız bu işlem için bir kez karar verin.")


def proposal_for(operation: dict) -> dict:
    """A suggestion only; no ledger row and no work item is changed here."""
    operation_id = str(operation.get("id") or "")
    return {
        "kind": "recovery_decision",
        "operation_id": operation_id,
        "operation_kind": str(operation.get("kind") or ""),
        "idempotency_key": str(operation.get("idempotency_key") or ""),
        "status": str(operation.get("status") or ""),
        "attempts": int(operation.get("attempts") or 0),
        "created_at": str(operation.get("created_at") or ""),
        "question": format_question(operation),
        "options": list(ANSWER_OPTIONS),
        "labels": dict(ANSWER_LABELS),
        "recommended": "manual_check",
        "note": ("Öneri yalnızca bilgilendirme içindir; sürücü hiçbir işlemi "
                 "kendiliğinden yeniden çalıştırmaz veya düzeltmez."),
        "proposed_at": _now(),
    }


async def collect_proposals(ledger, *, limit: int = 100) -> list[dict]:
    """Return one proposal per 'unknown' operation, in creation order.

    Reading is idempotent: repeated calls return the same proposals and never
    mutate the ledger. Applied/failed operations are ignored.
    """
    records = await ledger.list(limit=limit)
    unknown = [record for record in records if record.get("status") == "unknown"]
    unknown.sort(key=lambda record: (str(record.get("created_at") or ""),
                                     str(record.get("id") or "")))
    return [proposal_for(record) for record in unknown]


def summarize(proposals) -> dict:
    return {
        "unknown_count": len(proposals),
        "question_count": len(proposals),
        "operation_ids": [proposal["operation_id"] for proposal in proposals],
        "requires_user": bool(proposals),
        "auto_repaired": 0,
    }


async def apply_user_answer(ledger, operation_id: str, answer: str,
                            *, note: str = "") -> dict:
    """Apply an explicitly supplied user answer; never called by the daemon.

    'manual_check' deliberately leaves the record untouched so the user can
    inspect the outside system first.
    """
    answer = str(answer or "").strip()
    if answer not in ANSWER_OPTIONS:
        raise ValueError("Geçersiz kurtarma yanıtı.")
    operation = await ledger.get(operation_id)
    if operation is None:
        raise ValueError("İşlem bulunamadı.")
    if operation["status"] != "unknown":
        return {"operation": operation, "changed": False, "answer": answer}
    if answer == "applied":
        updated = await ledger.complete(
            operation_id, {"recovered_by": "user_answer", "note": str(note)[:2000]})
        changed = True
    elif answer == "not_applied":
        reason = (f"Kullanıcı dış işlemin uygulanmadığını bildirdi. "
                  f"{str(note)[:1800]}").strip()
        updated = await ledger.fail(operation_id, reason)
        changed = True
    else:
        updated = operation
        changed = False
    return {"operation": updated, "changed": changed, "answer": answer}
