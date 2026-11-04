"""Independent adapters for the locally configured Codex, Antigravity and Ollama."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import re
from uuid import uuid4

import httpx

from .execution_policy import model_allowed
from .processes import MAX_OUTPUT_BYTES, ProcessError, run_command
from .outcomes import ensure_usable_result
from .local_edits import apply_local_edits, local_write_prompt


class ProviderError(RuntimeError):
    pass


def resolve_codex() -> list[str]:
    if os.name == 'nt':
        packages = Path(os.environ.get('APPDATA', '')) / 'npm' / 'node_modules' / '@openai'
        for executable in sorted(packages.glob('codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe')):
            if executable.with_name('codex-code-mode-host.exe').is_file():
                return [str(executable)]
    found = shutil.which('codex') or shutil.which('codex.exe')
    directories = [Path(os.environ.get('APPDATA', '')) / 'npm',
                   Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI' / 'Codex' / 'bin',
                   Path.home() / '.codex' / 'packages' / 'app-server-daemon' / 'current' / 'bin']
    if found:
        candidate = Path(found)
        if os.name != 'nt' or candidate.suffix.lower() == '.exe':
            return [str(candidate)]
        directories.insert(0, candidate.parent)
    for directory in directories:
        if not directory.is_dir():
            continue
        direct = directory / 'codex.exe'
        if direct.is_file():
            return [str(direct)]
        packages = directory / 'node_modules' / '@openai'
        matches = sorted(packages.glob('codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe'))
        matches += sorted(packages.glob('codex*/vendor/*/codex/codex.exe'))
        if matches:
            return [str(matches[0])]
        script = packages / 'codex' / 'bin' / 'codex.js'
        node = shutil.which('node.exe') or shutil.which('node')
        if node and script.is_file():
            return [node, str(script)]
    raise ProviderError('Codex CLI bulunamadı; PATH veya npm kurulumunu kontrol edin.')


def resolve_claude() -> list[str]:
    found = shutil.which('claude.exe') or shutil.which('claude')
    if not found:
        candidate = Path.home() / '.local' / 'bin' / 'claude.exe'
        found = str(candidate) if candidate.is_file() else None
    if not found:
        raise ProviderError('Claude Code CLI bulunamadı; claude.exe kurulumunu kontrol edin.')
    return [found]


def resolve_opencode() -> list[str]:
    found = shutil.which('opencode.exe') or shutil.which('opencode.cmd') or shutil.which('opencode')
    if not found:
        candidate = Path.home() / '.opencode' / 'bin' / 'opencode.exe'
        found = str(candidate) if candidate.is_file() else None
    if not found:
        raise ProviderError('OpenCode CLI bulunamadı; opencode kurulumunu kontrol edin.')
    if os.name == 'nt' and Path(found).suffix.lower() in {'.cmd', '.bat'}:
        return ['cmd', '/c', found]
    return [found]

def resolve_agy() -> list[str]:
    found = shutil.which('agy.exe') or shutil.which('agy')
    if not found:
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'agy' / 'bin' / 'agy.exe'
        found = str(candidate) if candidate.is_file() else None
    if not found or (os.name == 'nt' and Path(found).suffix.lower() != '.exe'):
        raise ProviderError('Antigravity CLI bulunamadı; agy.exe kurulumunu kontrol edin.')
    return [found]


def _failure(text: str) -> str:
    """Never forward arbitrary stderr, which can contain credentials or prompts."""
    value = text[-65536:].lower()
    if any(x in value for x in ('usage limit', 'quota_exceeded', 'insufficient_quota')):
        return 'Sağlayıcı kullanım kotası doldu.'
    if any(x in value for x in ('rate limit', 'too many requests')):
        return 'Sağlayıcı istek sınırına ulaşıldı.'
    if any(x in value for x in ('unauthorized', 'not logged', 'sign in', 'authentication', 'keyring')):
        return 'CLI oturumu doğrulanamadı; sağlayıcının kendi CLI aracında oturum açın.'
    if any(x in value for x in ('unknown flag', 'unknown option', 'unrecognized option')):
        return 'Kurulu CLI sürümü gerekli argümanları desteklemiyor.'
    return 'Sağlayıcı işlemi başarısız oldu. CLI erişimini ve model kimliğini kontrol edin.'


def opencode_exit_diagnostic(process):
    code = int(process.get('returncode', -1))
    raw = str(process.get('stderr', '')) + str(process.get('stdout', ''))
    value = raw.lower()
    category = 'cli_exit'
    reason = _failure(raw)
    for signals, label, message in [
        (('econnreset', 'socket connection was closed', 'enotfound', 'etimedout'), 'transport', 'Sağlayıcı ağ bağlantısı kesildi.'),
        (('sağlıklı endpoint yok', 'no healthy endpoint', 'model şu anda dolu'), 'provider_unavailable', 'Sağlayıcı model endpointi geçici olarak kullanılamıyor.'),
        (('context_length_exceeded', 'maximum context length', 'context window'), 'context_limit', 'Model bağlam sınırı aşıldı.'),
    ]:
        if any(signal in value for signal in signals):
            category, reason = label, message
            break
    if code & 0xffffffff == 0xc000013a:
        category, reason = 'process_interrupted', 'CLI işlemi dışarıdan kapatıldı (Windows CTRL/CLOSE).'
    return {'returncode': code, 'exit_hex': hex(code & 0xffffffff), 'category': category,
            'stdout_bytes': len(str(process.get('stdout', '')).encode('utf-8')),
            'stderr_bytes': len(str(process.get('stderr', '')).encode('utf-8')),
            'reason': reason + f' [exit={code}; {category}]'}


class Providers:
    def __init__(self, models: list[dict], root: Path):
        self.models = [dict(row) for row in models]
        self.by_id = {row['id']: row for row in self.models}
        self.root = Path(root).resolve()

    def available(self) -> list[dict]:
        resolved = {}
        for provider, resolver in [('codex', resolve_codex), ('antigravity', resolve_agy),
                                   ('claude-code', resolve_claude), ('opencode', resolve_opencode)]:
            try:
                resolved[provider] = resolver()[-1]
            except ProviderError:
                resolved[provider] = None
        resolved['ollama'] = shutil.which('ollama') or shutil.which('ollama.exe')
        return [{'id': provider, 'available': bool(executable), 'executable': executable}
                for provider, executable in resolved.items()]

    async def run(self, model_id, prompt, *, workspace: str, allow_writes: bool,
                  timeout: float, on_event, effort: str = '', images: list[str] | None = None) -> dict:
        model = self.by_id.get(model_id, {})
        if model.get('provider') not in {'opencode', 'evren'}:
            return await self._run(model_id, prompt, workspace=workspace, allow_writes=allow_writes,
                                   timeout=timeout, on_event=on_event, effort=effort, images=images)
        timeout = 0  # Operator request: no fixed wall-clock limit on Evren CLI calls.
        session_id = await on_event('provider_session_lookup', '', {'model_id': model_id})
        resuming_session = isinstance(session_id, str) and bool(re.fullmatch(r'ses_[A-Za-z0-9]+', session_id))
        if not resuming_session:
            session_id = 'ses_' + uuid4().hex
            # Persist identity before spawn: even a zero-output network failure
            # must retry the same session rather than creating another one.
            await on_event('provider_session', 'CLI oturum checkpointi çağrıdan önce kaydedildi.',
                           {'session_id': session_id, 'model_id': model_id, 'created_before_spawn': True})
        call_id, seen = uuid4().hex, set()
        totals = {'input_tokens': 0, 'output_tokens': 0}
        accounted = True
        started = False

        async def capture(kind, message, data=None):
            nonlocal accounted, started
            if kind == 'provider_session':
                return await on_event(kind, message, {**(data or {}), 'model_id': model_id})
            if kind == 'provider_started':
                started = True
            if kind == 'provider_usage':
                key = (data or {}).get('event_id')
                if key in seen:
                    return
                seen.add(key)
                for name in totals:
                    totals[name] += data[name]
                ack = await on_event(kind, message, {**data, 'model_id': model_id,
                                                     'call_id': call_id + ':' + key})
                accounted = accounted and ack is True
                return ack
            return await on_event(kind, message, data)

        try:
            result = await self._run(model_id, prompt, workspace=workspace, allow_writes=allow_writes,
                                     timeout=timeout, on_event=capture, effort=effort, images=images, session_id=session_id, session_is_resume=resuming_session)
        except BaseException:
            # A failed call may have consumed tokens without a completed turn report.
            if started:
                await on_event('provider_usage', 'Başarısız çağrının kalan tüketimi bilinmiyor.',
                               {'model_id': model_id, 'call_id': call_id + ':incomplete',
                                'input_tokens': 0, 'output_tokens': 0, 'source': 'unknown'})
            raise
        if seen:
            result['usage'] = {**totals, 'source': 'provider_reported', 'accounted': accounted}
        else:
            missing = {'input_tokens': 0, 'output_tokens': 0}
            ack = await on_event('provider_usage', 'Sağlayıcı kullanım bildirmedi; tüketim bilinmiyor.',
                                {**missing, 'source': 'unknown', 'model_id': model_id,
                                 'call_id': call_id + ':unreported'})
            result['usage'] = {**missing, 'source': 'unknown', 'accounted': ack is True}
        return result

    async def _run(self, model_id, prompt, *, workspace: str, allow_writes: bool,
                   timeout: float, on_event, effort: str = '', images: list[str] | None = None, session_id: str | None = None, session_is_resume: bool = False) -> dict:
        model = self.by_id.get(model_id)
        if not model or not model_allowed(model) or model.get('execution_supported', True) is not True:
            raise ProviderError('Model yürütmeye açık değil veya katalogda bulunamadı.')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode('utf-8')) > 200000:
            raise ProviderError('Prompt boş veya 200 KB sınırından büyük.')
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 <= timeout <= 1800:
            raise ProviderError('Sağlayıcı süresi 0 (sınırsız) veya 1–1800 saniye olmalı.')
        try:
            directory = Path(workspace).resolve(strict=True)
        except OSError as exc:
            raise ProviderError('Çalışma klasörü bulunamadı.') from exc
        if not directory.is_dir():
            raise ProviderError('Çalışma yolu bir klasör olmalı.')
        effort = effort or model.get('default_effort', '')
        if effort and effort not in model.get('effort_levels', []):
            raise ProviderError('Model bu düşünme yoğunluğunu desteklemiyor.')
        provider = model.get('provider')
        images = images or []
        try:
            if provider == 'ollama':
                local_prompt = local_write_prompt(directory, prompt) if allow_writes else prompt
                result = await self._ollama(model_id, local_prompt, timeout, on_event, images=images)
                if allow_writes:
                    changed = apply_local_edits(directory, result['output'])
                    result['local_patch_applied'] = changed
                    result['output'] += '\n\nUygulanan yerel dosya düzenlemeleri: ' + ', '.join(changed)
                return result
            if provider == 'codex':
                command = resolve_codex() + ['--no-daemon', 'exec', '--ephemeral',
                         '--json', '--skip-git-repo-check', '--sandbox', 'workspace-write' if allow_writes else 'read-only',
                         '--cd', str(directory)]
                concrete = model.get('base_model') if model_id == 'codex' else model_id
                if concrete and concrete not in {'codex', 'codex-auto-review', 'auto'}:
                    command += ['--model', concrete]
                if effort:
                    command += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
                for image_path in images:
                    command += ['-i', image_path]
                command += ['-']
                await on_event('provider_policy', 'Codex yerel sandbox ile çalışır; özel Skill/MCP katalog aktarımı bu sürümde yok.', {})
                process = await run_command(command, root=self.root, workspace=directory,
                                             timeout=timeout, on_event=on_event, stdin=prompt)
                return self._codex(model_id, process)
            if provider == 'antigravity':
                if images:
                    raise ProviderError('Antigravity CLI görsel eki kabul etmiyor; görsel destekli Codex veya Ollama seçin.')
                if len(prompt) > 24000:
                    raise ProviderError('Antigravity prompt sınırı 24.000 karakter.')
                actual_model = model.get('base_model') or model_id
                command = resolve_agy() + ['--model', actual_model]
                if effort:
                    command += ['--effort', effort]
                command += ['--mode', 'accept-edits' if allow_writes else 'plan',
                           '--output-format', 'json', '--dangerously-skip-permissions',
                           '--print-timeout', '0s', '--print', prompt]
                if os.name == 'nt':
                    import subprocess
                    if len(subprocess.list2cmdline(command).encode('utf-16-le')) // 2 >= 30000:
                        raise ProviderError('Antigravity komut uzunluğu Windows sınırını aşıyor.')
                await on_event('provider_policy', 'Antigravity global araç ayarlarını devralır; çalışma başına MCP izolasyonu sunmaz.', {})
                process = await run_command(command, root=self.root, workspace=directory,
                                             timeout=timeout, on_event=on_event)
                return self._agy(model_id, process)
            if provider == 'claude-code':
                actual_model = model.get('base_model') or model_id
                if actual_model.startswith('claude-code:'):
                    actual_model = actual_model.split(':', 1)[1]
                command = resolve_claude() + ['--print', prompt]
                if actual_model:
                    command += ['--model', actual_model]
                if allow_writes:
                    command += ['--dangerously-skip-permissions']
                await on_event('provider_policy', 'Claude Code yerel CLI ile çalıştırılıyor.', {})
                process = await run_command(command, root=self.root, workspace=directory,
                                             timeout=timeout, on_event=on_event)
                output = process.get('stdout', '') or process.get('stderr', '')
                return {'output': output.strip(), 'model_id': model_id, 'status': 'completed',
                        'usage': {}, 'local_patch_applied': []}
            if provider in {'opencode', 'evren'}:
                actual_model = model.get('base_model') or model_id
                if actual_model.startswith('opencode:'):
                    actual_model = actual_model.split(':', 1)[1]
                call_prompt = ('Ağ kesintisinden sonra aynı atanmış adıma devam et. Önce mevcut dosyaları ve tamamladığın işlemleri kontrol et; aynı etkiyi tekrarlama. Yeni kapsam ekleme, yarım kalan işi tamamla ve gerçek sonucu bildir.' if session_is_resume else prompt)
                command = resolve_opencode() + ['run', call_prompt, '--format', 'json']
                if session_id:
                    command += ['--session', session_id]
                    await on_event('provider_session_resumed' if session_is_resume else 'provider_session_opened', 'Aynı CLI oturumundan devam ediliyor.' if session_is_resume else 'Kalıcı CLI oturumu açılıyor.', {'session_id': session_id, 'model_id': model_id})
                if actual_model and actual_model != 'auto':
                    command += ['--model', actual_model]
                if allow_writes:
                    command += ['--agent', 'build', '--auto']
                else:
                    command += ['--agent', 'plan']
                await on_event('provider_policy', 'OpenCode CLI ile çalıştırılıyor.', {})
                process = await run_command(command, root=self.root, workspace=directory,
                                             timeout=timeout, on_event=on_event, live_format="opencode_jsonl")
                if process.get('returncode', 0) != 0:
                    diagnostic = opencode_exit_diagnostic(process)
                    await on_event('provider_exit_error', 'OpenCode CLI başarısız çıkış kaydı.', diagnostic)
                    raise ProviderError(diagnostic['reason'])
                return self._opencode(model_id, process)
            raise ProviderError('Desteklenmeyen sağlayıcı.')
        except ProcessError as exc:
            raise ProviderError(str(exc)) from exc
        except OSError as exc:
            raise ProviderError('CLI başlatılamadı; çalışma klasörü ve yürütülebilir dosyayı kontrol edin.') from exc

    @staticmethod
    def _opencode(model_id, process):
        """Extract only the last assistant message from OpenCode JSONL transport."""
        message_id = None
        parts = {}
        for line in process.get('stdout', '').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'error':
                raise ProviderError('OpenCode hata olayi dondurdu; islem tamamlanmadi.')
            part = event.get('part')
            if event.get('type') != 'text' or not isinstance(part, dict):
                continue
            text = part.get('text')
            if not isinstance(text, str):
                continue
            current = part.get('messageID')
            if current != message_id:
                parts = {}
                message_id = current
            parts[part.get('id') or str(len(parts))] = text
        output = '\n'.join(parts.values()).strip()
        if not output:
            raise ProviderError('OpenCode gecerli son mesaj dondurmedi.')
        return {'output': output, 'model_id': model_id, 'status': 'completed',
                'usage': {}, 'local_patch_applied': []}

    @staticmethod
    def _codex(model_id, process):
        messages, usage, completed, failed = [], {}, False, False
        for line in process['stdout'].splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            item = row.get('item', {})
            if row.get('type') == 'item.completed' and isinstance(item, dict) and item.get('type') == 'agent_message':
                if isinstance(item.get('text'), str):
                    messages.append(item['text'])
            if row.get('type') == 'turn.completed':
                completed = True
                usage = row.get('usage') if isinstance(row.get('usage'), dict) else {}
            if row.get('type') in {'error', 'turn.failed'}:
                failed = True
        if process['returncode'] != 0 or failed:
            raise ProviderError(_failure(process['stdout'] + process['stderr']))
        if not completed or not messages or not messages[-1].strip():
            raise ProviderError('Codex tamamlanmış, kullanılabilir bir yanıt döndürmedi.')
        return ensure_usable_result({'output': messages[-1], 'model_id': model_id, 'status': 'completed', 'usage': usage})

    @staticmethod
    def _agy(model_id, process):
        if process['returncode'] != 0:
            raise ProviderError(_failure(process['stdout'] + process['stderr']))
        data = None
        raw = process['stdout'].lstrip('\ufeff')
        try:
            data = json.loads(raw)
        except ValueError:
            for line in reversed(raw.splitlines()):
                try:
                    candidate = json.loads(line)
                    if isinstance(candidate, dict):
                        data = candidate
                        break
                except ValueError:
                    continue
        if not isinstance(data, dict) or str(data.get('status')).upper() != 'SUCCESS':
            raise ProviderError(_failure(raw))
        output = data.get('response')
        if not isinstance(output, str) or not output.strip():
            if 'print timeout' in raw:
                raise ProviderError('Antigravity CLI yanıt süresi doldu (zaman aşımı).')
            if 'denied_actions' in raw or 'auto-denied' in raw:
                raise ProviderError('Antigravity araç çalıştırma izinleri reddedildi.')
            raise ProviderError('Antigravity boş yanıt döndürdü.')
        return ensure_usable_result({'output': output, 'model_id': model_id, 'status': 'completed',
                'usage': data.get('usage') if isinstance(data.get('usage'), dict) else {}})

    async def _ollama(self, model_id, prompt, timeout, on_event, *, images=None):
        parts, total, final = [], 0, None
        await on_event('provider_started', 'Yerel Ollama yanıtı bekleniyor.', {})
        message = {'role': 'user', 'content': prompt}
        if images:
            message['images'] = [base64.b64encode(Path(path).read_bytes()).decode('ascii') for path in images]
        async def pulse():
            while True:
                await asyncio.sleep(5)
                await on_event('provider_activity', 'Ollama çalışıyor; yanıt bekleniyor.', {'received_bytes': total})
        heartbeat = asyncio.create_task(pulse())
        try:
            async with asyncio.timeout(timeout), httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
                async with client.stream('POST', 'http://127.0.0.1:11434/api/chat', json={
                    'model': model_id, 'stream': True, 'think': False,
                    'keep_alive': '30m',
                    'messages': [message],
                    'options': {'num_ctx': 16384, 'num_predict': 4096}}) as response:
                    response.raise_for_status()
                    pending = b''
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            raise ProviderError('Ollama çıktısı 2 MiB sınırını aştı.')
                        pending += chunk
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            if line.strip():
                                row = json.loads(line)
                                if row.get('error'):
                                    raise ProviderError('Ollama modeli çalıştıramadı.')
                                content = row.get('message', {}).get('content', '')
                                if not isinstance(content, str):
                                    raise ProviderError('Ollama yanıt biçimi geçersiz.')
                                parts.append(content)
                                if row.get('done'):
                                    final = row
                    if pending.strip():
                        row = json.loads(pending)
                        parts.append(row.get('message', {}).get('content', ''))
                        if row.get('done'):
                            final = row
            if not final or not ''.join(parts).strip():
                raise ProviderError('Ollama tamamlanmış bir yanıt döndürmedi.')
            if final.get('done_reason') == 'length':
                raise ProviderError('Ollama yanıtı çıktı sınırında kesildi.')
            return {'output': ''.join(parts), 'model_id': model_id, 'status': 'completed',
                    'usage': {'input_tokens': final.get('prompt_eval_count', 0), 'output_tokens': final.get('eval_count', 0)}}
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, AttributeError) as exc:
            raise ProviderError('Ollama yanıtı alınamadı; yerel servis, model ve süre sınırını kontrol edin.') from exc
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
