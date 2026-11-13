"""Read-only acceptance of the actual pinned V2 config, provider and MCP catalogs."""
import json
from pathlib import Path
import sys
import time
from urllib.error import URLError

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from web_state import read_api

def validate_provider_models(config, providers, models):
    expected = config['providers']['edsys-local']
    assert [row['id'] for row in providers] == ['edsys-local']
    assert providers[0]['package'] == expected['package'] == '@opencode/ai/providers/openai-compatible'
    assert providers[0]['settings']['baseURL'] == expected['settings']['baseURL'] == 'http://127.0.0.1:11435/v1'
    for name in ['timeout', 'headerTimeout', 'chunkTimeout']:
        assert providers[0]['settings'][name] == expected['settings'][name] == 7200000
    assert providers[0]['compatibility']['maxTokensField'] == 'max_tokens'
    assert len(models) == 1
    model = models[0]
    assert model['providerID'] == 'edsys-local' and model['id'] == 'edsys-qwen36-coder:latest'
    assert model['limit'] == expected['models']['edsys-qwen36-coder:latest']['limit'] == {'context':262144,'output':32768}
    assert model['compatibility']['reasoningField'] == 'reasoning_content'
    variants = {v['id']: v.get('settings', {}) for v in model['variants']}
    assert variants == {'normal': {}, 'fast': {'reasoningEffort': 'none'}}


def validate():
    config = json.loads((SOURCE / 'opencode.json').read_text())
    expected = json.loads((SOURCE / 'opencode-client.lock.json').read_text())['version']
    assert read_api('/api/info')['version'] == expected
    validate_provider_models(config, read_api('/api/provider')['data'], read_api('/api/model')['data'])
    agents = {row['id']:row for row in read_api('/api/agent')['data']}
    assert {'build','plan','local-general','local-code','general','explore'} <= agents.keys()
    for name in ['general','explore']:
        assert {'action':'edsys_save_checkpoint','resource':'*','effect':'deny'} in agents[name]['permissions']
    plugins = read_api('/api/plugin')['data']
    assert any(row['id'] == 'edsys.project-context' and row['state']['status'] == 'active' for row in plugins)
    groups = {row['name']:row['status']['status'] for row in read_api('/api/mcp')['data']}
    assert set(groups) == set(config['mcp']['servers'])
    assert set(groups.values()) == {'connected'}
    print('PASS: pinned V2, sole local provider/model, max_tokens/32768 output, context plugin, focused agents and seven connected MCPs')


if __name__ == '__main__':
    # Plugin/provider/MCP catalogs populate asynchronously after V2 starts.
    deadline = time.monotonic() + 30
    while True:
        try:
            validate()
            break
        except (AssertionError, KeyError, URLError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)
