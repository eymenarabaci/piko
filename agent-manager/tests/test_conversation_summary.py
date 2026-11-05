from manager.conversation_summary import plan_result_summary, reported_outcome


def test_outcome_keeps_behavior_and_leaves_technical_evidence_in_details():
    output = "Sonuç: Bağlantılar artık görevden açılabiliyor.\n\nTeknik kanıt:\n- src/tasks.ts\nKalan: Mobil test bekliyor."
    assert reported_outcome(output) == "Bağlantılar artık görevden açılabiliyor."


def test_plan_summary_does_not_replay_workers_or_hide_incomplete_steps():
    run = {"plan": {"steps": [{"id": "a", "title": "Database", "model_id": "secret-model"}, {"id": "b"}]}}
    summary = plan_result_summary(run, {"a": {"status": "completed", "output": "Sonuç: İzin akışını planladım; uygulamadım."}, "b": {"status": "failed"}})
    assert "planladım; uygulamadım" in summary
    assert "1 adım başarısız" in summary
    assert "Database" not in summary and "secret-model" not in summary
    assert "raporlarından" in summary


def test_empty_or_failed_plan_never_claims_success():
    for run, results in [({}, {}), ({"plan": {"steps": [{"id": "a"}]}}, {"a": {"status": "failed"}})]:
        summary = plan_result_summary(run, results)
        assert "başarıyla" not in summary and "tamamlandı" not in summary
