#!/usr/bin/env python3
"""Guarded immutable provider mounts and model-only workspace activation."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SERVICE = Path(__file__).resolve().parent
CANONICAL = Path('/srv/edsys/edsys-infrastructure')
RELEASE = Path('/opt/edsys-rag-assist/releases/v1.0.0')
NATIVE = '/app/server/utils/AiProviders/liteLLM'
UPSTREAM_HASH = '3208973be792e4b616c2048945fa6541c836c7acd97025081c34f43858b86305'
IMAGE = 'mintplexlabs/anythingllm@sha256:00903f6311607b661d40f9e1d0e027d61e28a7b002ea9d1b7cad7763d26099f9'
DATABASE = Path('/mnt/ai-store/anythingllm/anythingllm.db')
RUNTIME_VERIFIER = Path('/home/jeremy/bin/anythingllm-rag-verify.py')


def command(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs).stdout


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def save(private, name, value):
    target = private / name
    target.write_text(json.dumps(value, indent=2, sort_keys=True))
    target.chmod(0o600)


def inspect():
    return json.loads(command('docker', 'inspect', 'anythingllm', text=True))[0]


def workspace():
    with sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute('select * from workspaces where slug=?', ('edsys-rag-current',)).fetchall()
        if len(rows) != 1:
            raise RuntimeError('Expected one managed workspace')
        return dict(rows[0])


def compose_config(root):
    # Resolved credentials remain in this process only, never in logs/receipts.
    raw = command('sudo', '-n', 'docker', 'compose', '--project-name', 'anythingllm', '-f', str(root / 'docker/anythingllm/compose.yaml'), 'config', '--format', 'json', text=True)
    return json.loads(raw)['services']['anythingllm']


def preflight():
    before, candidate = compose_config(CANONICAL), compose_config(ROOT)
    before_without_volumes = {k: v for k, v in before.items() if k != 'volumes'}
    if before_without_volumes != {k: v for k, v in candidate.items() if k != 'volumes'}:
        raise RuntimeError('Candidate changes more than provider mounts')
    if candidate['volumes'][:len(before['volumes'])] != before['volumes'] or len(candidate['volumes']) != len(before['volumes']) + 3:
        raise RuntimeError('Candidate mount inventory differs')
    actual = inspect()
    if actual['Config']['Image'] != IMAGE:
        raise RuntimeError('AnythingLLM image differs from qualification pin')
    environment = dict(item.split('=', 1) for item in actual['Config']['Env'])
    if any(environment.get(k) != str(v) for k, v in candidate.get('environment', {}).items()):
        raise RuntimeError('Resolved source environment differs from running app; inspect privately')
    baseline_mounts = {(v['source'], v['target'], not v.get('read_only', False)) for v in before['volumes']}
    running_mounts = {(v['Source'], v['Destination'], v['RW']) for v in actual['Mounts']}
    if running_mounts != baseline_mounts or len(actual['Mounts']) != len(before['volumes']):
        raise RuntimeError('Running mount paths or permissions differ from baseline')
    baseline_ports = sorted((v.get('host_ip', ''), str(v['published']), str(v['target']) + '/' + v.get('protocol', 'tcp')) for v in before.get('ports', []))
    running_ports = sorted((binding['HostIp'], binding['HostPort'], target) for target, bindings in actual['HostConfig']['PortBindings'].items() for binding in bindings)
    if running_ports != baseline_ports:
        raise RuntimeError('Running port bindings differ from baseline')
    if actual['HostConfig']['RestartPolicy'] != {'Name': before.get('restart', 'no'), 'MaximumRetryCount': 0}:
        raise RuntimeError('Running restart policy differs from baseline')
    native = command('docker', 'exec', 'anythingllm', 'cat', NATIVE + '/index.js')
    if hashlib.sha256(native).hexdigest() != UPSTREAM_HASH:
        raise RuntimeError('Running native provider changed')
    current = workspace()
    if current['chatProvider'] != 'litellm' or current['chatModel'] != 'edsys-chat-local' or current['chatMode'] != 'query' or current['router_id'] is not None:
        raise RuntimeError('Workspace no longer matches the selected local baseline')
    native_verifier = (CANONICAL / 'scripts/ops/anythingllm-rag-verify.py').read_bytes()
    if RUNTIME_VERIFIER.is_symlink() or RUNTIME_VERIFIER.read_bytes() != native_verifier:
        raise RuntimeError('Installed verifier differs from authoritative baseline')
    receipt = {'workspace_digest': digest(current), 'env_digest': digest(environment), 'image': actual['Image'], 'ports': actual['HostConfig']['PortBindings'], 'networks': sorted(actual['NetworkSettings']['Networks']), 'mounts': actual['Mounts'], 'verifier_sha256': hashlib.sha256(native_verifier).hexdigest(), 'source_hashes': {name: hashlib.sha256((SERVICE / name).read_bytes()).hexdigest() for name in ('provider.cjs', 'assist.cjs')}, 'candidate_verifier_sha256': hashlib.sha256((ROOT / 'scripts/ops/anythingllm-rag-verify.py').read_bytes()).hexdigest()}
    return receipt, current, native, candidate


def install_verifier(content):
    handle, temporary = tempfile.mkstemp(prefix='.anythingllm-rag-verify-', dir=RUNTIME_VERIFIER.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o755)
        os.replace(temporary, RUNTIME_VERIFIER)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-state', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    private = args.private_state
    private.mkdir(parents=True, mode=0o700, exist_ok=True)
    private.chmod(0o700)
    if (private / 'app-attempted.json').exists():
        raise RuntimeError('Previous app attempt recorded; inspect instead of replaying')
    receipt, original, upstream, candidate = preflight()
    if not args.apply:
        save(private, 'app-preflight.json', receipt)
        save(private, 'workspace-before.json', original)
        (private / 'upstream.cjs').write_bytes(upstream)
        (private / 'upstream.cjs').chmod(0o600)
        print(json.dumps({'preflight': True, 'only_three_provider_mounts_planned': True, 'runtime_environment_matches': True, 'workspace_change': 'chatModel only', 'credentials_exported': False}))
        return
    if receipt != json.loads((private / 'app-preflight.json').read_text()):
        raise RuntimeError('App preflight changed; inspect')
    if RELEASE.exists():
        raise RuntimeError('Immutable release path already exists; inspect')
    save(private, 'app-attempted.json', {'started': True})
    (private / 'verifier-before.py').write_bytes(RUNTIME_VERIFIER.read_bytes())
    (private / 'verifier-before.py').chmod(0o600)
    command('sudo', '-n', 'install', '-d', '-m', '0755', str(RELEASE))
    for source, name in [(SERVICE / 'provider.cjs', 'provider.cjs'), (SERVICE / 'assist.cjs', 'assist.cjs'), (private / 'upstream.cjs', 'upstream.cjs')]:
        command('sudo', '-n', 'install', '-m', '0644', str(source), str(RELEASE / name))
        if (RELEASE / name).read_bytes() != source.read_bytes():
            raise RuntimeError('Immutable release readback differs')
    timer_active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'edsys-rag-sync.timer']).returncode == 0
    if timer_active:
        command('systemctl', '--user', 'stop', 'edsys-rag-sync.timer')
    try:
        # Ordinary model selections retain the upstream class during activation.
        output = command('sudo', '-n', 'docker', 'compose', '--project-name', 'anythingllm', '--project-directory', str(CANONICAL / 'docker/anythingllm'), '-f', str(ROOT / 'docker/anythingllm/compose.yaml'), 'up', '-d', '--no-deps', '--pull', 'never', '--no-build', 'anythingllm', text=True)
        (private / 'compose-apply.log').write_text(output)
        (private / 'compose-apply.log').chmod(0o600)
        for _ in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:3002/api/ping', timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError('App did not recover; inspect then restore only prior Compose/mode')
        spec = importlib.util.spec_from_file_location('verifier', RUNTIME_VERIFIER)
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        if workspace() != original:
            raise RuntimeError('Workspace changed during container activation')
        verifier.api_post('http://127.0.0.1:3002', verifier.read_api_key(DATABASE), '/v1/workspace/edsys-rag-current/update', {'chatModel': 'edsys-knowledge-local-first'}, 20)
        current = workspace()
        changed = {key for key in original if original[key] != current[key]}
        if changed - {'chatModel', 'lastUpdatedAt'} or current['chatModel'] != 'edsys-knowledge-local-first':
            raise RuntimeError('Workspace readback differs; inspect')
        install_verifier((ROOT / 'scripts/ops/anythingllm-rag-verify.py').read_bytes())
        after = inspect()
        env = dict(item.split('=', 1) for item in after['Config']['Env'])
        if digest(env) != receipt['env_digest'] or after['Image'] != receipt['image'] or after['HostConfig']['PortBindings'] != receipt['ports'] or sorted(after['NetworkSettings']['Networks']) != receipt['networks']:
            raise RuntimeError('Runtime environment/image/network/ports changed; inspect')
        expected_mounts = {(v['source'], v['target'], not v.get('read_only', False)) for v in candidate['volumes']}
        actual_mounts = {(v['Source'], v['Destination'], v['RW']) for v in after['Mounts']}
        if actual_mounts != expected_mounts:
            raise RuntimeError('Runtime mount readback differs')
        proof = command('docker', 'exec', 'anythingllm', 'node', '-e', "const {LiteLLM}=require('/app/server/utils/AiProviders/liteLLM');const {getEmbeddingEngineSelection}=require('/app/server/utils/helpers');const c=new LiteLLM(getEmbeddingEngineSelection(),'edsys-knowledge-local-first');console.log('EDSYS_RESULT '+JSON.stringify({connector:c.constructor.name,embedder:c.embedder.constructor.name,streaming:c.streamingEnabled(),local:c.model}));process.exit(0)", text=True)
        if 'RagAssistConnector' not in proof or 'LiteLLMEmbedder' not in proof:
            raise RuntimeError('Scoped provider or configured embedder did not load')
        result = {'applied': True, 'workspace_chat_model_only_changed': True, 'configured_embedder_preserved': True, 'existing_environment_image_ports_networks_preserved': True, 'only_three_readonly_code_mounts_added': True, 'provider_hash_pin_verified': True, 'verifier_installed': True, 'credentials_exported': False}
        save(private, 'app-result.json', result)
        print(json.dumps(result))
    finally:
        if timer_active:
            command('systemctl', '--user', 'start', 'edsys-rag-sync.timer')


if __name__ == '__main__':
    main()
