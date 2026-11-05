"""No model/server calls: graph relevance, incremental updates and privacy bounds."""
from pathlib import Path
import json

from manager.project_memory import ProjectMemory


def test_project_memory_incremental_graph_and_unverified_notes(tmp_path):
    workspace = tmp_path / 'project'
    src = workspace / 'src'
    src.mkdir(parents=True)
    (src / 'auth.ts').write_text("import { api } from './api'; export function login() {}", encoding='utf-8')
    (src / 'api.ts').write_text('export const api = {};', encoding='utf-8')
    (src / 'graphics.ts').write_text('export const canvas = {};', encoding='utf-8')
    (src / 'credentials.ts').write_text('private secret data', encoding='utf-8')
    (workspace / '.env').write_text('PASSWORD=do-not-index', encoding='utf-8')
    (workspace / 'package.json').write_text(json.dumps({'scripts': {'dev': 'do-not-store-command'},
                                                       'dependencies': {'react': '^18'}}), encoding='utf-8')
    generated = workspace / 'node_modules' / 'large'
    generated.mkdir(parents=True)
    (generated / 'ignored.ts').write_text('export const ignored = 1;', encoding='utf-8')
    memory = ProjectMemory(tmp_path / 'data')
    first = memory.refresh(workspace)
    assert first['files'] == 4
    assert first['changed'] == 4
    assert Path(first['notebook_path']).is_file()
    graph = json.loads(Path(first['graph_path']).read_text(encoding='utf-8'))
    assert graph['graph']['src/auth.ts'] == ['src/api.ts']
    serialized = json.dumps(graph)
    assert 'do-not-index' not in serialized
    assert 'private secret data' not in serialized
    assert 'do-not-store-command' not in serialized
    second = memory.refresh(workspace)
    assert second['changed'] == 0 and second['reused'] == 4
    (src / 'auth.ts').write_text("import { api } from './api'; export function loginNew() {}", encoding='utf-8')
    changed = memory.refresh(workspace, {'id': 'run', 'status': 'failed', 'plan': {'steps': [
        {'id': 'one', 'title': 'Auth değişikliği'}]}, 'results': {'one': {'status': 'completed', 'output': 'PASSWORD=do-not-store'}}})
    assert changed['changed'] == 1
    context = memory.context(workspace, 'giriş auth loginNew', max_chars=3000)
    assert 'src/auth.ts' in context and 'src/api.ts' in context
    assert 'src/graphics.ts' not in context
    assert 'bağımsız doğrulanmadı' in context
    assert 'do-not-store' not in context
    assert len(context) <= 3000
    (src / 'graphics.ts').unlink()
    assert memory.refresh(workspace)['removed'] == 1


def test_project_memory_strict_context_bound(tmp_path):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    (workspace / 'app.py').write_text('import json\ndef entry():\n return json.dumps({})\n', encoding='utf-8')
    memory = ProjectMemory(tmp_path / 'data')
    assert len(memory.context(workspace, 'entry', max_chars=80)) <= 80
    assert memory.context(workspace, 'entry', max_chars=0) == ''


def test_compounding_memory_facts_and_rules(tmp_path):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    (workspace / 'main.py').write_text('def main(): pass', encoding='utf-8')
    memory = ProjectMemory(tmp_path / 'data')

    # Add rule and preference
    rule = memory.add_fact(workspace, 'Asla vendor klasörünü doğrudan düzenleme.', category='rule', tags=['vendor', 'kural'])
    pref = memory.add_fact(workspace, 'Yanıtlar ve dokümantasyon Türkçe olmalıdır.', category='preference', tags=['dil', 'türkçe'])
    arch = memory.add_fact(workspace, 'Veritabanında SQLite WAL modu kullanılır.', category='architecture', tags=['sqlite', 'db'])

    assert rule['category'] == 'rule'
    assert len(memory.list_facts(workspace)) == 3
    assert len(memory.list_facts(workspace, category='rule')) == 1

    # Search facts
    results = memory.search_facts(workspace, 'sqlite veritabanı')
    assert len(results) >= 1
    assert results[0]['id'] == arch['id']

    # Context injection includes rules
    context = memory.context(workspace, 'vendor paket güncellemesi', max_chars=2000)
    assert 'Öğrenilen Proje Kuralları ve Hafızası' in context
    assert 'Asla vendor klasörünü doğrudan düzenleme.' in context

    # Update and delete
    updated = memory.update_fact(workspace, rule['id'], content='Asla vendor/ ve build/ klasörünü değiştirme.')
    assert updated['content'] == 'Asla vendor/ ve build/ klasörünü değiştirme.'
    assert memory.delete_fact(workspace, pref['id']) is True
    assert len(memory.list_facts(workspace)) == 2


