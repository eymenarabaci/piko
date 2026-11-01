"""End-to-end github flow: gate -> ledger -> draft -> approval -> push -> delivery."""
from __future__ import annotations

import hashlib
import secrets

from . import github


def draft_payload(request: dict) -> dict:
    """Draft in place: local branch + commit + PR text. Never pushes."""
    if not isinstance(request, dict):
        raise github.GithubError("Taslak isteği bir nesne olmalı.")
    return github.draft(request.get("workspace", ""), **{
        key: value for key, value in request.items() if key != "workspace"})


def issue_approval(draft: dict) -> dict:
    """Local approval bound to the exact branch + commit the user reviewed."""
    material = f"{draft['branch']}|{draft['commit_sha']}|{draft['pr_title']}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return {
        "approval_token": f"github-{secrets.token_hex(16)}",
        "draft_hash": digest,
        "branch": draft["branch"],
        "commit_sha": draft["commit_sha"],
        "message": ("Onay yalnız bu branch/commit için geçerlidir; push ve PR "
                    "birlikte gösterildi."),
    }


def evidence_lines(evidence: dict) -> list[str]:
    lines = [f"github: {evidence['source']} · branch {evidence['branch']}",
             f"commit {evidence['commit_sha'][:12] or '—'}",
             f"PR {evidence['pr_url'] or evidence['pr_error'] or 'yok'}"]
    if evidence.get("test_summary"):
        lines.append(f"test: {evidence['test_summary']}")
    return lines


async def deliver(engine, *, persona, request: dict, approval_token: str = "",
                  idempotency_key: str = "", work_id: str = "", run_id: str = "",
                  workspace: str = "", create_pr: bool = True,
                  draft_only: bool = False) -> dict:
    """Run the approved github delivery through the real bridge gate."""
    await engine.authorize_bridge(persona, github.BRIDGE_ID, "draft", payload=request)

    try:
        draft = draft_payload({**request, "workspace": workspace})
    except github.GithubError as exc:
        return {"ok": False, "error": github.error_payload(exc)}

    if draft_only:
        return {"ok": True, "draft_only": True, "draft": draft,
                "approval": issue_approval(draft)}
    levels = ((persona or {}).get("bridges", {}).get(github.BRIDGE_ID, {})
              if isinstance(persona, dict) else {})
    granted = levels.get("levels") if isinstance(levels, dict) else levels
    apply_persona = persona if granted and "apply" in granted else github.APPROVER_PERSONA

    decision = await engine.authorize_bridge(
        apply_persona, github.BRIDGE_ID, "apply",
        approval_token=approval_token,
        idempotency_key=idempotency_key or f"github:{draft['commit_sha']}",
        payload={"branch": draft["branch"], "commit_sha": draft["commit_sha"],
                 "pr_title": draft["pr_title"]})

    if decision.get("replayed") or decision.get("should_execute") is False:
        existing = None
        if engine.work is not None and work_id:
            existing = await engine.work.find_deliverable(
                work_id, idempotency_key or f"github:{draft['commit_sha']}")
        return {"replayed": True, "draft": draft, "decision": decision,
                "deliverable": existing,
                "evidence": (existing or {}).get("evidence", [])}

    try:
        applied = github.apply(
            workspace or engine.default_workspace,
            branch=draft["branch"],
            pr_title=draft["pr_title"], pr_body=draft["pr_body"],
            test_summary=draft.get("test_summary", ""), create_pr=create_pr)
    except Exception as exc:
        payload = github.error_payload(exc)
        await engine.finish_bridge(decision, error=payload["message"])
        return {"ok": False, "error": payload, "draft": draft, "decision": decision}

    evidence = github.build_evidence(applied)
    result = await engine.finish_bridge(decision, result=evidence)
    deliverable = None
    if engine.work is not None and work_id:
        deliverable = await engine.work.add_deliverable(
            work_id, run_id=run_id,
            title=f"GitHub değişikliği · {draft['pr_title']}",
            kind="github-change", bridge_id=github.BRIDGE_ID,
            summary=f"branch {draft['branch']} · commit {draft['commit_sha'][:10]}"
                    + (f" · PR {evidence['pr_url']}" if evidence["pr_url"] else ""),
            requirements=[draft["pr_title"]],
            evidence=evidence_lines(evidence),
            open_points=[] if evidence["pr_url"] else [evidence["pr_error"] or "PR yok."],
            files=[draft["branch"], draft["commit_sha"]],
            idempotency_key=idempotency_key or f"github:{draft['commit_sha']}",
            note=evidence["summary"] if "summary" in evidence else "")
    return {"ok": True, "draft": draft, "decision": decision, "evidence": evidence,
            "deliverable": deliverable, "operation": result,
            "applied": applied}
