"""Optional, bounded Graphify code context alongside the built-in project memory."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import re
import shutil
import time

from .processes import run_command


async def _ignore_event(*_args):
    pass


class GraphifyContext:
    def __init__(self, data_dir: Path, *, cli: str | None = None, runner=run_command):
        self.root = Path(data_dir).resolve()
        self.runner = runner
        self.cli = cli or self._find_cli()
        self._locks: dict[str, asyncio.Lock] = {}
        self._stale: set[str] = set()
        self._retry_after: dict[str, float] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task[str]] = {}
        self._task_created: dict[tuple[str, str], float] = {}
        self._recent: dict[str, tuple[float, str, str]] = {}

    @staticmethod
    def _find_cli() -> str | None:
        configured = os.environ.get('GRAPHIFY_CLI')
        if configured:
            return configured if Path(configured).is_file() else shutil.which(configured)
        local = Path(__file__).resolve().parents[1] / '.graphify-venv' / ('Scripts/graphify.exe' if os.name == 'nt' else 'bin/graphify')
        return str(local) if local.is_file() else shutil.which('graphify')

    def _paths(self, workspace: str | Path) -> tuple[str, Path, Path]:
        resolved = Path(workspace).resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError('Proje klasörü bulunamadı.')
        key = hashlib.sha256(os.path.normcase(str(resolved)).encode('utf-8')).hexdigest()[:24]
        cache = self.root / 'graphify' / key
        return key, cache, cache / 'graphify-out' / 'graph.json'

    @staticmethod
    def _environment() -> dict[str, str]:
        env = {key: value for key, value in os.environ.items()
               if key.upper() not in {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY',
                                       'GOOGLE_API_KEY', 'DEEPSEEK_API_KEY', 'KIMI_API_KEY',
                                       'GRAPHIFY_OUT'}}
        env['GRAPHIFY_BACKEND'] = 'none'
        env['GRAPHIFY_QUERY_LOG_DISABLE'] = '1'
        return env

    def invalidate(self, workspace: str | Path) -> None:
        key, _, _ = self._paths(workspace)
        self._stale.add(key)
        self._retry_after.pop(key, None)
        self._recent.pop(key, None)
        for task_key, task in list(self._tasks.items()):
            if task_key[0] == key:
                if not task.done():
                    task.cancel()
                self._tasks.pop(task_key, None)
                self._task_created.pop(task_key, None)

    async def context(self, workspace: str | Path, task: str, *, max_chars: int = 1800) -> str:
        if not self.cli or not task.strip() or max_chars <= 0:
            return ''
        try:
            key, _, _ = self._paths(workspace)
            task_key = (key, task[:1000])
            job = self._tasks.get(task_key)
            if job is not None and job.done() and time.monotonic() - self._task_created[task_key] > 600:
                self._tasks.pop(task_key, None)
                self._task_created.pop(task_key, None)
                job = None
            if job is None:
                if len(self._tasks) >= 64:
                    self._tasks = {key: value for key, value in self._tasks.items() if not value.done()}
                    self._task_created = {key: value for key, value in self._task_created.items() if key in self._tasks}
                job = asyncio.create_task(self._fetch(workspace, task[:1000]))
                self._tasks[task_key] = job
                self._task_created[task_key] = time.monotonic()
            return (await asyncio.wait_for(asyncio.shield(job), timeout=0.2))[:max_chars]
        except asyncio.TimeoutError:
            recent = self._recent.get(key)
            if recent and time.monotonic() - recent[0] < 600:
                current_words = {word for word in re.findall(r'\w+', task.lower()) if len(word) > 3}
                prior_words = {word for word in re.findall(r'\w+', recent[1].lower()) if len(word) > 3}
                if current_words & prior_words:
                    return recent[2][:max_chars]
            return ''
        except asyncio.CancelledError:
            raise
        except Exception:
            return ''

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._task_created.clear()
        self._recent.clear()

    async def _fetch(self, workspace: str | Path, task: str) -> str:
        try:
            key, cache, graph = self._paths(workspace)
            if time.monotonic() < self._retry_after.get(key, 0):
                return ''
            async with self._locks.setdefault(key, asyncio.Lock()):
                if not graph.is_file() or key in self._stale or time.time() - graph.stat().st_mtime > 600:
                    cache.mkdir(parents=True, exist_ok=True)
                    result = await self.runner(
                        [self.cli, 'extract', str(Path(workspace).resolve()), '--code-only',
                         '--no-viz', '--max-workers', '2', '--out', str(cache)],
                        root=self.root, workspace=cache, timeout=120, on_event=_ignore_event,
                        env=self._environment())
                    if result['returncode'] != 0 or not graph.is_file() or graph.stat().st_size == 0:
                        self._retry_after[key] = time.monotonic() + 300
                        return ''
                    self._stale.discard(key)
                result = await self.runner(
                    [self.cli, 'query', task[:1000], '--budget', '700', '--graph', str(graph)],
                    root=self.root, workspace=cache, timeout=15, on_event=_ignore_event,
                    env=self._environment())
                if result['returncode'] != 0:
                    return ''
                answer = result['stdout'].strip()
                if answer:
                    self._recent[key] = (time.monotonic(), task, answer)
                return answer if answer else ''
        except asyncio.CancelledError:
            raise
        except Exception:
            return ''
