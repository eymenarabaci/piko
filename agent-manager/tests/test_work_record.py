from manager.work_record import WorkRecordManager


async def test_work_record_survives_restart(tmp_path):
    path = tmp_path / "work.sqlite"
    manager = WorkRecordManager(path)
    await manager.open()
    item = await manager.create(
        "Raporu hazırla", title="Rapor", requirements=["Kaynakları oku"],
        acceptance=["Rapor dosyası hazır"], priority="high",
        due_at="2026-11-02T10:00:00+03:00",
        next_action="Plan hazırlanıyor.")
    decision = await manager.add_decision(item["id"], "Hangi biçim?")
    await manager.close()

    reopened = WorkRecordManager(path)
    await reopened.open()
    loaded = await reopened.get(item["id"])
    await reopened.close()

    assert loaded["goal"] == "Raporu hazırla"
    assert loaded["priority"] == "high"
    assert loaded["due_at"].startswith("2026-11-02T07:00:00")
    assert loaded["status"] == "waiting"
    assert loaded["decisions"][0]["id"] == decision["id"]


async def test_run_link_is_idempotent_and_summary_has_next_action(tmp_path):
    manager = WorkRecordManager(tmp_path / "work.sqlite")
    await manager.open()
    run = {"id": "run1", "conversation_id": "conv1", "prompt": "Kod incele"}
    first = await manager.create_for_run(run)
    second = await manager.create_for_run(run)
    assert first["id"] == second["id"]

    await manager.add_deliverable(
        first["id"], run_id="run1", summary="İnceleme tamamlandı.",
        requirements=["Bulgu listesi"], evidence=["pytest: 3 passed"])
    summary = await manager.summary(first["id"])
    assert summary["what_was_asked"] == "Kod incele"
    assert summary["what_was_done"] == "İnceleme tamamlandı."
    assert summary["latest_version"] == 1
    assert "incele" in summary["next_action"].lower()
    await manager.close()


async def test_deliverable_versions_keep_repair_lineage(tmp_path):
    manager = WorkRecordManager(tmp_path / "work.sqlite")
    await manager.open()
    item = await manager.create("Site yap")
    v1 = await manager.add_deliverable(item["id"], summary="İlk sürüm",
                                       idempotency_key="deliver-1")
    duplicate = await manager.add_deliverable(item["id"], summary="İlk sürüm",
                                              idempotency_key="deliver-1")
    assert duplicate["id"] == v1["id"]
    await manager.request_repair(v1["id"], "Başlık eksik")
    v2 = await manager.add_deliverable(item["id"], summary="Başlık eklendi")
    assert v2["version"] == 2
    assert v2["parent_version"] == 1

    await manager.accept(v2["id"], "Kabul edildi")
    work = await manager.get(item["id"])
    assert work["status"] == "done"
    await manager.close()


async def test_user_test_result_is_recorded_separately(tmp_path):
    manager = WorkRecordManager(tmp_path / "work.sqlite")
    await manager.open()
    item = await manager.create("Mobil ekranı dene")
    deliverable = await manager.add_deliverable(item["id"], summary="Ekran hazır")
    updated = await manager.record_test(deliverable["id"], False, "Buton görünmüyor")
    assert updated["status"] == "test_failed"
    work = await manager.get(item["id"])
    assert work["status"] == "active"
    assert "düzeltme" in work["next_action"].lower()
    await manager.close()
