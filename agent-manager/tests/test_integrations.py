import pytest

from manager.integrations import IntegrationGate
from manager.operations import OperationLedger


async def _gate(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    await ledger.open()
    gate = IntegrationGate(tmp_path / "data", workspace, ledger=ledger)
    await gate.open()
    return gate, ledger, workspace


async def test_read_permission_and_path_traversal(tmp_path):
    gate, ledger, workspace = await _gate(tmp_path)
    (workspace / "notes.txt").write_text("merhaba", encoding="utf-8")
    assert (await gate.read_file("notes.txt", role="reviewer"))["content"] == "merhaba"
    with pytest.raises(PermissionError):
        await gate.read_file("../secret.txt", role="reviewer")
    with pytest.raises(PermissionError):
        await gate.create_draft(service="local_files", target="x.txt", content="x",
                                role="reviewer")
    await gate.close()
    await ledger.close()


async def test_draft_is_not_applied_before_confirmation(tmp_path):
    gate, ledger, workspace = await _gate(tmp_path)
    draft = await gate.create_draft(service="local_files", target="out/notes.txt",
                                    content="onaylı içerik", role="implementer")
    assert not (workspace / "out" / "notes.txt").exists()

    confirmation = await gate.request_confirmation(draft["id"])
    assert confirmation["content"] == "onaylı içerik"
    assert confirmation["target"] == "out/notes.txt"

    with pytest.raises(PermissionError):
        await gate.confirm_and_apply(draft["id"], "wrong-id", role="implementer")

    applied = await gate.confirm_and_apply(draft["id"], confirmation["confirmation_id"],
                                           content_hash=confirmation["content_hash"],
                                           role="implementer")
    assert applied["applied"] is True
    assert (workspace / "out" / "notes.txt").read_text(encoding="utf-8") == "onaylı içerik"

    replay = await gate.confirm_and_apply(draft["id"], confirmation["confirmation_id"],
                                          role="implementer")
    assert replay["replayed"] is True
    await gate.close()
    await ledger.close()


async def test_external_send_without_connector_reports_failure(tmp_path):
    gate, ledger, workspace = await _gate(tmp_path)
    draft = await gate.create_draft(service="email", target="a@example.test",
                                    content="Merhaba", role="implementer")
    confirmation = await gate.request_confirmation(draft["id"])
    result = await gate.confirm_and_apply(draft["id"], confirmation["confirmation_id"],
                                          role="implementer")
    assert result["applied"] is False
    assert "yapılandırılmadı" in result["error"]
    operations = await ledger.list()
    assert operations[0]["status"] == "failed"
    await gate.close()
    await ledger.close()


async def test_registry_has_permission_levels(tmp_path):
    gate, ledger, workspace = await _gate(tmp_path)
    services = {item["service"]: item for item in gate.registry()}
    assert services["local_files"]["available"] is True
    assert services["email"]["levels"] == ["read", "draft", "apply"]
    assert services["github"]["available"] is False
    await gate.close()
    await ledger.close()
