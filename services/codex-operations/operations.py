"""Bounded host-local reads over established EdSys service routes.

No caller-selected commands, URLs, credential paths, device actions or models.
Returned service content is untrusted data, never operational authority.
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[2]
HA_HELPER = SOURCE_ROOT / "scripts/ops/edcore-ha-api.py"
RAG_HELPER = SOURCE_ROOT / "scripts/ops/anythingllm-rag-verify.py"
RAG_DATABASE = Path("/mnt/ai-store/anythingllm/anythingllm.db")
PLEX_PREFERENCES = Path("/srv/plex-config/Library/Application Support/Plex Media Server/Preferences.xml")
BASES = {"frigate": "http://192.168.50.50:5000", "plex": "http://192.168.50.50:32400",
         "ollama": "http://192.168.50.50:11434", "rag": "http://127.0.0.1:3002"}
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 2400
ENTITY = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_ -]{1,80}\Z")
SECRET_KEY = re.compile(r"(?:password|passwd|secret|credential|authorization|api.?key|(?:^|_)token(?:$|_))", re.I)
SENSITIVE_TEXT = re.compile(r"(?:\bBearer\s+\S+|\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}|\b(?:api[_ -]?key|password|access[_ -]?token)\s*[:=]\s*\S+)", re.I)


class OperationsError(RuntimeError):
    """A deliberately sanitized, user-facing operational failure."""


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise OperationsError("Existing approved helper is unavailable")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def bounded_int(value: int, minimum: int, maximum: int, label: str = "limit") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise OperationsError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def text(value: str, maximum: int = 240, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise OperationsError(f"Text must contain 1..{maximum} characters" if not empty else f"Text must contain at most {maximum} characters")
    if any(ord(c) < 32 and c not in "\t\n" for c in value) or SENSITIVE_TEXT.search(value):
        raise OperationsError("Credential-shaped text or control characters are not accepted")
    return value.strip()


def sanitize(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {str(k): "[redacted]" if SECRET_KEY.search(str(k)) else sanitize(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", value, flags=re.I)
        value = re.sub(r"([?&](?:token|access_token|api_key|apikey|key|password|signature)=)[^&#\s]+", r"\1[redacted]", value, flags=re.I)
        return SENSITIVE_TEXT.sub("[redacted]", value)[:MAX_TEXT]
    return value


def pick(value: dict, fields: tuple[str, ...]) -> dict:
    return sanitize({k: value[k] for k in fields if k in value and isinstance(value[k], (str, int, float, bool, type(None)))})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(service: str, path: str, *, query: dict | None = None, headers: dict | None = None,
            payload: dict | None = None, xml: bool = False, plain: bool = False) -> Any:
    """Internal fixed-route transport. Only vector retrieval uses read-only POST."""
    if service not in BASES or not path.startswith("/") or "?" in path or ".." in path:
        raise OperationsError("Unsupported fixed service route")
    url = BASES[service] + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None if payload is None else json.dumps(payload).encode()
    if payload is not None and not (service == "rag" and path == "/api/v1/workspace/edsys-rag-current/vector-search"):
        raise OperationsError("Only managed vector retrieval permits a read-only POST")
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/xml" if xml else "application/json",
                                 **({"Content-Type": "application/json"} if data else {}), **(headers or {})},
                                 method="POST" if data else "GET")
    try:
        # Fixed LAN/loopback destinations; do not inherit proxy credentials or follow redirects.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=30) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise OperationsError(f"{service} response exceeded the bounded read limit")
            if xml:
                return ET.fromstring(body)
            if plain:
                return body.decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        raise OperationsError(f"{service} API returned HTTP {exc.code}; no alternate credentials or redirects attempted") from None
    except OperationsError:
        raise
    except (OSError, ValueError, ET.ParseError, urllib.error.URLError):
        raise OperationsError(f"{service} read failed or returned an unsupported response") from None


def ha(payload: dict) -> Any:
    try:
        value = module(HA_HELPER, "edsys_ops_ha").execute("pve-node3", payload)
        return sanitize(value["result"])
    except Exception:
        raise OperationsError("Home Assistant approved read route failed; no retry or alternate credentials attempted") from None


def ha_inventory() -> dict:
    value = ha({"operation": "inspect"})
    return {**pick(value, ("core_version", "entity_count", "unavailable_count", "service_domains", "service_count", "transport")),
            "domains": {k: v for k, v in value.get("domains", {}).items() if re.fullmatch(r"[a-z_]+", k) and isinstance(v, int)}}


def ha_entities(domain: str = "", query: str = "", limit: int = 20) -> dict:
    bounded_int(limit, 1, 50)
    text(domain, 40, empty=True)
    if domain and not re.fullmatch(r"[a-z_][a-z0-9_]*", domain):
        raise OperationsError("domain must be a Home Assistant domain identifier")
    query = text(query, 120, empty=True)
    value = ha({"operation": "entities", "domain": domain or None, "filter": query or None, "limit": limit})
    return {"matching_count": value.get("matching_count"), "limited": value.get("limited"),
            "entities": [pick(x, ("entity_id", "state", "name", "last_updated")) for x in value.get("entities", [])[:limit]]}


def entity(value: str) -> str:
    if not isinstance(value, str) or len(value) > 160 or not ENTITY.fullmatch(value):
        raise OperationsError("An explicit domain.entity_id is required")
    return value


def ha_state(entity_id: str) -> dict:
    entity_id = entity(entity_id)
    value = ha({"operation": "state", "method": "GET", "path": "api/states/" + entity_id})
    return {**pick(value, ("entity_id", "state", "last_changed", "last_updated")),
            "name": sanitize(value.get("attributes", {}).get("friendly_name", ""))}


def ha_history(entity_id: str, hours: int = 6, limit: int = 100) -> dict:
    entity_id = entity(entity_id)
    bounded_int(hours, 1, 24, "hours")
    bounded_int(limit, 1, 200)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    path = "api/history/period/" + urllib.parse.quote(start.isoformat(), safe="")
    path += "?" + urllib.parse.urlencode({"filter_entity_id": entity_id, "end_time": end.isoformat(),
                                         "minimal_response": "", "no_attributes": "", "significant_changes_only": ""})
    value = ha({"operation": "history", "method": "GET", "path": path})
    rows = [row for group in value if isinstance(group, list) for row in group if isinstance(row, dict)]
    return {"entity_id": entity_id, "start": start.isoformat(), "end": end.isoformat(), "returned_count": min(len(rows), limit),
            "limited": len(rows) > limit, "states": [pick(x, ("state", "last_changed", "last_updated")) for x in rows[:limit]]}


def frigate_health() -> dict:
    version = request("frigate", "/api/version", plain=True)
    stats = request("frigate", "/api/stats")
    cameras = stats.get("cameras", {})
    return {"version": sanitize(version), "camera_count": len(cameras),
            "cameras": [{"name": sanitize(k), **pick(v, ("camera_fps", "process_fps", "skipped_fps", "detection_fps"))}
                        for k, v in list(cameras.items())[:50]],
            "detectors": [{"name": sanitize(k), **pick(v, ("inference_speed", "detection_start"))}
                          for k, v in list(stats.get("detectors", {}).items())[:20]],
            "uptime_seconds": stats.get("service", {}).get("uptime")}


def frigate_events(camera: str = "", label: str = "", hours: int = 24, limit: int = 20) -> dict:
    bounded_int(hours, 1, 168, "hours")
    bounded_int(limit, 1, 50)
    for value in (camera, label):
        if value and (not isinstance(value, str) or not IDENTIFIER.fullmatch(value)):
            raise OperationsError("camera and label must be bounded names from the returned inventory/events")
    params = {"limit": limit, "after": round(time.time() - hours * 3600, 3), "include_thumbnails": 0}
    if camera:
        params["camera"] = camera
    if label:
        params["label"] = label
    value = request("frigate", "/api/events", query=params)
    if not isinstance(value, list):
        raise OperationsError("Frigate returned an unsupported event response")
    return {"count": min(len(value), limit), "window_hours": hours, "events": [
        pick(x, ("id", "camera", "label", "start_time", "end_time", "has_clip", "has_snapshot", "false_positive")) for x in value[:limit]]}


def plex_key() -> str:
    try:
        if PLEX_PREFERENCES.stat().st_size > MAX_BYTES:
            raise ValueError()
        token = ET.parse(PLEX_PREFERENCES).getroot().attrib.get("PlexOnlineToken", "")
        if not token:
            raise ValueError()
        return token
    except (OSError, ValueError, ET.ParseError):
        raise OperationsError("Existing Plex server credential is unavailable; no credential fallback attempted") from None


def plex(path: str, query: dict | None = None) -> ET.Element:
    key = plex_key()
    root = request("plex", path, query=query, headers={"X-Plex-Token": key}, xml=True)
    # Even an upstream title containing the active credential must not echo it.
    for node in root.iter():
        node.attrib = sanitize(node.attrib, (key,))
        if node.text:
            node.text = sanitize(node.text, (key,))
    return root


def plex_libraries() -> dict:
    root = plex("/library/sections")
    rows = [pick(x.attrib, ("key", "title", "type", "language", "updatedAt")) for x in root.findall("Directory")]
    return {"count": len(rows), "libraries": rows[:50], "limited": len(rows) > 50}


PLEX_FIELDS = ("ratingKey", "title", "type", "year", "duration", "grandparentTitle", "parentTitle", "index", "parentIndex", "viewCount", "addedAt", "updatedAt")


def plex_search(query: str, limit: int = 15) -> dict:
    query = text(query, 160)
    bounded_int(limit, 1, 40)
    root = plex("/hubs/search", {"query": query, "limit": limit, "includeCollections": 0, "includeExternalMedia": 0})
    rows = [pick(x.attrib, PLEX_FIELDS) for x in root.iter() if x.tag in {"Video", "Track", "Directory"} and x.attrib.get("ratingKey")]
    return {"results": rows[:limit], "returned_count": min(len(rows), limit), "limited": len(rows) > limit}


def plex_metadata(rating_key: str) -> dict:
    if not isinstance(rating_key, str) or not re.fullmatch(r"[0-9]{1,12}", rating_key):
        raise OperationsError("rating_key must be the numeric ID returned by Plex search")
    root = plex("/library/metadata/" + rating_key)
    rows = [pick(x.attrib, PLEX_FIELDS + ("summary", "contentRating", "studio")) for x in root if x.attrib.get("ratingKey")]
    return {"items": rows[:1], "count": min(len(rows), 1)}


def ollama_models() -> dict:
    version = request("ollama", "/api/version")
    inventory = request("ollama", "/api/tags")
    running = request("ollama", "/api/ps")
    fields = ("name", "model", "modified_at", "size", "digest", "size_vram", "expires_at")
    def models(rows):
        return [{**pick(x, fields), "details": pick(x.get("details", {}), ("family", "parameter_size", "quantization_level", "format"))} for x in rows[:50]]
    return {"version": version.get("version"), "models": models(inventory.get("models", [])),
            "running_models": models(running.get("models", [])), "inference_exposed": False}


def rag_local_embeddings() -> None:
    """Fail closed if the established local embedding selection changes."""
    try:
        result = subprocess.run(["docker", "inspect", "--format", "{{json .Config.Env}}", "anythingllm"],
                                capture_output=True, text=True, timeout=10, check=True)
        if len(result.stdout) > MAX_BYTES:
            raise ValueError()
        env = dict(x.split("=", 1) for x in json.loads(result.stdout) if "=" in x)
        if (env.get("EMBEDDING_ENGINE"), env.get("EMBEDDING_MODEL_PREF"), env.get("LITE_LLM_BASE_PATH")) != (
                "litellm", "edsys-embeddings-fast", "http://192.168.50.50:4000/v1"):
            raise ValueError()
        with sqlite3.connect(f"file:{RAG_DATABASE}?mode=ro", uri=True) as connection:
            # A database override needs separate review; do not silently honor a new provider.
            count = connection.execute("select count(*) from system_settings where lower(label) like '%embed%'").fetchone()[0]
            if count:
                raise ValueError()
    except Exception:
        raise OperationsError("Managed local embedding guard did not pass; vector query was not sent") from None


def rag_inventory() -> dict:
    try:
        workspaces, stats = module(RAG_HELPER, "edsys_ops_rag").load_inventory(RAG_DATABASE)
        rows = [pick(x, ("name", "slug", "chatProvider", "chatModel", "chatMode", "topN", "similarityThreshold", "router_id"))
                for x in workspaces if x.get("slug") == "edsys-rag-current"]
        return {"workspace": rows[0] if len(rows) == 1 else None, "statistics": stats, "retrieval_only": True,
                "generation_exposed": False}
    except Exception:
        raise OperationsError("Managed RAG inventory is unavailable through the existing read-only helper") from None


def rag_search(query: str, limit: int = 4, score_threshold: float = 0.65) -> dict:
    query = text(query, 800)
    bounded_int(limit, 1, 6)
    if isinstance(score_threshold, bool) or not isinstance(score_threshold, (int, float)) or not math.isfinite(score_threshold) or not 0.5 <= score_threshold <= 1:
        raise OperationsError("score_threshold must be a finite number from 0.5 to 1")
    rag_local_embeddings()
    try:
        key = module(RAG_HELPER, "edsys_ops_rag").read_api_key(RAG_DATABASE)
    except Exception:
        raise OperationsError("Existing managed RAG credential is unavailable; no alternate credential attempted") from None
    response = request("rag", "/api/v1/workspace/edsys-rag-current/vector-search", headers={"Authorization": "Bearer " + key},
                       payload={"query": query, "topN": limit, "scoreThreshold": score_threshold})
    rows = []
    for result in (response.get("results") or [])[:limit]:
        meta = result.get("metadata") or {}
        rows.append({"text": str(result.get("text", ""))[:MAX_TEXT], "score": result.get("score"),
                     "source": pick(meta, ("title", "chunkSource", "docSource", "published", "source"))})
    return sanitize({"workspace": "edsys-rag-current", "retrieval_only": True, "count": len(rows), "results": rows}, (key,))
