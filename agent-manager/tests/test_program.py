"""Sequential work-program contracts."""
import json

import pytest

from manager.program import (
    ProgramItem, build_completion_audit_prompt, build_phase_planning_prompt,
    build_program_prompt, parse_completion_audit, parse_program, single_item_program,
)


def test_single_item_fallback_preserves_request():
    program = single_item_program("  Önce API sorununu çöz, sonra demo göster.  ")
    assert len(program.items) == 1
    assert program.items[0].goal == "Önce API sorununu çöz, sonra demo göster."


def test_program_keeps_separate_jobs_and_preferences():
    payload = {"items": [
        {"title": "API", "goal": "API sorununu çöz", "acceptance": "İstek başarılı", "demo_requested": False, "schedule_hint": ""},
        {"title": "Demo", "goal": "Çalışan demoyu kullanıcıya göster", "acceptance": "Önizleme hazır", "demo_requested": True, "schedule_hint": "yarın"},
    ]}
    parsed = parse_program(json.dumps(payload, ensure_ascii=False))
    assert len(parsed.items) == 2
    assert parsed.items[1].demo_requested
    assert parsed.items[1].schedule_hint == "yarın"


@pytest.mark.parametrize("payload", [
    {"items": []},
    {"items": [{"title": f"İş {i}", "goal": f"Görev {i}"} for i in range(6)]},
    {"items": [{"title": "A", "goal": "Aynı iş"}, {"title": "B", "goal": "  aynı  iş  "}]},
    {"items": [{"title": "A", "goal": "İş", "unknown": "talimat"}]},
])
def test_invalid_or_duplicate_work_is_rejected(payload):
    with pytest.raises(ValueError):
        parse_program(json.dumps(payload, ensure_ascii=False))


def test_program_requires_only_json_and_prompt_does_not_split_one_job_into_files():
    with pytest.raises(ValueError, match="sonrasında"):
        parse_program('{"items":[{"title":"A","goal":"B"}]} devam et')
    prompt = build_program_prompt("Tek bir özellik ekle")
    assert "tam olarak 1 dilim" in prompt
    assert "Kullanıcının istemediği işleri ekleme" in prompt


def test_audit_requires_action_specific_evidence():
    with pytest.raises(ValueError, match="hedef"):
        parse_completion_audit('{"action":"repair","reason":"Eksik","repair_goal":"","question":""}')
    with pytest.raises(ValueError, match="soru"):
        parse_completion_audit('{"action":"await_user_test","reason":"Elle görmek gerek","repair_goal":"","question":""}')
    accepted = parse_completion_audit('{"action":"accept","reason":"Test geçti","repair_goal":"","question":""}')
    assert accepted.action == "accept"


def test_audit_and_next_plan_context_keep_untrusted_reports_separate():
    item = ProgramItem(title="API", goal="API sorununu çöz", acceptance="200 döner")
    audit = build_completion_audit_prompt(
        "API'yi düzelt", item, {"fix": {"status": "completed", "output": "Test yaptım"}}, "/tmp/project",
    )
    assert "raporlarını başarı kanıtı sayma" in audit
    assert '"fix"' in audit
    next_prompt = build_phase_planning_prompt(
        "API'yi düzelt ve demo yap", item,
        [{"title": "Önceki", "audit": "Düzeltme gerek", "output": "Çıktı"}],
        feedback="Endpoint hâlâ 500", queued_messages=["Kullanıcıya kısa demo göster"],
    )
    assert "Sonraki dilimleri şimdiden planlama" in next_prompt
    assert "Endpoint hâlâ 500" in next_prompt
    assert "Kullanıcıya kısa demo göster" in next_prompt
