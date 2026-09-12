import os
from pathlib import Path
import subprocess

import pytest


SOURCE = Path(__file__).parents[1] / "edsys-reboot-acceptance.sh"
FUNCTIONS = SOURCE.read_text().split('action="${1:-}"')[0]


def verify(tmp_path, before, actual, query_exit=0):
    if before is not None:
        (tmp_path / "docker-live-restore.before").write_text(before + "\n")
    script = FUNCTIONS + """
docker() { printf '%s\\n' "$TEST_ACTUAL"; return "$TEST_QUERY_EXIT"; }
verify_docker_live_restore "$TEST_RUN_DIR"
"""
    return subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "TEST_ACTUAL": actual,
            "TEST_QUERY_EXIT": str(query_exit),
            "TEST_RUN_DIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("value", ["true", "false"])
def test_unchanged_live_restore_passes(tmp_path, value):
    assert verify(tmp_path, value, value).returncode == 0


@pytest.mark.parametrize("before,actual", [("true", "false"), ("false", "true")])
def test_changed_live_restore_fails(tmp_path, before, actual):
    assert verify(tmp_path, before, actual).returncode != 0


def test_missing_baseline_fails(tmp_path):
    assert verify(tmp_path, None, "true").returncode != 0


@pytest.mark.parametrize("actual", ["", "unknown", "true\\nfalse"])
def test_invalid_docker_response_fails(tmp_path, actual):
    assert verify(tmp_path, actual, actual).returncode != 0


def test_failed_docker_query_fails_even_with_matching_output(tmp_path):
    assert verify(tmp_path, "true", "true", query_exit=1).returncode != 0
