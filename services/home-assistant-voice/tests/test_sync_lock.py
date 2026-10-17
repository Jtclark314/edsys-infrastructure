"""Offline command and real local-lock regressions; no SSH/QGA is invoked."""
import fcntl
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('ha_knowledge_sync_lock', ROOT / 'sync_knowledge.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


@pytest.mark.parametrize('locked', [False, True])
def test_fixed_refresh_command_lock_wait_and_catalog_failure_semantics(monkeypatch, capsys, locked):
    calls = []
    def export(path):
        path.write_bytes(b'offline consistent-catalog fixture')
        return {'sources': 1, 'chunks': 1}
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if locked:
            return SimpleNamespace(returncode=75, stdout='', stderr='private transport detail')
        payload = json.loads(kwargs['input'])
        return SimpleNamespace(returncode=0,
                               stdout=json.dumps({'exitcode': 0, 'out-data': json.dumps({'sha256': payload['sha256']})}))
    monkeypatch.setattr(sync, 'export', export)
    monkeypatch.setattr(sync.subprocess, 'run', runner)
    if locked:
        with pytest.raises(RuntimeError, match='transport failed'):
            sync.main()
        assert capsys.readouterr() == ('', '')
    else:
        sync.main()
        assert json.loads(capsys.readouterr().out)['digest_verified'] is True
    assert len(calls) == 1  # Refresh never replays a write after any failure.
    command, options = calls[0]
    assert command[0] == 'ssh' and command[-2] == 'pve-node3'
    remote = shlex.split(command[-1])
    assert remote[:7] == ['flock', '--exclusive', '--wait', '2', '--conflict-exit-code',
                          '75', '/run/lock/edsys-ha-qga-300.lock']
    assert remote[7:11] == ['qm', 'guest', 'exec', '300']
    assert remote[-1] == sync.RECEIVER
    assert options['timeout'] == 60
    assert 'TimeoutStartSec=65' in (ROOT / 'systemd/edsys-ha-voice-knowledge.service').read_text()


def test_real_flock_timeout_never_runs_the_wrapped_receiver(tmp_path):
    lock_path = tmp_path / 'same-guest.lock'
    marker = tmp_path / 'receiver-ran'
    code = 'from pathlib import Path; Path(' + repr(str(marker)) + ').write_text("ran")'
    command = ['flock', '--exclusive', '--wait', '0.05', '--conflict-exit-code',
               '75', str(lock_path), sys.executable, '-c', code]
    with lock_path.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(command, capture_output=True, timeout=2, check=False)
        assert result.returncode == 75
        assert not marker.exists()
    result = subprocess.run(command, capture_output=True, timeout=2, check=False)
    assert result.returncode == 0 and marker.read_text() == 'ran'
