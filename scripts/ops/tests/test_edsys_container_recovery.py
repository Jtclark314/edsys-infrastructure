import importlib.util
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).parents[1] / "edsys-container-recovery.py"
SPEC = importlib.util.spec_from_file_location("edsys_container_recovery", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def container(project: str, service: str, status: str, health: str | None = None):
    state = {"Status": status}
    if health:
        state["Health"] = {"Status": health}
    return {
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
            }
        },
        "State": state,
    }


def test_running_live_restored_service_is_skipped_even_if_health_is_starting():
    project = {"name": "example", "services": ["api"]}
    index = {("example", "api"): [container("example", "api", "running", "starting")]}
    assert MODULE.services_needing_start(project, index) == []
    assert MODULE.project_state_errors(project, index) == []


def test_stopped_service_is_selected_without_selecting_running_peer():
    project = {"name": "example", "services": ["db", "api"]}
    index = {
        ("example", "db"): [container("example", "db", "running", "healthy")],
        ("example", "api"): [container("example", "api", "exited")],
    }
    assert MODULE.services_needing_start(project, index) == ["api"]


def test_unhealthy_service_fails_audit():
    project = {"name": "example", "services": ["api"]}
    index = {("example", "api"): [container("example", "api", "running", "unhealthy")]}
    assert MODULE.project_state_errors(project, index) == ["example/api: unhealthy"]


def socket_consumer(name: str, pid: int = 101):
    return {
        "Name": f"/{name}",
        "State": {"Status": "running", "Pid": pid},
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/var/run/docker.sock",
                "Destination": "/var/run/docker.sock",
            }
        ],
    }


def test_socket_consumer_with_matching_inode_is_current():
    manifest = {"docker_socket_rebind": {"consumers": ["homepage"]}}
    stats = {
        "/var/run/docker.sock": SimpleNamespace(st_dev=28, st_ino=200),
        "/proc/101/root/var/run/docker.sock": SimpleNamespace(st_dev=28, st_ino=200),
    }
    stale, errors = MODULE.docker_socket_consumer_state(
        manifest,
        inspected=[socket_consumer("homepage")],
        stat_fn=stats.__getitem__,
    )
    assert stale == []
    assert errors == []


def test_socket_consumer_with_old_inode_is_stale():
    manifest = {"docker_socket_rebind": {"consumers": ["homepage"]}}
    stats = {
        "/var/run/docker.sock": SimpleNamespace(st_dev=28, st_ino=200),
        "/proc/101/root/var/run/docker.sock": SimpleNamespace(st_dev=28, st_ino=100),
    }
    stale, errors = MODULE.docker_socket_consumer_state(
        manifest,
        inspected=[socket_consumer("homepage")],
        stat_fn=stats.__getitem__,
    )
    assert stale == ["homepage"]
    assert errors == []


def test_missing_approved_socket_consumer_fails_closed():
    manifest = {"docker_socket_rebind": {"consumers": ["homepage"]}}
    stats = {"/var/run/docker.sock": SimpleNamespace(st_dev=28, st_ino=200)}
    stale, errors = MODULE.docker_socket_consumer_state(
        manifest,
        inspected=[],
        stat_fn=stats.__getitem__,
    )
    assert stale == []
    assert errors == ["approved Docker socket consumer missing: homepage"]


def test_stopped_socket_consumer_can_defer_to_tier_recovery():
    manifest = {"docker_socket_rebind": {"consumers": ["homepage"]}}
    item = socket_consumer("homepage")
    item["State"] = {"Status": "exited", "Pid": 0}
    stats = {"/var/run/docker.sock": SimpleNamespace(st_dev=28, st_ino=200)}
    stale, errors = MODULE.docker_socket_consumer_state(
        manifest,
        inspected=[item],
        stat_fn=stats.__getitem__,
        stopped_is_error=False,
    )
    assert stale == []
    assert errors == []
