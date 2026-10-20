import importlib.util
import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


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
        root_stat_fn=lambda root, path: stats[str(root) + path],
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
        root_stat_fn=lambda root, path: stats[str(root) + path],
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


def identity(result):
    return result.st_dev, result.st_ino


@pytest.mark.parametrize("target", ["/run", "../run"])
def test_socket_path_resolves_absolute_and_relative_var_run_links_in_root(tmp_path, target):
    root = tmp_path / "container"
    (root / "var").mkdir(parents=True)
    (root / "run").mkdir()
    socket_path = root / "run/docker.sock"
    socket_path.touch()
    (root / "var/run").symlink_to(target)

    assert identity(MODULE.stat_in_root(root, "/var/run/docker.sock")) == identity(socket_path.stat())


def test_real_var_run_directory_is_not_replaced_with_run(tmp_path):
    root = tmp_path / "container"
    (root / "var/run").mkdir(parents=True)
    (root / "run").mkdir()
    expected = root / "var/run/docker.sock"
    expected.touch()
    (root / "run/docker.sock").touch()

    assert identity(MODULE.stat_in_root(root, "/var/run/docker.sock")) == identity(expected.stat())
    assert identity(expected.stat()) != identity((root / "run/docker.sock").stat())


def test_absolute_link_cannot_false_green_against_host_socket(tmp_path):
    host_run = tmp_path / "host-run"
    host_run.mkdir()
    host_socket = host_run / "docker.sock"
    host_socket.touch()
    root = tmp_path / "container"
    (root / "var").mkdir(parents=True)
    (root / "var/run").symlink_to(host_run)
    rooted_run = root / str(host_run).lstrip("/")
    rooted_run.mkdir(parents=True)
    stale_socket = rooted_run / "docker.sock"
    stale_socket.touch()

    # The old concatenated host stat escapes the fake process root and falsely
    # reports the host socket. The real rooted resolver must select this stale
    # consumer without running Docker, a recovery hook or any restart command.
    assert identity((root / "var/run/docker.sock").stat()) == identity(host_socket.stat())
    stale, errors = MODULE.docker_socket_consumer_state(
        {"docker_socket_rebind": {"consumers": ["homepage"]}},
        inspected=[socket_consumer("homepage")],
        stat_fn=lambda path: host_socket.stat(),
        root_stat_fn=lambda process_root, path: MODULE.stat_in_root(root, path),
    )
    assert stale == ["homepage"]
    assert errors == []


def test_nested_and_terminal_socket_links_stay_in_container_root(tmp_path):
    root = tmp_path / "container"
    (root / "var").mkdir(parents=True)
    (root / "run/sockets").mkdir(parents=True)
    expected = root / "run/sockets/engine.sock"
    expected.touch()
    (root / "var/run").symlink_to("../runtime")
    (root / "runtime").symlink_to("/run")
    (root / "run/docker.sock").symlink_to("sockets/engine.sock")

    assert identity(MODULE.stat_in_root(root, "/var/run/docker.sock")) == identity(expected.stat())


def test_symlink_dot_dot_cannot_escape_process_root(tmp_path):
    root = tmp_path / "container"
    (root / "var").mkdir(parents=True)
    (root / "run").mkdir()
    expected = root / "run/docker.sock"
    expected.touch()
    (root / "var/run").symlink_to("../../../../run")

    assert identity(MODULE.stat_in_root(root, "/var/run/docker.sock")) == identity(expected.stat())


def test_symlink_loop_fails_closed_without_leaking_descriptors(tmp_path):
    root = tmp_path / "container"
    (root / "var").mkdir(parents=True)
    (root / "var/run").symlink_to("/loop")
    (root / "loop").symlink_to("/var/run")
    before = len(os.listdir("/proc/self/fd"))

    with pytest.raises(OSError) as error:
        MODULE.stat_in_root(root, "/var/run/docker.sock")
    assert error.value.errno == errno.ELOOP
    assert len(os.listdir("/proc/self/fd")) == before


def test_missing_container_socket_is_an_error_not_a_restart_target(tmp_path):
    root = tmp_path / "container"
    (root / "var").mkdir(parents=True)
    (root / "var/run").symlink_to("/missing")
    stale, errors = MODULE.docker_socket_consumer_state(
        {"docker_socket_rebind": {"consumers": ["homepage"]}},
        inspected=[socket_consumer("homepage")],
        stat_fn=lambda path: SimpleNamespace(st_dev=28, st_ino=100),
        root_stat_fn=lambda process_root, path: MODULE.stat_in_root(root, path),
    )
    assert stale == []
    assert errors == ["homepage: container Docker socket stat failed: FileNotFoundError"]
