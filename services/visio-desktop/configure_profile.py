#!/usr/bin/env python3
"""Create-only native Visio workspace/profile configuration; preserve defaults."""
import json
import os
from pathlib import Path
import tomllib

from mcp_batch import PACKAGE, assert_interactive_windows


def main():
    assert_interactive_windows()
    root = Path.home() / "Documents" / "VisioMcp" / "EdSysPilot"
    dotnet = Path(os.environ["ProgramFiles"]) / "dotnet" / "dotnet.exe"
    if not dotnet.is_file():
        raise RuntimeError("Inspect installed .NET SDK path before configuration")
    root.mkdir(parents=True, exist_ok=True)
    (root / "jobs").mkdir(exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    (root / ".codex").mkdir(exist_ok=True)
    lines = [
        'model = "gpt-6-astra"', '', '[mcp_servers.visio-local]',
        'required = true',
        'command = ' + json.dumps(str(dotnet)),
        'args = ' + json.dumps(["tool", "exec", PACKAGE, "--yes"]),
        'startup_timeout_sec = 120', 'tool_timeout_sec = 180', '',
        '[mcp_servers.visio-local.env]', 'VISIO_APP_VISIBLE = "true"',
        'VISIO_MCP_ROOT = ' + json.dumps(str(root / 'artifacts')), '',
    ]
    workspace = '\n'.join(lines)
    profile = workspace + '\n[features]\nplugins = false\napps = false\n'
    for name in ('MCP_DOCKER', 'node_repl', 'openaiDeveloperDocs'):
        profile += '\n[mcp_servers.' + name + ']\nenabled = false\n'
    results = []
    for path, content in [(root / '.codex' / 'config.toml', workspace),
                          (Path.home() / '.codex' / 'visio.config.toml', profile)]:
        expected = tomllib.loads(content)
        if path.exists():
            actual = tomllib.loads(path.read_text(encoding='utf-8-sig'))
            if actual != expected:
                raise RuntimeError(f'Existing {path.name} differs; inspect and preserve it')
            results.append({'file': path.name, 'status': 'verified-existing'})
        else:
            with path.open('x', encoding='utf-8') as handle:
                handle.write(content)
            results.append({'file': path.name, 'status': 'created'})
    print(json.dumps({'configuration': results, 'defaultConfigModified': False}))


if __name__ == '__main__':
    main()
