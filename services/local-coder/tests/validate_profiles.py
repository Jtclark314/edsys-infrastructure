"""Read-only acceptance of the actual pinned V2 config, provider and MCP catalogs."""
import json
from pathlib import Path
import sys
import time
from urllib.error import URLError

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from web_state import read_api

def validate():
    config = json.loads((SOURCE / 'opencode.json').read_text())
    expected = json.loads((SOURCE / 'opencode-client.lock.json').read_text())['version']
    assert read_api('/api/info')['version'] == expected
    providers = read_api('/api/provider')['data']
    assert [row['id'] for row in providers] == ['edsys-local']
    models = read_api('/api/model')['data']
    assert len(models) == 1
    model = models[0]
    assert model['providerID'] == 'edsys-local' and model['id'] == 'edsys-qwen36-coder:latest'
    assert model['limit'] == {'context':262144,'output':16384}
    assert model['compatibility']['reasoningField'] == 'reasoning_content'
    assert {v['id'] for v in model['variants']} == {'normal','fast'}
    agents = {row['id']:row for row in read_api('/api/agent')['data']}
    assert {'build','plan','local-general','local-code','general','explore'} <= agents.keys()
    for name in ['general','explore']:
        assert {'action':'edsys_save_checkpoint','resource':'*','effect':'deny'} in agents[name]['permissions']
    plugins = read_api('/api/plugin')['data']
    assert any(row['id'] == 'edsys.project-context' and row['state']['status'] == 'active' for row in plugins)
    groups = {row['name']:row['status']['status'] for row in read_api('/api/mcp')['data']}
    assert set(groups) == set(config['mcp']['servers'])
    assert set(groups.values()) == {'connected'}
    print('PASS: pinned V2, sole local provider/model, context plugin, focused agents and seven connected MCPs')


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
