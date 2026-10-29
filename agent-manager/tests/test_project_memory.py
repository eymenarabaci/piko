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
    assert stats['facts_count'] >= 1
    facts = memory.list_facts(workspace)
    assert any('Doğrulama başarılı' in f['content'] for f in facts)

