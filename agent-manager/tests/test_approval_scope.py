from pathlib import Path

import pytest

from manager.operations import OperationLedger
from manager.bridge_gate import BridgeGate, ApprovalRequired
from manager.bridges import BridgeRegistry
from manager.packs import office, office_flow


def gate_for(ledger=None):
    registry = BridgeRegistry()
    office.register(registry)
    return BridgeGate(registry, token_store=ledger)


@pytest.mark.asyncio
async def test_only_issued_matching_unexpired_approval_can_be_consumed(tmp_path):
    ledger = OperationLedger(tmp_path / 'ops.sqlite')
    await ledger.open()
    try:
        gate = gate_for(ledger)
        payload = {'workspace': str(tmp_path), 'target': 'reviewed', 'hashes': {'a': '123'}}
        async def apply(token, value=payload):
            return await gate.authorize(office.APPROVER_PERSONA, 'office', 'apply',
                approval_token=token, idempotency_key='one', payload=value)
        with pytest.raises(ApprovalRequired):
            await apply('fabricated')
        token = await gate.issue_approval_async('office', 'apply', payload=payload)
        with pytest.raises(ApprovalRequired):
            await apply(token, {**payload, 'target': 'changed'})
        with pytest.raises(ApprovalRequired):
            await apply(token, {**payload, 'hashes': {'a': 'different'}})
        assert not await ledger.approval_used(token)
        decision = await apply(token)
        assert decision['approval_used']
        with pytest.raises(ApprovalRequired):
            await apply(token)  # not a completed external operation yet
        operation = await ledger.begin('office', decision['ledger_key'], payload)
        await ledger.complete(operation['id'], {'ok': True})
        assert (await apply(token))['ledger_key'] == decision['ledger_key']
        with pytest.raises(ApprovalRequired):
            await gate.authorize(office.APPROVER_PERSONA, 'office', 'apply',
                approval_token=token, idempotency_key='another', payload=payload)
        expired = await gate.issue_approval_async('office', 'apply', payload=payload)
        await ledger.db.execute("UPDATE issued_approvals SET expires_at='2000-01-01T00:00:00+00:00' WHERE used_at IS NULL")
        await ledger.db.commit()
        with pytest.raises(ApprovalRequired):
            await apply(expired)
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_issued_approval_survives_restart_and_is_bridge_bound(tmp_path):
    path = tmp_path / 'ops.sqlite'
    ledger = OperationLedger(path)
    await ledger.open()
    token = await ledger.issue_approval(bridge='office', level='apply', payload={'x': 1})
    await ledger.close()
    restored = OperationLedger(path)
    await restored.open()
    try:
        assert not await restored.consume_approval(token, bridge='github', level='apply', payload={'x': 1})
        assert not await restored.consume_approval(token, bridge='office', level='dangerous', payload={'x': 1})
        assert await restored.consume_approval(token, bridge='office', level='apply', payload={'x': 1})
    finally:
        await restored.close()


def test_in_memory_gate_cannot_accept_fabricated_or_changed_payload():
    gate = gate_for()
    token = gate.issue_approval('office', 'apply', payload={'target': 'a'})
    for candidate, payload in [('made-up', {'target': 'a'}), (token, {'target': 'b'})]:
        with pytest.raises(ApprovalRequired):
            gate.check(office.APPROVER_PERSONA, 'office', 'apply', approval_token=candidate,
                       idempotency_key='one', payload=payload)
    assert gate.check(office.APPROVER_PERSONA, 'office', 'apply', approval_token=token,
                      idempotency_key='one', payload={'target': 'a'})['approval_used']


def test_office_approval_payload_binds_workspace_and_normalized_target(tmp_path):
    draft = {'hashes': {'report.md': 'abc'}}
    first = office_flow.approval_payload(draft, 'reports', str(tmp_path))
    assert first['target'] == str((tmp_path / 'reports').resolve())
    assert first != office_flow.approval_payload(draft, 'reports', str(tmp_path / 'other'))
