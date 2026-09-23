from datetime import datetime, timedelta, timezone
from pathlib import Path

from edsys_fleet.collector import FleetCollector
from edsys_fleet.config import load_config
from edsys_fleet.readiness import evaluate_readiness


def observation():
    return dict(observed_at=datetime.now(timezone.utc).isoformat(), disk_total=500*1024**3, disk_available=100*1024**3,
        stream_endpoints=True, health={'services': [{'name': n, 'status': 'Running'} for n in ['Tailscale','sshd','SunshineService','SentinelAgent']]},
        user_readiness=dict(checked_at=datetime.now(timezone.utc).isoformat(), elevated=False, session_id=1,
            checks=[dict(id=k, status='ok', detail='verified') for k in [*[f'drive-{x}' for x in 'FIKQRSTU'], 'sync']]))


def test_readiness_requires_fresh_limited_desktop_observations():
    raw=observation()
    assert evaluate_readiness(raw)['status']=='ok'
    raw['user_readiness']['elevated']=True
    assert evaluate_readiness(raw)['checks'][0]['status']=='unknown'
    raw['user_readiness']['elevated']=False
    raw['user_readiness']['checked_at']=(datetime.now(timezone.utc)-timedelta(minutes=11)).isoformat()
    assert evaluate_readiness(raw)['status']=='unknown'


def test_readiness_reports_real_disk_and_service_failures():
    raw=observation();raw['disk_available']=40*1024**3
    value=evaluate_readiness(raw)
    assert value['status']=='warning'
    assert next(c for c in value['checks'] if c['id']=='disk')['status']=='warning'
    raw['health']['services'][0]['status']='Stopped'
    assert evaluate_readiness(raw)['status']=='critical'


def test_controller_policy_does_not_require_developer_tools_and_normalizes_cli():
    config=load_config(Path(__file__).resolve().parents[1]/'config/fleet-policy.yml')
    collector=FleetCollector(config)
    versions={'codex':'0.154.0','node':'24.21.0','npm':'11.19.0','chrome':'153.0.8010.53',
              'codex_desktop':'26.917.8451.0','sunshine':'2026.914.233613','tailscale':'1.90.0',
              'powertoys':'0.101.2362.0','office':'16.0.20430.20092','bluebeam':'21.10.0','syncthing':'2.1.5'}
    assert collector._drift('work-laptop', versions)==[]
    assert collector._clean_version('codex-cli 0.154.0')=='0.154.0'
    versions['codex']='0.155.0'
    assert collector._drift('work-laptop', versions)[0]['severity']=='info'
    assert 'edcore-ops' not in [h['id'] for h in config.hosts]
    assert all('edcore-ops' not in c.get('hosts',[]) for c in config.components.values())
