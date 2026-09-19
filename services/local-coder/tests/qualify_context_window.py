#!/usr/bin/env python3
"""Opt-in CPU long-context recall check; synthetic data, private evidence only.

Wait for user work to become idle before running. The two requests run serially
and can take tens of minutes. They override context per request without changing
the installed model alias or active OpenCode configuration.
"""
import argparse
import json
from pathlib import Path
import time
import urllib.request


def qualify(context, destination):
    lines = {131072: 4000, 262144: 7200}[context]
    markers = ['ALPHA-72419', 'BETA-58326', 'GAMMA-91642']
    rows = [f'Entry {i:06d}: routine storage audit record contains no requested marker.\n'
            for i in range(lines)]
    for position, label, value in [(0, 'first', markers[0]),
                                   (lines // 2, 'middle', markers[1]),
                                   (lines - 1, 'last', markers[2])]:
        rows[position] = f'IMPORTANT {label} marker = {value}\n'
    prompt = ('Find the three IMPORTANT markers in this data. Report only their exact values in first, middle, last order.\n'
              + ''.join(rows) + '\nReturn the three IMPORTANT marker values, exactly.\n')
    payload = {'model': 'edsys-qwen36-coder:latest', 'prompt': prompt,
               'stream': False, 'think': False, 'keep_alive': '10m',
               'options': {'num_ctx': context, 'num_predict': 160, 'temperature': 0}}
    print(f'Starting {context}-token window; {len(prompt)} prompt characters', flush=True)
    start = time.time()
    request = urllib.request.Request('http://127.0.0.1:11435/api/generate',
                                    data=json.dumps(payload).encode(),
                                    headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=7200) as response:
        result = json.load(response)
    with urllib.request.urlopen('http://127.0.0.1:11435/api/ps', timeout=30) as response:
        loaded = json.load(response)
    summary = {key: result.get(key) for key in
               ('response', 'done', 'done_reason', 'prompt_eval_count', 'eval_count', 'total_duration')}
    summary.update(context=context, elapsed_seconds=round(time.time() - start),
                   loaded=[{key: model.get(key) for key in
                            ('name', 'context_length', 'size', 'size_vram')}
                           for model in loaded['models']])
    summary['markers_pass'] = all(value in result.get('response', '') for value in markers)
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    evidence = destination / f'context-{context}.json'
    evidence.write_text(json.dumps(summary, indent=2) + '\n')
    evidence.chmod(0o600)
    print(json.dumps(summary), flush=True)
    threshold = 65536 if context == 131072 else 131072
    if not summary['markers_pass'] or result['prompt_eval_count'] <= threshold:
        raise RuntimeError('Recall failed or the prompt did not exceed the prior context threshold')
    if not any(model['context_length'] == context for model in summary['loaded']):
        raise RuntimeError('The engine did not allocate the requested context')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=int, choices=(131072, 262144), action='append')
    parser.add_argument('--evidence', type=Path,
                        default=Path('/mnt/ai-store/local-coder/evidence/context-window'))
    args = parser.parse_args()
    for value in args.context or [131072, 262144]:
        qualify(value, args.evidence)