def test_compounding_memory_auto_reflection(tmp_path):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    (workspace / 'test_app.py').write_text('def test_ok(): assert True', encoding='utf-8')
    memory = ProjectMemory(tmp_path / 'data')

    run_payload = {
        'id': 'run_123',
        'status': 'completed',
        'prompt': 'Birim testleri çalıştır ve doğrula',
        'plan': {
            'steps': [
                {'id': 'step_1', 'title': 'Test ve doğrulama çalıştır'}
            ]
        },
        'results': {
            'step_1': {'status': 'completed', 'output': 'All 5 tests passed successfully in 0.12s.'}
        }
    }
    stats = memory.refresh(workspace, run_payload)
    assert stats['facts_count'] == 0
    assert not memory.list_facts(workspace)
    data = json.loads(Path(stats['graph_path']).read_text(encoding='utf-8'))
    assert 'All 5 tests passed' in data['plan_notes'][0]['steps'][0]['summary']
    assert data['plan_notes'][0]['evidence'] == 'Ajan/orkestratör raporu; bağımsız doğrulanmadı.'



def test_failed_verification_is_never_promoted_to_success(tmp_path):
    workspace=tmp_path/'project'; workspace.mkdir()
    memory=ProjectMemory(tmp_path/'data')
    memory.refresh(workspace, {'id':'run','status':'completed','plan':{'steps':[{'id':'test','title':'Test build'}]},'results':{'test':{'status':'failed','output':'Tests failed'}}})
    assert not memory.list_facts(workspace)


def test_phase_history_is_preserved_with_goals_and_unverified_evidence(tmp_path):
    workspace=tmp_path/'project'; workspace.mkdir()
    memory=ProjectMemory(tmp_path/'data')
    first={'execution_id':'first','status':'completed','prompt':'Implement scheduler','plan':{'steps':[{'id':'a','title':'Scheduler'}]},'results':{'a':{'status':'completed','output':'report only'}},'audit':'repair: no executable proof'}
    stats=memory.refresh(workspace,{'id':'run','execution_id':'second','status':'running','active_goal':'Add executable proof','plan_history':[first],'plan':{'steps':[]},'results':{}})
    notes=json.loads(Path(stats['graph_path']).read_text(encoding='utf-8'))['plan_notes']
    assert {n['execution_id'] for n in notes} == {'first','second'}
    assert notes[0]['steps'][0]['summary']=='report only'
    assert 'repair' in notes[0]['audit']
    assert 'Implement scheduler' in memory.context(workspace,'scheduler',max_chars=4000)


def test_user_rule_survives_automatic_fact_retention_and_context_ranking(tmp_path):
    workspace=tmp_path/'project'; workspace.mkdir()
    memory=ProjectMemory(tmp_path/'data')
    rule={'id':'rule','source':'user','category':'rule','content':'Canlı servisi değiştirme','tags':[]}
    automatic=[{'id':str(i),'source':'auto_reflection','category':'insight','content':f'pytest scheduler measurement baseline {i}','tags':['pytest']} for i in range(45)]
    retained=memory._auto_reflect_internal({'status':'running'},[rule,*automatic])
    assert rule in retained
    assert len([f for f in retained if f['source']=='auto_reflection'])==45
    stats=memory.refresh(workspace)
    path=Path(stats['graph_path']);data=json.loads(path.read_text(encoding='utf-8'));data['facts']=retained;path.write_text(json.dumps(data),encoding='utf-8')
    context=memory.context(workspace,'pytest scheduler measurement baseline',max_chars=4000)
    assert 'Canlı servisi değiştirme' in context
    assert 'kaynak=user' in context
    assert 'pytest scheduler measurement baseline' not in context
    assert len(memory.list_facts(workspace, include_quarantined=True)) == 46
    assert len(memory.list_facts(workspace)) == 1
    assert 'Doğrulama başarılı' not in context


def test_prompt_echo_and_transient_errors_are_quarantined_without_losing_user_rules(tmp_path):
    workspace = tmp_path/'project'; workspace.mkdir()
    memory = ProjectMemory(tmp_path/'data')
    stats = memory.refresh(workspace)
    path = Path(stats['graph_path']); data = json.loads(path.read_text(encoding='utf-8'))
    records = [
        {'id':'echo','source':'auto_reflection','category':'insight','content':'Doğrulama başarılı: Varsayılan olarak işi kendin tamamla.'},
        {'id':'quota','source':'auto_reflection','category':'insight','content':'CAO HTTP 502 uygun model kotası kalmadı'},
        {'id':'rule','source':'user','category':'rule','content':'Kota korumasını kaldırma'},
        {'id':'pref','source':'user','category':'preference','content':'Türkçe yanıtla'}]
    data['facts'] = records; path.write_text(json.dumps(data), encoding='utf-8')
    stats = memory.refresh(workspace)
    all_facts = memory.list_facts(workspace, include_quarantined=True)
    assert {f['id'] for f in all_facts} == {'echo','quota','rule','pref'}
    assert {f['id'] for f in memory.list_facts(workspace)} == {'rule','pref'}
    assert {f['id'] for f in memory.search_facts(workspace, 'CAO kota doğrulama')} == {'rule','pref'} or all(f['source']=='user' for f in memory.search_facts(workspace, 'CAO kota doğrulama'))
    assert all(f['quarantine_reason']=='automatic_run_report_not_project_fact' for f in all_facts if f['source']=='auto_reflection')
    assert stats['facts_count']==2 and stats['quarantined_facts_count']==2
    context=memory.context(workspace,'CAO HTTP 502 doğrulama',max_chars=4000)
    assert 'Varsayılan olarak' not in context and 'CAO HTTP 502' not in context
    assert 'Kota korumasını kaldırma' in context and 'Türkçe yanıtla' in context
