from manager.step_context import original_task_context


def test_phase_keeps_original_continuous_records_and_scope():
    result = original_task_context({"prompt": "STATE.json ve PLAN.json ilerledikçe güncelle", "active_goal": "Motoru düzelt"}, can_write=True)
    assert "STATE.json ve PLAN.json ilerledikçe güncelle" in result
    assert "anlamlı kontrol noktalarında" in result
    assert "sonraki dilimlerini üstlenme" in result
    assert "doğrulanmış kanıt" in result


def test_readonly_contract_does_not_grant_write_access():
    result = original_task_context({"prompt": "STATE.json güncelle", "active_goal": "Denetle"}, can_write=False)
    assert "kayıt dosyalarını değiştirme" in result
    assert "kontrol noktalarında kaydet" not in result


def test_single_phase_does_not_duplicate_prompt():
    assert original_task_context({"prompt": "same", "active_goal": "same"}, can_write=True) == ""


def test_large_original_keeps_trailing_constraints_bounded():
    result = original_task_context({"prompt": "START" + "x" * 20000 + "END_RULE", "active_goal": "phase"}, can_write=True)
    assert "START" in result and "END_RULE" in result
    assert len(result) < 13000
