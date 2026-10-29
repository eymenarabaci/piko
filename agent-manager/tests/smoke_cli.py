"""Explicit, minimal live-provider smoke; never imported by unittest discovery."""
import asyncio
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from manager.providers import Providers, ProviderError


async def event(*args):
    pass


async def main():
    models = json.loads((ROOT / 'config/models.json').read_text(encoding='utf-8'))['models']
    providers = Providers(models, ROOT)
    for model in ['gpt-6.1-sol', 'gemini-3.8-flash-medium']:
        try:
            result = await providers.run(model,
                'Yalnız AGENT_MANAGER_OK yaz. Araç kullanma/dosya okuma/yazma.',
                workspace=str(ROOT), allow_writes=False, timeout=55, on_event=event)
            print(json.dumps({'model': model, 'status': result['status'],
                              'output_bytes': len(result['output'].encode('utf-8')),
                              'expected_marker': result['output'].strip() == 'AGENT_MANAGER_OK'}), flush=True)
        except ProviderError as exc:
            print(json.dumps({'model': model, 'status': 'failed', 'error': str(exc)}), flush=True)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            response = await client.get('http://127.0.0.1:11434/api/tags')
            response.raise_for_status()
        print(json.dumps({'provider': 'ollama', 'status': 'reachable',
                          'model_count': len(response.json().get('models', []))}), flush=True)
    except (httpx.HTTPError, ValueError):
        print(json.dumps({'provider': 'ollama', 'status': 'unavailable'}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
