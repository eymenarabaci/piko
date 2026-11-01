import pytest

from manager.operations import (BudgetExceeded, OperationConflict, OperationLedger,
                                OperationUncertain, allowed_tool, require_tool)


async def test_applied_operation_is_not_executed_twice(tmp_path):
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    await ledger.open()
    first = await ledger.begin("github:create_issue", "issue:42", {"title": "x"})
    assert first["should_execute"] is True
    await ledger.complete(first["id"], {"url": "https://example.test/42"})

    replay = await ledger.begin("github:create_issue", "issue:42", {"title": "x"})
    assert replay["should_execute"] is False
    assert replay["replayed"] is True
    assert replay["result"]["url"].endswith("/42")
    await ledger.close()


async def test_conflicting_payload_is_rejected(tmp_path):
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    await ledger.open()
    await ledger.begin("email:send", "mail:1", {"to": "a@example.test"})
    with pytest.raises(OperationConflict):
        await ledger.begin("email:send", "mail:1", {"to": "b@example.test"})
    await ledger.close()


async def test_restart_marks_pending_as_unknown(tmp_path):
    path = tmp_path / "ops.sqlite"
    ledger = OperationLedger(path)
    await ledger.open()
    operation = await ledger.begin("drive:publish", "doc:7", {"id": 7})
    await ledger.close()

    reopened = OperationLedger(path)
    await reopened.open()
    saved = await reopened.get(operation["id"])
    assert saved["status"] == "unknown"
    with pytest.raises(OperationUncertain):
        await reopened.begin("drive:publish", "doc:7", {"id": 7})
    await reopened.close()


async def test_budgets_stop_work(tmp_path):
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    await ledger.open()
    await ledger.configure_budget("run1", token_limit=100, seconds_limit=10, repairs_limit=1)
    await ledger.consume("run1", tokens=60, seconds=4)
    with pytest.raises(BudgetExceeded):
        await ledger.consume("run1", tokens=50)
    await ledger.consume("run1", repairs=1)
    with pytest.raises(BudgetExceeded):
        await ledger.consume("run1", repairs=1)
    with pytest.raises(BudgetExceeded):
        await ledger.consume("run1", seconds=7)
    await ledger.close()


def test_role_tool_permissions():
    assert allowed_tool("reviewer", "read")
    assert not allowed_tool("reviewer", "write")
    assert allowed_tool("implementer", "apply")
    with pytest.raises(PermissionError):
        require_tool("reviewer", "apply")
