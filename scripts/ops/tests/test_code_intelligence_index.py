from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "edsys-code-intelligence-index.py"
)


@pytest.fixture(scope="module")
def indexer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("edsys_code_intelligence_index", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve the defining module through sys.modules.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def create_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Index Test"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "config",
            "user.email",
            "index-test@example.invalid",
        ],
        check=True,
    )
    (path / "tracked.py").write_text("def indexed_symbol():\n    return True\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "test"], check=True)


def test_catalog_validation_and_git_metadata(
    indexer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "sample-repo"
    create_repo(repo)
    catalog = tmp_path / "repositories.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repositories": [
                    {
                        "name": "sample-repo",
                        "source": str(repo),
                        "branch": "HEAD",
                        "enabled": True,
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(indexer, "SAFE_SOURCE_ROOTS", (tmp_path,))
    repositories = indexer.load_catalog(catalog)
    assert [item.name for item in repositories] == ["sample-repo"]
    metadata = indexer.git_metadata(repositories[0])
    assert metadata["tracked_files"] == 1
    assert len(metadata["commit"]) == 40


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "../escape", "source": "/tmp", "branch": "HEAD"},
        {"name": "valid", "source": "/tmp", "branch": "main"},
    ],
)
def test_catalog_rejects_unsafe_entries(
    indexer: ModuleType, tmp_path: Path, entry: dict
) -> None:
    catalog = tmp_path / "repositories.json"
    catalog.write_text(
        json.dumps({"schema_version": 1, "repositories": [entry]}),
        encoding="utf-8",
    )
    with pytest.raises(indexer.IndexErrorSafe):
        indexer.load_catalog(catalog)


def test_build_command_is_bounded_and_offline(
    indexer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    target = tmp_path / "index"
    observed: list[str] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        observed.extend(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(indexer, "run", fake_run)
    repository = indexer.Repository("repo", repo_path, repo_path)
    indexer.build_index([repository], target, incremental=True)
    assert "--network" in observed
    assert observed[observed.index("--network") + 1] == "none"
    assert "--cap-drop" in observed
    assert "--memory" in observed
    assert "-submodules=false" in observed
    assert "-incremental=true" in observed
    assert f"{repo_path}:/repos/repo:ro" in observed


def test_git_command_uses_only_repository_specific_trust(
    indexer: ModuleType, tmp_path: Path
) -> None:
    command = indexer.git_command(tmp_path, "status", "--short")
    assert command[:3] == ["git", "-c", f"safe.directory={tmp_path}"]
    assert "--global" not in command


def test_atomic_status_and_failure_preserve_last_good(
    indexer: ModuleType, tmp_path: Path
) -> None:
    status = tmp_path / "state" / "index-status.json"
    indexer.write_json_atomic(
        status,
        {
            "schema_version": 1,
            "success": True,
            "generated_at": "2026-07-29T00:00:00Z",
            "repositories": [{"name": "repo"}],
        },
    )
    indexer.record_failure(
        status,
        mode="incremental",
        started_at="2026-07-29T01:00:00Z",
        exc=RuntimeError("payload must not be persisted"),
    )
    payload = json.loads(status.read_text())
    assert payload["success"] is True
    assert payload["generated_at"] == "2026-07-29T00:00:00Z"
    assert payload["last_attempt"]["success"] is False
    assert "payload must not be persisted" not in status.read_text()


def test_remove_tree_refuses_state_root_or_escape(
    indexer: ModuleType, tmp_path: Path
) -> None:
    child = tmp_path / "child"
    child.mkdir()
    indexer.remove_tree(child, tmp_path)
    assert not child.exists()
    with pytest.raises(indexer.IndexErrorSafe):
        indexer.remove_tree(tmp_path, tmp_path)
    with pytest.raises(indexer.IndexErrorSafe):
        indexer.remove_tree(tmp_path.parent / "outside", tmp_path)
