#!/usr/bin/env python3
"""Read-only integrity and retrieval verification for EdSys-RAG-Current."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


EXPECTED_WORKSPACE = {
    "name": "EdSys-RAG-Current",
    "slug": "edsys-rag-current",
    "chatProvider": "litellm",
    "chatModel": "edsys-chat-cloud",
    "chatMode": "query",
    "topN": 6,
    "similarityThreshold": 0.65,
    "openAiHistory": 8,
    "openAiTemp": 0.1,
    "vectorSearchMode": "default",
    "router_id": None,
}

QUICK_CASES = [
    {
        "id": "workhorse-role",
        "question": "What is the current role of 9950x in EdSys?",
        "expected_source_terms": ["9950x", "workhorse"],
        "min_expected_source_terms": 2,
    },
    {
        "id": "anythingllm-embedding-route",
        "question": "How are AnythingLLM embeddings created and stored in EdSys?",
        "expected_source_terms": ["edsys-embeddings-fast", "Infinity", "LanceDB"],
        "min_expected_source_terms": 2,
    },
]

QUICK_NEGATIVE_QUERIES = ["What is the television series Silo about?"]

FULL_NEGATIVE_QUERIES = [
    "How do I bake a chocolate layer cake?",
    "Who won the most recent World Cup?",
    "Explain quantum chromodynamics to a graduate student.",
    "Write a poem about autumn leaves.",
    "What will the weather be tomorrow?",
    "Should I invest in an index fund?",
    "What medicine should I take for a migraine?",
    "What is the television series Silo about?",
    "How do I repair a dishwasher pump?",
    "Translate good morning into Japanese.",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Run two positive sentinels and one negative control")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    parser.add_argument("--api-url", default=os.getenv("ANYTHINGLLM_API_URL", "http://127.0.0.1:3002"))
    parser.add_argument("--database", type=Path, default=Path("/mnt/ai-store/anythingllm/anythingllm.db"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("/mnt/ai-store/rag/state/anythingllm-rag-current-manifest.json"),
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=Path("/home/jeremy/code/EdSys-Master/data/rag-golden-queries.yml"),
    )
    parser.add_argument("--score-threshold", type=float, default=0.65)
    parser.add_argument("--top-n", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def read_api_key(database: Path) -> str:
    configured = os.getenv("ANYTHINGLLM_API_KEY", "").strip()
    if configured:
        return configured

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "select secret from api_keys where secret is not null and length(secret) > 0 order by id limit 1"
        ).fetchone()
    if not row:
        raise RuntimeError("AnythingLLM API key is not configured")
    return str(row[0])


def api_post(api_url: str, api_key: str, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    base = api_url.rstrip("/")
    if not base.endswith("/api"):
        base += "/api"
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"AnythingLLM API returned HTTP {exc.code} for {path}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AnythingLLM API is unreachable for {path}: {exc.reason}") from exc


def load_inventory(database: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    uri = f"file:{database}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        workspaces = [
            dict(row)
            for row in connection.execute(
                """
                select name, slug, chatProvider, chatModel, chatMode, topN,
                       similarityThreshold, openAiHistory, openAiTemp,
                       vectorSearchMode, router_id
                from workspaces
                order by id
                """
            )
        ]
        workspace_stats = connection.execute(
            """
            select
              count(*) as document_count,
              sum(case when json_extract(wd.metadata, '$.docSource') = 'EdSysVault_RAG' then 0 else 1 end)
                as non_managed_document_count,
              sum(case when not exists (
                select 1 from document_vectors dv where dv.docId = wd.docId
              ) then 1 else 0 end) as documents_without_vectors
            from workspace_documents wd
            join workspaces w on w.id = wd.workspaceId
            where w.slug = 'edsys-rag-current'
            """
        ).fetchone()
        vector_count = connection.execute(
            """
            select count(*)
            from document_vectors dv
            where exists (
              select 1
              from workspace_documents wd
              join workspaces w on w.id = wd.workspaceId
              where w.slug = 'edsys-rag-current' and wd.docId = dv.docId
            )
            """
        ).fetchone()[0]
        orphan_vector_count = connection.execute(
            """
            select count(*)
            from document_vectors dv
            left join workspace_documents wd on wd.docId = dv.docId
            where wd.id is null
            """
        ).fetchone()[0]

    stats = {
        "document_count": int(workspace_stats["document_count"] or 0),
        "non_managed_document_count": int(workspace_stats["non_managed_document_count"] or 0),
        "documents_without_vectors": int(workspace_stats["documents_without_vectors"] or 0),
        "vector_count": int(vector_count or 0),
        "orphan_vector_count": int(orphan_vector_count or 0),
    }
    return workspaces, stats


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float):
        try:
            return abs(float(actual) - expected) < 0.000001
        except (TypeError, ValueError):
            return False
    return actual == expected


def validate_inventory(
    workspaces: list[dict[str, Any]], stats: dict[str, int], manifest: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    if len(workspaces) != 1:
        failures.append(f"expected one AnythingLLM workspace, found {len(workspaces)}")
        return failures

    workspace = workspaces[0]
    for field, expected in EXPECTED_WORKSPACE.items():
        actual = workspace.get(field)
        if not values_equal(actual, expected):
            failures.append(f"workspace {field} expected {expected!r}, found {actual!r}")

    if stats["document_count"] <= 0:
        failures.append("workspace contains no documents")
    if stats["non_managed_document_count"] != 0:
        failures.append(f"workspace contains {stats['non_managed_document_count']} non-managed documents")
    if stats["documents_without_vectors"] != 0:
        failures.append(f"workspace contains {stats['documents_without_vectors']} documents without vectors")
    if stats["vector_count"] < stats["document_count"]:
        failures.append("workspace vector count is lower than document count")
    if stats["orphan_vector_count"] != 0:
        failures.append(f"AnythingLLM contains {stats['orphan_vector_count']} orphan vectors")

    if manifest.get("workspaceSlug") != EXPECTED_WORKSPACE["slug"]:
        failures.append("manifest workspaceSlug is not edsys-rag-current")
    documents = manifest.get("documents")
    if not isinstance(documents, dict):
        failures.append("manifest documents field is invalid")
    elif len(documents) != stats["document_count"]:
        failures.append(
            f"manifest has {len(documents)} documents but workspace has {stats['document_count']}"
        )
    return failures


def load_full_cases(path: Path) -> list[dict[str, Any]]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required for the full golden-query verification") from exc

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    cases: list[dict[str, Any]] = []
    for case in raw.get("cases", []):
        cases.append(
            {
                "id": case["id"],
                "question": case["question"],
                "expected_source_terms": case.get("expected_source_terms", []),
                "min_expected_source_terms": int(
                    case.get("acceptance", {}).get("min_expected_source_terms", 1)
                ),
            }
        )
    if not cases:
        raise RuntimeError(f"No golden queries found in {path}")
    return cases


def searchable_text(results: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for result in results:
        metadata = result.get("metadata") or {}
        values.extend(
            [
                str(result.get("text", "")),
                str(metadata.get("title", "")),
                str(metadata.get("chunkSource", "")),
            ]
        )
    return "\n".join(values).lower()


def evaluate_retrieval(
    api_url: str,
    api_key: str,
    cases: list[dict[str, Any]],
    negative_queries: list[str],
    top_n: int,
    score_threshold: float,
    timeout: float,
) -> dict[str, Any]:
    failures: list[str] = []
    latencies: list[float] = []

    for case in cases:
        started = time.perf_counter()
        response = api_post(
            api_url,
            api_key,
            "/v1/workspace/edsys-rag-current/vector-search",
            {"query": case["question"], "topN": top_n, "scoreThreshold": score_threshold},
            timeout,
        )
        latencies.append(time.perf_counter() - started)
        results = response.get("results") or []
        haystack = searchable_text(results)
        hits = sum(str(term).lower() in haystack for term in case["expected_source_terms"])
        if not results or hits < case["min_expected_source_terms"]:
            failures.append(
                f"{case['id']}: results={len(results)} expected_source_term_hits={hits}"
            )

    for index, query in enumerate(negative_queries, start=1):
        started = time.perf_counter()
        response = api_post(
            api_url,
            api_key,
            "/v1/workspace/edsys-rag-current/vector-search",
            {"query": query, "topN": top_n, "scoreThreshold": score_threshold},
            timeout,
        )
        latencies.append(time.perf_counter() - started)
        results = response.get("results") or []
        if results:
            failures.append(f"negative-{index}: unrelated query returned {len(results)} results")

    ordered = sorted(latencies)
    median = ordered[len(ordered) // 2] if ordered else 0.0
    return {
        "positive_passed": len(cases) - sum(not failure.startswith("negative-") for failure in failures),
        "positive_total": len(cases),
        "negative_passed": len(negative_queries)
        - sum(failure.startswith("negative-") for failure in failures),
        "negative_total": len(negative_queries),
        "median_latency_seconds": round(median, 4),
        "failures": failures,
    }


def render_report(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    status = "PASS" if report["ok"] else "FAIL"
    stats = report.get("inventory", {})
    retrieval = report.get("retrieval", {})
    print(
        f"{status} EdSys-RAG-Current workspaces={report.get('workspace_count', 0)} "
        f"documents={stats.get('document_count', 0)} vectors={stats.get('vector_count', 0)}"
    )
    if retrieval:
        print(
            "retrieval "
            f"positive={retrieval['positive_passed']}/{retrieval['positive_total']} "
            f"negative={retrieval['negative_passed']}/{retrieval['negative_total']} "
            f"median={retrieval['median_latency_seconds']:.4f}s"
        )
    for failure in report.get("failures", []):
        print(f"FAIL {failure}")


def main() -> int:
    args = parse_args()
    report: dict[str, Any] = {"ok": False, "mode": "quick" if args.quick else "full"}
    try:
        workspaces, stats = load_inventory(args.database)
        manifest = load_manifest(args.manifest)
        failures = validate_inventory(workspaces, stats, manifest)
        api_key = read_api_key(args.database)
        cases = QUICK_CASES if args.quick else load_full_cases(args.queries)
        negatives = QUICK_NEGATIVE_QUERIES if args.quick else FULL_NEGATIVE_QUERIES
        retrieval = evaluate_retrieval(
            args.api_url,
            api_key,
            cases,
            negatives,
            args.top_n,
            args.score_threshold,
            args.timeout,
        )
        failures.extend(retrieval["failures"])
        report.update(
            {
                "ok": not failures,
                "workspace_count": len(workspaces),
                "inventory": stats,
                "retrieval": retrieval,
                "failures": failures,
            }
        )
    except (OSError, RuntimeError, sqlite3.Error, json.JSONDecodeError) as exc:
        report["failures"] = [str(exc)]

    render_report(report, args.json)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
