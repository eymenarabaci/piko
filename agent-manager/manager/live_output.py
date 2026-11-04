"""Bounded read-only output snapshots for owned CLI calls; never terminal input."""
from collections import OrderedDict
import json
import re
import threading
import time

LIMIT = 65536
MAX_STREAMS = 32
_lock = threading.RLock()
_streams = OrderedDict()


def _public_text(value):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(value))
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[gizlendi]', text)
    return re.sub(r'(?i)((?:api[_-]?key|password|passwd|token|secret|şifre)["\s]*[:=]["\s]*)[^\s,;"}]+', r'\1[gizlendi]', text)


def start(stream_id, pid):
    with _lock:
        _streams[stream_id] = {'pid': pid, 'state': 'running', 'output': '', 'received_bytes': 0,
                               '_started': time.monotonic(), '_activity': time.monotonic()}
        while len(_streams) > MAX_STREAMS:
            old = next((key for key, value in _streams.items() if value['state'] != 'running'), next(iter(_streams)))
            _streams.pop(old)


def append(stream_id, text='', byte_count=0):
    with _lock:
        item = _streams.get(stream_id)
        if item is None:
            return
        item['received_bytes'] += byte_count
        item['_activity'] = time.monotonic()
        if text:
            item['output'] = (item['output'] + _public_text(text))[-LIMIT:]


def append_event(stream_id, line):
    """Publish visible assistant/tool status only, never reasoning or tool payloads."""
    try:
        event = json.loads(line)
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    part = event.get('part') or {}
    if not isinstance(part, dict):
        return
    kind = event.get('type')
    if kind == 'text' and isinstance(part.get('text'), str):
        append(stream_id, part['text'] + '\n')
    elif kind == 'tool_use':
        state = part.get('state') or {}
        if isinstance(state, dict):
            append(stream_id, f"[{part.get('tool', 'araç')}] {state.get('status', '')}: {str(state.get('title', ''))[:160]}\n")
    elif kind == 'step_start':
        append(stream_id, 'Ajan adımı başladı.\n')
    elif kind == 'error':
        append(stream_id, 'Sağlayıcı hata bildirdi.\n')


def finish(stream_id, state):
    with _lock:
        if stream_id in _streams:
            _streams[stream_id]['state'] = state
            _streams[stream_id]['_finished'] = time.monotonic()


def snapshot(stream_id):
    with _lock:
        item = _streams.get(stream_id)
        if item is None:
            return None
        now = time.monotonic()
        return {key: value for key, value in item.items() if not key.startswith('_')} | {
            'elapsed_seconds': round(item.get('_finished', now) - item['_started'], 1),
            'idle_seconds': round(now - item['_activity'], 1), 'kind': 'cli', 'read_only': True}
