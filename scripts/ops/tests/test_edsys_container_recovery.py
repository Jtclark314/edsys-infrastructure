import importlib.util
from pathlib import Path


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
