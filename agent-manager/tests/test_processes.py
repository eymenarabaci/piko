import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
import psutil
import threading
import time
from unittest.mock import patch

from manager.processes import ProcessError, run_command
from manager import processes
from manager.providers import Providers


async def event(*args):
    pass


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_joins_ledger_thread(self):
        started = threading.Event()
        finished = threading.Event()
        original = processes._record
        calls = 0
        def slow_record(root, entry):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                time.sleep(.15)
                original(root, entry)
                finished.set()
            else:
                original(root, entry)
        with tempfile.TemporaryDirectory() as directory, patch.object(processes, '_record', slow_record):
            root = Path(directory)
            task = asyncio.create_task(run_command([sys.executable, '-c', 'import time;time.sleep(60)'],
                                      root=root, workspace=root, timeout=3, on_event=event))
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(.01)
            task.cancel()
            await asyncio.sleep(.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(finished.is_set())
            row = json.loads((root / 'work/resource-ledger.json').read_text())['resources'][0]
            self.assertEqual(row['state'], 'cancelled')
            self.assertFalse(psutil.pid_exists(row['pid']))

    async def test_cancellation_retains_spawn_handle(self):
        spawned = asyncio.Event()
        original = asyncio.create_subprocess_exec
        owned = []
        async def slow_spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            owned.append(child.pid)
            spawned.set()
            await asyncio.sleep(.1)
            return child
        with tempfile.TemporaryDirectory() as directory, patch.object(asyncio, 'create_subprocess_exec', slow_spawn):
            root = Path(directory)
            task = asyncio.create_task(run_command([sys.executable, '-c', 'import time;time.sleep(60)'],
                                      root=root, workspace=root, timeout=3, on_event=event))
            await asyncio.wait_for(spawned.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(psutil.pid_exists(owned[0]))
            row = json.loads((root / 'work/resource-ledger.json').read_text())['resources'][0]
            self.assertEqual(row['state'], 'cancelled')

    async def test_owned_descendant_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = ('import subprocess,sys,time; '
                      'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
                      'open("child.pid","w").write(str(p.pid)); time.sleep(60)')
            with self.assertRaises(ProcessError):
                await run_command([sys.executable, '-c', script], root=root, workspace=root,
                                  timeout=5, on_event=event)
            pid = int((root / 'child.pid').read_text())
            self.assertFalse(psutil.pid_exists(pid), 'Owned descendant survived timeout')

    async def test_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kwargs = dict(root=root, workspace=root, timeout=3, on_event=event)
            reply = await run_command([sys.executable, '-c', 'import sys; print(sys.stdin.read()); print("err",file=sys.stderr)'],
                                      stdin='hello', **kwargs)
            self.assertEqual(reply['stdout'].strip(), 'hello')
            self.assertEqual(reply['stderr'].strip(), 'err')
            with self.assertRaises(ProcessError):
                await run_command([sys.executable, '-c', 'import time; time.sleep(30)'], **{**kwargs, 'timeout': .3})
            task = asyncio.create_task(run_command([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs))
            await asyncio.sleep(.2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(ProcessError):
                await run_command([sys.executable, '-c', 'print("x"*2200000)'], **kwargs)
            records = json.loads((root / 'work/resource-ledger.json').read_text())['resources']
            self.assertEqual(len(records), 4)
            self.assertEqual([r['state'] for r in records], ['completed', 'timeout', 'cancelled', 'failed'])

    def test_parsers(self):
        reply = Providers._codex('test', dict(returncode=0, stderr='', stdout=
            '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n{"type":"turn.completed","usage":{}}'))
        self.assertEqual(reply['output'], 'ok')
        reply = Providers._agy('test', dict(returncode=0, stderr='', stdout='{"status":"SUCCESS","response":"ok"}'))
        self.assertEqual(reply['output'], 'ok')
