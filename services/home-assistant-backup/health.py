#!/usr/bin/env python3
"""Read-only aggregate HA export health; never opens an archive, manifest or key."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess

STAGE = Path('/srv/edsys-backup/staging/home-assistant')
MAX_AGE = timedelta(hours=26)


def read_status(path, uid=0):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                    or info.st_mode & 0o077 or not 0 < info.st_size <= 16384):
                return None
            raw = stream.read(16385)
            if len(raw) != info.st_size:
                return None
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def service_status(runner=subprocess.run):
    try:
        result = runner(['systemctl', 'show', 'edsys-home-assistant-backup.service',
                         '-p', 'LoadState', '-p', 'ActiveState', '-p', 'Result',
                         '-p', 'ExecMainStatus'], capture_output=True, text=True,
                        timeout=5, check=False)
        if result.returncode or len(result.stdout) > 4096:
            return {}
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    except (OSError, subprocess.SubprocessError):
        return {}


def safe_time(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def summarize(current, last_good, service, now=None):
    now = now or datetime.now(timezone.utc)
    reasons = []
    active = service.get('ActiveState')
    active = active if active in {'active', 'inactive', 'activating', 'deactivating', 'failed', 'reloading'} else 'unknown'
    result = service.get('Result')
    result = 'success' if result == 'success' else ('failed' if result else 'unknown')
    loaded = service.get('LoadState') == 'loaded'
    if not loaded:
        reasons.append('collector_service_unavailable')
    if active == 'failed' or result == 'failed' or service.get('ExecMainStatus') not in ('0', None):
        reasons.append('collector_service_failed')
    if active == 'unknown' or result == 'unknown':
        reasons.append('collector_service_state_unknown')

    def project(value, label):
        if not isinstance(value, dict):
            reasons.append(label + '_missing_or_unreadable')
            return None
        projected = {'state': value.get('status') if value.get('status') in {'success', 'failed'} else 'unknown'}
        if projected['state'] != 'success':
            reasons.append(label + '_not_successful')
        slug = value.get('backup_id')
        projected['backup_id'] = slug if isinstance(slug, str) and re.fullmatch(r'[0-9a-f]{8}', slug) else None
        if projected['backup_id'] is None:
            reasons.append(label + '_backup_id_unavailable')
        for field in ('checked_at', 'backup_date'):
            date = safe_time(value.get(field))
            projected[field] = date.isoformat() if date else None
            if date is None or now - date < -timedelta(minutes=5):
                reasons.append(label + '_' + field + '_invalid')
            elif now - date > MAX_AGE:
                reasons.append(label + '_' + field + '_stale')
            if field == 'backup_date' and date:
                projected['backup_age_hours'] = round((now - date).total_seconds() / 3600, 2)
        projected['native_key_verified'] = value.get('native_key_verified') is True
        if not projected['native_key_verified']:
            reasons.append(label + '_key_not_verified')
        return projected

    current_projection = project(current, 'current_status')
    good_projection = project(last_good, 'last_good_export')
    if (current_projection and good_projection and current_projection['state'] == 'success'
            and current_projection['backup_id'] != good_projection['backup_id']):
        reasons.append('current_and_last_good_ids_disagree')
    return {'export_health': 'unhealthy' if reasons else 'healthy',
            'scope': 'HA export stage only; Restic/offsite inclusion is separate',
            'reasons': reasons,
            'collector': {'loaded': loaded, 'active_state': active, 'result': result},
            'current_status': current_projection, 'last_good_export': good_projection}


def main():
    result = summarize(read_status(STAGE / 'status.json'),
                       read_status(STAGE / 'last-success.json'), service_status())
    print(json.dumps(result, sort_keys=True))
    return 0 if result['export_health'] == 'healthy' else 2


if __name__ == '__main__':
    raise SystemExit(main())
