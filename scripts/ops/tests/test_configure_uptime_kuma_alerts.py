import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "configure-uptime-kuma-alerts.py"
SPEC = importlib.util.spec_from_file_location("configure_uptime_kuma_alerts", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def configure_pushover_environment(monkeypatch):
    monkeypatch.setenv("KUMA_ALERT_PROVIDER", "pushover")
    monkeypatch.setenv("PUSHOVER_USER_KEY", "example-user-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "example-app-token")


def test_pushover_ttl_is_empty_by_default(monkeypatch):
    configure_pushover_environment(monkeypatch)
    monkeypatch.delenv("PUSHOVER_TTL", raising=False)
    assert MODULE.build_config()["pushoverttl"] == ""


def test_pushover_positive_ttl_is_preserved(monkeypatch):
    configure_pushover_environment(monkeypatch)
    monkeypatch.setenv("PUSHOVER_TTL", "300")
    assert MODULE.build_config()["pushoverttl"] == "300"


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_pushover_rejects_invalid_ttl(monkeypatch, value):
    configure_pushover_environment(monkeypatch)
    monkeypatch.setenv("PUSHOVER_TTL", value)
    with pytest.raises(SystemExit, match="PUSHOVER_TTL must be a positive integer"):
        MODULE.build_config()
