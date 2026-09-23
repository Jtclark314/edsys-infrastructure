"""Evaluate signed host observations; absence of evidence never means ready."""
from datetime import datetime, timezone
from typing import Any


def evaluate_readiness(raw: dict[str, Any]) -> dict[str, Any]:
    checks = []
    def add(key, label, status, detail):
        checks.append(dict(id=key, label=label, status=status, detail=detail))
    user = raw.get('user_readiness') or {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(user['checked_at'].replace('Z', '+00:00'))).total_seconds()
        fresh = -30 <= age <= 600 and user.get('elevated') is False and int(user.get('session_id', 0)) > 0
    except (KeyError, ValueError, TypeError):
        fresh = False
    observed = {str(c.get('id')): c for c in user.get('checks', []) if isinstance(c, dict)} if fresh else {}
    for key, label in [(f'drive-{x}', f'{x}: drive') for x in 'FIKQRSTU'] + [('sync', 'Syncthing')]:
        item = observed.get(key, {})
        status = item.get('status', 'unknown')
        if status not in {'ok', 'warning', 'critical', 'unknown', 'not_applicable'}:
            status = 'unknown'
        add(key, label, status, str(item.get('detail') or 'Fresh normal-desktop observation unavailable.')[:240])
    health = raw.get('health') or {}
    services = {item.get('name'): item.get('status') for item in health.get('services', [])}
    for name, label in [('Tailscale', 'Tailscale service'), ('sshd', 'SSH service'), ('SunshineService', 'Sunshine service'), ('SentinelAgent', 'Endpoint protection')]:
        state = services.get(name)
        add(name, label, 'ok' if state == 'Running' else ('critical' if state else 'unknown'), state or 'Service state unavailable.')
    stream = raw.get('stream_endpoints')
    add('stream', 'Streaming endpoints', 'ok' if stream is True else ('warning' if stream is False else 'unknown'),
        'Sunshine control and RTSP listeners respond on the Tailscale address; video quality is not measured.' if stream else 'Sunshine control and RTSP listeners could not be confirmed on the Tailscale address.')
    total, free = float(raw.get('disk_total') or 0), float(raw.get('disk_available') or 0)
    if total:
        pct = free / total * 100
        status = 'critical' if pct < 5 or free < 10*1024**3 else ('warning' if pct < 10 or free < 20*1024**3 else 'ok')
        add('disk', 'System disk capacity', status, f'{free/1024**3:.1f} GiB free ({pct:.1f}%); warning below 10% or 20 GiB, critical below 5% or 10 GiB.')
    else:
        add('disk', 'System disk capacity', 'unknown', 'Disk capacity unavailable.')
    statuses = {c['status'] for c in checks}
    status = 'critical' if 'critical' in statuses else ('warning' if 'warning' in statuses else ('unknown' if 'unknown' in statuses else 'ok'))
    return dict(status=status, checked_at=raw.get('observed_at'), user_checked_at=user.get('checked_at'), user_session_fresh=fresh, checks=checks)
