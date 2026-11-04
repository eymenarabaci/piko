"""Normalize OpenCode's per-model-turn usage, including cache and reasoning."""
import json


def opencode_usage(line):
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(event, dict) or event.get('type') != 'step_finish':
        return None
    part = event.get('part')
    if not isinstance(part, dict) or not part.get('id'):
        return None
    tokens = part.get('tokens')
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get('cache') or {}
    if not isinstance(cache, dict):
        return None
    values = [tokens.get('input'), tokens.get('output'), tokens.get('reasoning', 0),
              cache.get('read', 0), cache.get('write', 0)]
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in values):
        return None
    incoming, outgoing, reasoning, read, write = values
    return {'event_id': str(part.get('messageID', '')) + ':' + str(part['id']),
            'input_tokens': incoming + read + write, 'output_tokens': outgoing + reasoning,
            'reasoning_tokens': reasoning, 'cache_read_tokens': read, 'cache_write_tokens': write,
            'source': 'provider_reported'}
