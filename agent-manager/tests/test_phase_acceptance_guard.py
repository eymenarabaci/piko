"""Acceptance cannot override missing work or negative independent reviews."""
from copy import deepcopy

import pytest

from manager.program import (
    AUDIT_GOAL_BUDGET, AUDIT_PROMPT_BUDGET, ProgramItem,
    build_completion_audit_prompt, phase_acceptance_issues,
)


def run_with(*, persona="developer", allow_writes=True, review=None):
    result = {"status": "completed", "output": "agent claims success"}
    if review is not None:
        result["independent_review"] = review
    return {"allow_writes": allow_writes,
            "plan": {"steps": [{"id": "fix", "persona": persona, "dependencies": []}]},
            "results": {"fix": result}}


@pytest.mark.parametrize("status", ["unavailable", "fail", "needs_user_test", "unknown"])
@pytest.mark.parametrize("persona", ["developer", "reviewer"])
def test_existing_negative_review_blocks_even_read_only_steps(status, persona):
    issues = phase_acceptance_issues(run_with(persona=persona, review={"status": status}))
    assert any(f"independent_review={status}" in issue for issue in issues)


@pytest.mark.parametrize("persona", ["developer", "feature_developer", "frontend_architect", ""])
def test_legacy_writer_without_review_cannot_be_accepted(persona):
    assert any("independent_review=missing" in issue
               for issue in phase_acceptance_issues(run_with(persona=persona)))


@pytest.mark.parametrize("persona", ["reviewer", "qa_tester", "security_auditor", "architect", "researcher", "analyzer"])
def test_read_only_personas_need_no_legacy_review(persona):
    assert phase_acceptance_issues(run_with(persona=persona)) == []


def test_allow_writes_false_does_not_require_writer_review():
    assert phase_acceptance_issues(run_with(allow_writes=False)) == []


def test_passing_independent_review_allows_completed_writer_and_does_not_mutate():
    run = run_with(review={"status": "pass"})
    original = deepcopy(run)
    assert phase_acceptance_issues(run) == []
    assert run == original


@pytest.mark.parametrize("status", [None, "running", "failed", "skipped", "waiting_quota", "success"])
def test_incomplete_dependency_cannot_be_overridden_by_passing_child_review(status):
    run = run_with(review={"status": "pass"})
    run["plan"]["steps"][0]["dependencies"] = ["parent"]
    if status is not None:
        run["results"]["parent"] = {"status": status}
    assert any("dependency parent" in issue for issue in phase_acceptance_issues(run))


def test_incomplete_plan_step_blocks_even_with_independent_pass():
    run = run_with(review={"status": "pass"})
    run["results"]["fix"]["status"] = "failed"
    assert any("adım tamamlanmadı" in issue for issue in phase_acceptance_issues(run))


@pytest.mark.parametrize("review", ["pass", {"verdict": "pass"}, {"status": ""}])
def test_malformed_review_is_not_success(review):
    assert phase_acceptance_issues(run_with(review=review))


def test_missing_and_malformed_plans_fail_closed():
    for run in [{}, {"plan": {"steps": []}}, {"plan": {"steps": [None]}}]:
        assert phase_acceptance_issues(run)


def test_audit_preserves_request_head_and_tail_and_demands_real_evidence():
    original = "HEAD: asıl hedefi tamamla" + " orta bağlam" * 1500 + "TAIL: güvenlik kontrolü olmadan teslim etme"
    item = ProgramItem(title="İş", goal="Hedefi tamamla")
    prompt = build_completion_audit_prompt(original, item, {
        "fix": {"status": "completed", "output": "rapor" * 15000,
                "independent_review": {"status": "pass"}}
    })
    assert original[:30] in prompt and original[-40:] in prompt
    excerpt = prompt.split("Ana istek: ", 1)[1].split("\nİş dilimi:", 1)[0]
    assert len(excerpt) <= AUDIT_GOAL_BUDGET
    assert len(prompt) <= AUDIT_PROMPT_BUDGET
    assert "mümkünse" not in prompt
    assert "gerçek kanıt zorunludur" in prompt
    assert "accept seçme" in prompt


def test_short_original_request_is_preserved_verbatim():
    original = "Kısa görev; sonunda test kanıtını göster."
    prompt = build_completion_audit_prompt(original, ProgramItem(title="İş", goal=original), {})
    assert original in prompt
