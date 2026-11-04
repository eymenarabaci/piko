import copy

import pytest

from manager.delivery_evidence import inspect
from manager.work_record import WorkRecordManager


PACK_EVIDENCE = {
    'veri': {'format': 'veri-evidence', 'files': [{'path': 'report.csv'}],
             'sha256': {'report.csv': 'a' * 64}, 'summary': 'Analiz sonucu',
             'report': {'path': 'report.md'}},
    'izleme': {'format': 'izleme-evidence', 'target': 'https://example.test', 'status': 200,
               'latency_samples': [12], 'window_start': '2026-11-01T00:00:00Z',
               'window_end': '2026-11-01T00:01:00Z', 'threshold': {'latency': 100},
               'snapshot_sha256': 'a' * 64},
    'medya': {'format': 'medya-evidence', 'source_sha256': 'a' * 64,
              'files': [{'path': 'preview.png'}], 'preview_frame': {'path': 'preview.png'},
              'summary': 'Kare üretildi', 'capability_used': 'ffmpeg'},
}


@pytest.mark.parametrize('bridge', list(PACK_EVIDENCE))
def test_every_new_pack_manifest_field_is_enforced(bridge):
    evidence = PACK_EVIDENCE[bridge]
    assert inspect({'bridge_id': bridge, 'evidence': [evidence]})['complete']
    for field in evidence:
        if field == 'format':
            continue
        incomplete = copy.deepcopy(evidence)
        incomplete.pop(field)
        view = inspect({'bridge_id': bridge, 'evidence': [incomplete]})
        assert not view['complete'], field
        assert field in view['missing'], field


def test_unknown_manifest_failed_and_invalid_hash_evidence_is_not_accepted():
    assert 'bridge_manifest' in inspect({'bridge_id': 'not-installed', 'evidence': ['ok']})['missing']
    malformed = copy.deepcopy(PACK_EVIDENCE['veri'])
    malformed['sha256'] = {'report.csv': 'not-a-sha'}
    assert 'sha256' in inspect({'bridge_id': 'veri', 'evidence': [malformed]})['missing']
    malformed['sha256'] = {'report.csv': 'a' * 64}
    malformed['ok'] = False
    assert 'evidence_failed' in inspect({'bridge_id': 'veri', 'evidence': [malformed]})['missing']


async def test_empty_generic_delivery_cannot_be_published_or_accepted(tmp_path):
    work = WorkRecordManager(tmp_path / 'work.sqlite')
    await work.open()
    try:
        item = await work.create('Rapor hazırla')
        with pytest.raises(ValueError, match='Kanıt eksik'):
            await work.add_deliverable(item['id'], summary='Bitti')
        draft = await work.add_deliverable(item['id'], summary='Taslak', status='draft')
        assert not draft['can_accept']
        with pytest.raises(ValueError):
            await work.accept(draft['id'])
        await work.db.execute("UPDATE deliverables SET status='delivered' WHERE id=?", (draft['id'],))
        await work.db.commit()
        with pytest.raises(ValueError, match='Kanıt eksik'):
            await work.accept(draft['id'])
        assert (await work.get(item['id']))['status'] != 'done'
    finally:
        await work.close()


async def test_only_latest_finished_run_delivery_can_close_work(tmp_path):
    work = WorkRecordManager(tmp_path / 'work.sqlite')
    await work.open()
    try:
        item = await work.create('Site yap', run_id='r1')
        v1 = await work.add_deliverable(item['id'], run_id='r1', summary='v1', evidence=['test: ana sayfa 200'])
        await work.request_repair(v1['id'], 'Başlığı düzelt')
        await work.update(item['id'], run_id='r2', status='active')
        assert not (await work.get_deliverable(v1['id']))['can_accept']
        with pytest.raises(ValueError):
            await work.accept(v1['id'])
        v2 = await work.add_deliverable(item['id'], run_id='r2', summary='v2', evidence=['test: başlık doğru'])
        assert v2['parent_version'] == 1 and v2['can_accept']
        old = await work.get_deliverable(v1['id'])
        assert not old['is_latest'] and not old['can_accept']
        with pytest.raises(ValueError, match='güncel'):
            await work.accept(v1['id'])
        with pytest.raises(ValueError, match='güncel'):
            await work.request_repair(v1['id'], 'eski kart')
        assert (await work.get(item['id']))['status'] == 'waiting'
        await work.accept(v2['id'])
        assert (await work.get(item['id']))['status'] == 'done'
    finally:
        await work.close()


async def test_delivery_cannot_claim_unrelated_run_or_parent(tmp_path):
    work = WorkRecordManager(tmp_path / 'work.sqlite')
    await work.open()
    try:
        item = await work.create('İş', run_id='current')
        for fields in ({'run_id': 'other'}, {'parent_version': 1}):
            with pytest.raises(ValueError):
                await work.add_deliverable(item['id'], summary='v1', evidence=['test: passed'], **fields)
        await work.add_deliverable(item['id'], run_id='current', summary='v1', evidence=['test: passed'])
        with pytest.raises(ValueError):
            await work.add_deliverable(item['id'], summary='v2', evidence=['test: passed'], parent_version=0)
    finally:
        await work.close()
