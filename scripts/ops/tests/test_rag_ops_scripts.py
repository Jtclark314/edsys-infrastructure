from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


OPS_DIR = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = OPS_DIR / name
    module_name = name.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rag_watch_debouncer_coalesces_quick_saves() -> None:
    watcher = load_script("edsys-rag-watch-sync.py")
    now = {"value": 0.0}
    calls = []

    def monotonic() -> float:
        return now["value"]

    def sleeper(seconds: float) -> None:
        now["value"] += seconds

    debouncer = watcher.DebouncedTrigger(8.0, monotonic=monotonic, sleeper=sleeper)
    assert debouncer.run(lambda: calls.append("sync")) is True
    assert debouncer.run(lambda: calls.append("sync")) is False
    now["value"] += 8.1
    assert debouncer.run(lambda: calls.append("sync")) is True
    assert calls == ["sync", "sync"]


def test_rag_enrichment_writes_metadata_sqlite(tmp_path) -> None:
    enrich = load_script("edsys-rag-enrich-metadata.py")
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "Demo RAG Summary.md").write_text(
        "---\nstatus: current\nsource_systems:\n  - EdSys\n---\n# Demo\n\nCurrent service note.",
        encoding="utf-8",
    )
    db_path = tmp_path / "metadata.sqlite"
    result = enrich.build(mirror, db_path)
    assert result["document_count"] == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("select title, status, summary from documents").fetchone()
    assert row == ("Demo", "current", "Current service note.")


def test_anythingllm_verifier_accepts_single_healthy_workspace() -> None:
    verifier = load_script("anythingllm-rag-verify.py")
    workspace = dict(verifier.EXPECTED_WORKSPACE)
    stats = {
        "document_count": 41,
        "non_managed_document_count": 0,
        "documents_without_vectors": 0,
        "vector_count": 386,
        "orphan_vector_count": 0,
    }
    manifest = {
        "workspaceSlug": "edsys-rag-current",
        "documents": {f"doc-{index}": {} for index in range(41)},
    }
    assert verifier.validate_inventory([workspace], stats, manifest) == []


def test_anythingllm_verifier_rejects_extra_workspace_and_drift() -> None:
    verifier = load_script("anythingllm-rag-verify.py")
    workspace = dict(verifier.EXPECTED_WORKSPACE)
    workspace["similarityThreshold"] = 0.25
    stats = {
        "document_count": 1,
        "non_managed_document_count": 0,
        "documents_without_vectors": 0,
        "vector_count": 1,
        "orphan_vector_count": 0,
    }
    manifest = {"workspaceSlug": "edsys-rag-current", "documents": {"doc": {}}}

    failures = verifier.validate_inventory([workspace], stats, manifest)
    assert any("similarityThreshold" in failure for failure in failures)
    assert verifier.validate_inventory([workspace, workspace], stats, manifest) == [
        "expected one AnythingLLM workspace, found 2"
    ]
