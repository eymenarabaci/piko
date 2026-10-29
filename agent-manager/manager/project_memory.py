"""Bounded project notebook and import graph, without model calls or source copies."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid

SCHEMA = 2
EXCLUDED = {
    '.git', '.svn', '.hg', '.venv', 'venv', 'env', 'node_modules', 'vendor',
    'dist', 'dist-electron', 'build', 'release', 'out', 'target', 'coverage',
    '.next', '.nuxt', '.cache', '__pycache__', '.pytest_cache', 'work', 'data',
    '.idea', '.vscode', 'bin', 'obj', '.gradle', '.turbo', 'generated',
}
EXTENSIONS = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.css', '.html',
              '.vue', '.svelte', '.rs', '.go', '.java', '.cs', '.json', '.toml', '.md'}
SENSITIVE = re.compile(r'(?i)(?:^\.env(?:\.|$)|credential|secret|service[-_]?account|private[-_]?key|token|password|keystore|lock\.)')
MAX_FILES, MAX_DIRECTORIES, MAX_BYTES, MAX_TOTAL_BYTES = 1000, 2000, 256_000, 8_000_000


def _now():
    return datetime.now(timezone.utc).isoformat()


def _words(value):
    value = re.sub(r'([a-z])([A-Z])', r'\1 \2', value)
    return {word.lower() for word in re.findall(r'[\w]+', value) if len(word) > 2}


def _safe_label(value, limit=180):
    value = re.sub(r'[\x00-\x1f]', ' ', str(value))
    value = re.sub(r'(?i)(bearer\s+\S+|(?:password|token|secret|api[_-]?key|şifre)\s*[:=]\s*\S+)', '[gizlendi]', value)
    value = re.sub(r'\b[A-Za-z0-9_\-]{40,}\b', '[uzun değer gizlendi]', value)
    return value[:limit]


class ProjectMemory:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir).resolve() / 'project_memory'
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _paths(self, workspace):
        workspace = Path(workspace).resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError('Proje klasörü bulunamadı.')
        identity = os.path.normcase(str(workspace))
        key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        return workspace, self.root / f'{key}.json', self.root / f'{key}.md'

    @staticmethod
    def _atomic(path, content):
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(content, encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _parse(path, source):
        imports, symbols = set(), set()
        package = None
        if path.suffix == '.py':
            try:
                tree = ast.parse(source)
                for item in ast.walk(tree):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        symbols.add(item.name)
                    elif isinstance(item, ast.Import):
                        imports.update(alias.name for alias in item.names)
                    elif isinstance(item, ast.ImportFrom):
                        prefix = '.' * item.level + (item.module or '')
                        imports.add(prefix)
            except (SyntaxError, ValueError, RecursionError):
                pass
        elif path.suffix in {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte'}:
            imports.update(re.findall(r'''(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(\s*|\bimport\s*)["']([^"'\r\n]{1,180})["']''', source))
            symbols.update(re.findall(r'\bexport\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|interface|type|enum)\s+(\w+)', source))
        if path.name == 'package.json':
            try:
                data = json.loads(source)
                package = {
                    'name': _safe_label(data.get('name', ''), 80),
                    'scripts': sorted(_safe_label(key, 60) for key in data.get('scripts', {}) if isinstance(key, str))[:30],
                    'dependencies': sorted(set(data.get('dependencies', {})) | set(data.get('devDependencies', {})))[:100],
                }
            except (ValueError, TypeError, AttributeError):
                pass
        return {'imports': sorted(imports)[:60], 'symbols': sorted(symbols)[:60], 'package': package}

    @staticmethod
    def _graph(files):
        modules = {}
        for name in files:
            path = Path(name)
            modules[name] = name
            modules[path.with_suffix('').as_posix()] = name
            if path.stem in {'index', '__init__'}:
                modules[path.parent.as_posix()] = name
            if path.suffix == '.py':
                modules[path.with_suffix('').as_posix().replace('/', '.')] = name
        graph = {}
        for name, item in files.items():
            edges = set()
            for reference in item['imports']:
                if reference.startswith('.'):
                    if Path(name).suffix == '.py':
                        count = len(reference) - len(reference.lstrip('.'))
                        parent = Path(name).parent
                        for _ in range(max(0, count - 1)):
                            parent = parent.parent
                        key = (parent / reference.lstrip('.').replace('.', '/')).as_posix()
                    else:
                        key = os.path.normpath(str(Path(name).parent / reference)).replace('\\', '/')
                elif reference.startswith('@/'):
                    key = 'src/' + reference[2:]
                else:
                    key = reference
                if key in modules and modules[key] != name:
                    edges.add(modules[key])
            graph[name] = sorted(edges)[:60]
        return graph

    def refresh(self, workspace: str | Path, run: dict | None = None) -> dict:
        with self._lock:
            workspace, json_path, md_path = self._paths(workspace)
            try:
                previous = json.loads(json_path.read_text(encoding='utf-8'))
                if previous.get('schema') not in {1, 2}:
                    previous = {}
            except (OSError, ValueError):
                previous = {}
            old_files = previous.get('files', {})
            files, changed, reused, consumed, directories = {}, [], 0, 0, 0
            truncated = False
            for directory, children, names in os.walk(workspace, followlinks=False):
                directories += 1
                if directories > MAX_DIRECTORIES:
                    truncated = True
                    break
                base = Path(directory)
                children[:] = sorted(child for child in children
                    if child.lower() not in EXCLUDED and not child.startswith('.')
                    and not (base / child).is_symlink()
                    and not (base / child).is_junction()
                    and (base / child).resolve() != self.root)
                for filename in sorted(names):
                    path = base / filename
                    if path.suffix.lower() not in EXTENSIONS or SENSITIVE.search(filename) or filename in {'package-lock.json', 'pnpm-lock.yaml'}:
                        continue
                    if path.is_symlink() or path.is_junction():
                        continue
                    try:
                        resolved = path.resolve(strict=True)
                        if not resolved.is_relative_to(workspace):
                            continue
                        stat = path.stat()
                        if stat.st_size > MAX_BYTES:
                            continue
                        name = path.relative_to(workspace).as_posix()
                        fingerprint = [stat.st_mtime_ns, stat.st_size]
                        old = old_files.get(name, {})
                        if old.get('fingerprint') == fingerprint:
                            files[name] = old
                            reused += 1
                        else:
                            if consumed + stat.st_size > MAX_TOTAL_BYTES:
                                truncated = True
                                continue
                            consumed += stat.st_size
                            source = path.read_bytes().decode('utf-8', 'replace')
                            files[name] = {'fingerprint': fingerprint, **self._parse(path, source)}
                            changed.append(name)
                    except (OSError, ValueError):
                        continue
                    if len(files) >= MAX_FILES:
                        truncated = True
                        break
                if len(files) >= MAX_FILES:
                    break
            removed = sorted(set(old_files) - set(files)) if not truncated else []
            notes = previous.get('plan_notes', [])[-11:]
            facts = previous.get('facts', [])
            if run is not None:
                note = {'execution_id': run.get('execution_id') or run.get('id'),
                        'status': _safe_label(run.get('status', 'unknown'), 30), 'recorded_at': _now(),
                        'evidence': 'Ajan/orkestratör raporu; bağımsız doğrulanmadı.',
                        'steps': [{'title': _safe_label(step.get('title', step.get('id', ''))),
                                   'status': _safe_label(run.get('results', {}).get(step.get('id'), {}).get('status', 'pending'), 30)}
                                  for step in (run.get('plan') or {}).get('steps', [])[:12]]}
                notes = [item for item in notes if item.get('execution_id') != note['execution_id']]
                notes.append(note)
                facts = self._auto_reflect_internal(run, facts)
            data = {'schema': SCHEMA, 'workspace': str(workspace), 'updated_at': _now(),
                    'files': files, 'graph': self._graph(files), 'plan_notes': notes,
                    'facts': facts,
                    'last_changes': {'changed': changed[:150], 'removed': removed[:150]},
                    'truncated': truncated}
            self._atomic(json_path, json.dumps(data, ensure_ascii=False, indent=2))
            self._atomic(md_path, self._markdown(data))
            return {'files': len(files), 'changed': len(changed), 'reused': reused,
                    'removed': len(removed), 'notebook_path': str(md_path),
                    'graph_path': str(json_path), 'truncated': truncated,
                    'facts_count': len(facts)}

    def _auto_reflect_internal(self, run: dict, facts: list[dict]) -> list[dict]:
        """Automatically synthesize lessons, architectural choices, and error resolutions from run."""
        facts_list = list(facts)
        status = run.get('status')
        results = run.get('results', {})
        plan = run.get('plan') or {}
        steps = plan.get('steps', [])
        prompt = run.get('prompt', '')

        # 1. Successful verification / architectural milestones
        if status in {'completed', 'success'}:
            for s in steps:
                s_id = s.get('id', '')
                res = results.get(s_id, {})
                out = res.get('output', '')
                title = s.get('title', s_id)
                if any(w in title.lower() for w in {'test', 'doğrulama', 'verify', 'build', 'lint'}):
                    content = f"Doğrulama başarılı ({title}): {out[:120].strip()}" if out else f"Doğrulama adımı tamamlandı: {title}"
                    self._append_fact_if_new(facts_list, content, category='insight', source='auto_reflection', tags=['test', 'verification'])
            if len(steps) >= 3 and prompt:
                p_words = [w for w in prompt.split() if len(w) > 3][:6]
                summary_content = f"Başarıyla tamamlanan iş akışı: {' '.join(p_words)} ({len(steps)} adım)"
                self._append_fact_if_new(facts_list, summary_content, category='architecture', source='auto_reflection', tags=['workflow', 'success'])

        # 2. Recovered or observed errors
        if status in {'failed', 'error'}:
            for s in steps:
                s_id = s.get('id', '')
                res = results.get(s_id, {})
                if res.get('status') in {'failed', 'error'}:
                    out = res.get('output', '') or res.get('error', '')
                    title = s.get('title', s_id)
                    reason = _safe_label(out[:140].strip()) if out else 'Bilinmeyen hata'
                    content = f"Hata tecrübesi ({title}): {reason}"
                    self._append_fact_if_new(facts_list, content, category='insight', source='auto_reflection', tags=['error', 'lesson'])

        return facts_list[-40:]

    @staticmethod
    def _append_fact_if_new(facts: list[dict], content: str, category: str = 'insight', source: str = 'auto_reflection', tags: list[str] | None = None):
        content = _safe_label(content, 300)
        c_words = _words(content)
        # Avoid near duplicate entries
        for existing in facts:
            e_words = _words(existing.get('content', ''))
            if c_words and len(c_words & e_words) / max(len(c_words), len(e_words)) > 0.75:
                return
        facts.append({
            'id': f"fact_{uuid.uuid4().hex[:12]}",
            'category': category if category in {'rule', 'preference', 'architecture', 'insight'} else 'insight',
            'content': content,
            'tags': [t.lower() for t in (tags or [])][:6],
            'source': source,
            'confidence': 0.85 if source == 'auto_reflection' else 1.0,
            'created_at': _now(),
            'updated_at': _now()
        })

    def add_fact(self, workspace: str | Path, content: str, category: str = 'insight', tags: list[str] | None = None, source: str = 'user', confidence: float = 1.0) -> dict:
        with self._lock:
            self.refresh(workspace)
            workspace, json_path, md_path = self._paths(workspace)
            data = json.loads(json_path.read_text(encoding='utf-8'))
            facts = data.get('facts', [])
            fact = {
                'id': f"fact_{uuid.uuid4().hex[:12]}",
                'category': category if category in {'rule', 'preference', 'architecture', 'insight'} else 'insight',
                'content': _safe_label(content, 400),
                'tags': [t.lower() for t in (tags or [])][:6],
                'source': source,
                'confidence': max(0.1, min(1.0, float(confidence))),
                'created_at': _now(),
                'updated_at': _now()
            }
            facts.append(fact)
            data['facts'] = facts[-50:]
            data['updated_at'] = _now()
            self._atomic(json_path, json.dumps(data, ensure_ascii=False, indent=2))
            self._atomic(md_path, self._markdown(data))
            return fact

    def delete_fact(self, workspace: str | Path, fact_id: str) -> bool:
        with self._lock:
            workspace, json_path, md_path = self._paths(workspace)
            if not json_path.is_file():
                return False
            data = json.loads(json_path.read_text(encoding='utf-8'))
            facts = data.get('facts', [])
            new_facts = [f for f in facts if f.get('id') != fact_id]
            if len(new_facts) == len(facts):
                return False
            data['facts'] = new_facts
            data['updated_at'] = _now()
            self._atomic(json_path, json.dumps(data, ensure_ascii=False, indent=2))
            self._atomic(md_path, self._markdown(data))
            return True

    def update_fact(self, workspace: str | Path, fact_id: str, content: str | None = None, category: str | None = None, tags: list[str] | None = None) -> dict | None:
        with self._lock:
            workspace, json_path, md_path = self._paths(workspace)
            if not json_path.is_file():
                return None
            data = json.loads(json_path.read_text(encoding='utf-8'))
            target = None
            for f in data.get('facts', []):
                if f.get('id') == fact_id:
                    if content is not None:
                        f['content'] = _safe_label(content, 400)
                    if category is not None and category in {'rule', 'preference', 'architecture', 'insight'}:
                        f['category'] = category
                    if tags is not None:
                        f['tags'] = [t.lower() for t in tags][:6]
                    f['updated_at'] = _now()
                    target = f
                    break
            if target is not None:
                data['updated_at'] = _now()
                self._atomic(json_path, json.dumps(data, ensure_ascii=False, indent=2))
                self._atomic(md_path, self._markdown(data))
            return target

    def list_facts(self, workspace: str | Path, category: str | None = None) -> list[dict]:
        with self._lock:
            workspace, json_path, _ = self._paths(workspace)
            if not json_path.is_file():
                return []
            try:
                data = json.loads(json_path.read_text(encoding='utf-8'))
                facts = data.get('facts', [])
                if category:
                    facts = [f for f in facts if f.get('category') == category]
                return facts
            except (OSError, ValueError):
                return []

    def search_facts(self, workspace: str | Path, query: str, limit: int = 10) -> list[dict]:
        facts = self.list_facts(workspace)
        if not query.strip():
            return facts[:limit]
        words = _words(query)
        scored = []
        for f in facts:
            f_words = _words(f.get('content', ''))
            f_tags = set(f.get('tags', []))
            score = len(words & f_words) * 3 + len(words & f_tags) * 5
            if f.get('category') in {'rule', 'preference'}:
                score += 2  # Higher priority for explicit constraints
            if score > 0:
                scored.append((score, f))
        scored.sort(key=lambda item: (-item[0], item[1].get('created_at', '')))
        return [f for _, f in scored][:limit]

    @staticmethod
    def _markdown(data):
        lines = ['# Proje defteri', '', f"Güncelleme: {data['updated_at']}",
                 'Statik dosya/import gözlemi; uygulamanın çalıştığının kanıtı değildir.', '']
        for name, item in data['files'].items():
            if item.get('package'):
                package = item['package']
                lines.extend([f"## Paket: {name}",
                              'Script adları: ' + ', '.join(package['scripts']),
                              'Bağımlılıklar: ' + ', '.join(package['dependencies']), ''])
        lines.extend(['## Dosyalar ve bağlantılar', ''])
        for name, item in data['files'].items():
            lines.append(f"- `{name}`" + (': ' + ', '.join(item['symbols'][:12]) if item['symbols'] else ''))
            if data['graph'].get(name):
                lines.append('  - Bağlantılar: ' + ', '.join(data['graph'][name]))
        lines.extend(['', '## Kalıcı Öğrenilen Bilgiler ve Kurallar'])
        facts = data.get('facts', [])
        if facts:
            for f in facts:
                cat = f.get('category', 'insight').upper()
                lines.append(f"- `[{cat}]` {f.get('content')} *(kaynak: {f.get('source')})*")
        else:
            lines.append('*Henüz kalıcı kural veya bilgi kaydedilmedi.*')
        lines.extend(['', '## Plan raporları', 'Aşağıdaki durumlar ajan raporudur; bağımsız doğrulanmadı.'])
        for note in data['plan_notes']:
            lines.append(f"- {note['status']} · {note['recorded_at']}")
            lines.extend(f"  - {step['title']}: {step['status']}" for step in note['steps'])
        return '\n'.join(lines) + '\n'

    def context(self, workspace: str | Path, task: str, max_chars: int = 10000) -> str:
        max_chars = max(0, min(int(max_chars), 20000))
        if max_chars == 0:
            return ""
        with self._lock:
            self.refresh(workspace)
            _, path, _ = self._paths(workspace)
            data = json.loads(path.read_text(encoding='utf-8'))
        words = _words(task)
        aliases = {'giriş': {'auth', 'login', 'user'}, 'yönetici': {'admin', 'role', 'auth'},
                   'çalışan': {'employee', 'user'}, 'dosya': {'file', 'workspace'},
                   'senkronizasyon': {'sync', 'queue'}, 'görev': {'task'}}
        for word, extra in aliases.items():
            if word in words:
                words.update(extra)
        scored = []
        for name, item in data['files'].items():
            score = len(words & _words(name)) * 4 + len(words & _words(' '.join(item['symbols']))) * 2
            if name.lower() in task.lower():
                score += 15
            scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = [name for score, name in scored if score > 0][:16]
        if not selected:
            selected = [name for _, name in scored if name.startswith(('src/', 'manager/', 'app/'))][:8]
        neighbors = set()
        for name in selected:
            neighbors.update(data['graph'].get(name, []))
            neighbors.update(source for source, edges in data['graph'].items() if name in edges)
        selected += sorted(neighbors - set(selected))[:8]

        # Extract relevant facts
        facts = data.get('facts', [])
        scored_facts = []
        for f in facts:
            f_words = _words(f.get('content', ''))
            f_tags = set(f.get('tags', []))
            score = len(words & f_words) * 3 + len(words & f_tags) * 4
            if f.get('category') == 'rule':
                score += 5  # Strict rules must always have high visibility
            elif f.get('category') == 'preference':
                score += 3
            scored_facts.append((score, f))
        scored_facts.sort(key=lambda item: -item[0])
        relevant_facts = [f for score, f in scored_facts if score > 0][:6]
        if not relevant_facts and facts:
            relevant_facts = [f for f in facts if f.get('category') in {'rule', 'preference'}][:4]

        lines = ['[Kalıcı proje defteri — görev odaklı statik bağlam]',
                 f"Son tarama: {data['updated_at']}; {len(data['files'])} dosya; kapsam kısaltılmış: {data['truncated']}.",
                 'Bu kayıt çalışma/derleme kanıtı değildir. Görevle ilgili dosyaları doğrula; tüm mimariyi yeniden tarama.']

        if relevant_facts:
            lines.append('[Öğrenilen Proje Kuralları ve Hafızası]:')
            for f in relevant_facts:
                cat_label = {'rule': 'Kural', 'preference': 'Tercih', 'architecture': 'Mimari', 'insight': 'Ders'}.get(f.get('category'), 'Not')
                lines.append(f"- [{cat_label}]: {f.get('content')}")

        for name, item in data['files'].items():
            if item.get('package'):
                pkg = item['package']
                lines.append(f"Paket {name}: script adları={','.join(pkg['scripts'])}; bağımlılıklar={','.join(pkg['dependencies'])[:900]}")
        for name in selected:
            item = data['files'][name]
            lines.append(f"{name} | semboller: {', '.join(item['symbols'][:12])} | yerel bağlantılar: {', '.join(data['graph'].get(name, [])[:8])}")
        for note in data['plan_notes'][-3:]:
            lines.append(f"Önceki plan raporu ({note['status']}, bağımsız doğrulanmadı): " + '; '.join(f"{step['title']}={step['status']}" for step in note['steps'])[:1000])
        return '\n'.join(lines)[:max_chars]
