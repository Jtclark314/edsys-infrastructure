from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import health

NOW = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
SERVICE = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'Result': 'success', 'ExecMainStatus': '0'}


def good():
    return {'status': 'success', 'backup_id': 'ab270a93',
            'checked_at': (NOW - timedelta(hours=1)).isoformat(),
            'backup_date': (NOW - timedelta(hours=2)).isoformat(),
            'native_key_verified': True}


def test_current_and_last_good_export_are_distinct_from_restic():
    current = {'status': 'failed', 'checked_at': NOW.isoformat(), 'error': 'private failure detail'}
    result = health.summarize(current, good(), SERVICE, NOW)
    assert result['export_health'] == 'unhealthy'
    assert result['current_status']['state'] == 'failed'
    assert result['last_good_export']['backup_id'] == 'ab270a93'
    assert 'Restic/offsite inclusion is separate' in result['scope']
    assert 'private' not in json.dumps(result)


def test_fresh_verified_success_is_healthy():
    result = health.summarize(good(), good(), SERVICE, NOW)
    assert result['export_health'] == 'healthy' and not result['reasons']


@pytest.mark.parametrize('change', ['failed_service', 'missing_service', 'missing_current',
                                   'missing_good', 'stale_backup', 'stale_check', 'key_unverified',
                                   'future', 'bad_id', 'bad_time', 'mismatched_ids'])
def test_health_failures_are_explicit_and_scrubbed(change):
    current, last, service = good(), good(), dict(SERVICE)
    if change == 'failed_service': service['Result'] = 'exit-code'
    if change == 'missing_service': service = {}
    if change == 'missing_current': current = None
    if change == 'missing_good': last = None
    if change == 'stale_backup': last['backup_date'] = (NOW - timedelta(hours=26, seconds=1)).isoformat()
    if change == 'stale_check': current['checked_at'] = (NOW - timedelta(hours=26, seconds=1)).isoformat()
    if change == 'key_unverified': current['native_key_verified'] = False
    if change == 'future': last['backup_date'] = (NOW + timedelta(minutes=6)).isoformat()
    if change == 'bad_id': current['backup_id'] = 'private-key-content'
    if change == 'bad_time': current['backup_date'] = 'private-key-content'
    if change == 'mismatched_ids': current['backup_id'] = '12345678'
    result = health.summarize(current, last, service, NOW)
    assert result['export_health'] == 'unhealthy' and result['reasons']
    assert 'private-key-content' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['symlink', 'oversized', 'public', 'malformed'])
def test_status_read_is_bounded_private_and_regular(tmp_path, kind):
    path = tmp_path / 'status.json'
    path.write_text(json.dumps(good()))
    path.chmod(0o600)
    if kind == 'symlink':
        actual = tmp_path / 'actual'
        path.rename(actual)
        path.symlink_to(actual)
    if kind == 'oversized': path.write_text('x' * 16385)
    if kind == 'public': path.chmod(0o644)
    if kind == 'malformed': path.write_text('not-json private-detail')
    assert health.read_status(path, uid=os.geteuid()) is None


def test_service_query_is_bounded_and_projects_no_raw_diagnostic():
    def runner(command, **kwargs):
        assert kwargs['timeout'] == 5
        assert command[:3] == ['systemctl', 'show', 'edsys-home-assistant-backup.service']
        return SimpleNamespace(returncode=0, stdout='LoadState=loaded\nActiveState=failed\nResult=private diagnostic\nExecMainStatus=1\n')
    result = health.summarize(good(), good(), health.service_status(runner), NOW)
    assert result['export_health'] == 'unhealthy'
    assert 'private' not in json.dumps(result)


def test_status_integration_remains_nonfatal():
    root = Path(__file__).resolve().parents[3]
    script = (root / 'scripts/backup/edsys-backup-status.sh').read_text()
    assert '/usr/bin/python3 "${HA_EXPORT_HEALTH_HELPER}" || true' in script
    assert '## Home Assistant Export Health' in script
