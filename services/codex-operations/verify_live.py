"""Explicit live stdio SDK acceptance, emitting only sanitized result summaries."""
import argparse
import asyncio
import json
import base64
import hashlib
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import operations as ops


WRITES = {"ha_set_power", "ha_light", "ha_media", "ha_scene"}
EXPECTED = {"action_targets", "camera_snapshot", "camera_event_snapshot", "camera_clip", *WRITES,
            "ha_inventory", "ha_entities", "ha_state", "ha_history", "frigate_health", "frigate_events",
            "plex_libraries", "plex_search", "plex_metadata", "ollama_models", "rag_inventory", "rag_search"}


def value(result):
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    raise AssertionError("Tool returned no structured/text JSON response")


async def verify(qualify_actions=False, qualify_cameras=False):
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "transport": "stdio MCP SDK", "calls": []}
    launcher = str(Path(__file__).resolve().with_name("edsys-operations-mcp"))
    async with stdio_client(StdioServerParameters(command=launcher, args=[])) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listing = await session.list_tools()
            names = {item.name for item in listing.tools}
            assert names == EXPECTED, "Unexpected MCP tool surface"
            assert all(item.annotations and item.annotations.read_only_hint is (item.name not in WRITES)
                       and item.annotations.destructive_hint is False
                       and item.annotations.open_world_hint is False for item in listing.tools), "Unsafe/missing tool annotations"
            report.update(tool_count=len(names), tools=sorted(names), write_tools=sorted(WRITES))

            async def read(name, arguments=None):
                result = await session.call_tool(name, arguments or {}, read_timeout_seconds=300)
                if result.is_error:
                    report["calls"].append({"tool": name, "ok": False, "error": "Sanitized MCP tool error; no upstream content logged"})
                    raise AssertionError(name + " returned an error")
                data = value(result)
                report["calls"].append({"tool": name, "ok": True, "response_keys": sorted(data)})
                return data

            inventory = await read("ha_inventory")
            report["ha"] = {k: inventory[k] for k in ("core_version", "entity_count", "service_count")}
            await read("ha_entities", {"domain": "weather", "limit": 2})
            state = await read("ha_state", {"entity_id": "sun.sun"})
            assert state.get("entity_id") == "sun.sun"
            history = await read("ha_history", {"entity_id": "sun.sun", "hours": 1, "limit": 5})
            assert history["returned_count"] > 0, "History returned no initial/recent state"
            report["ha"]["history_rows"] = history["returned_count"]
            frigate = await read("frigate_health")
            report["frigate"] = {"version": frigate["version"], "camera_count": frigate["camera_count"]}
            events = await read("frigate_events", {"limit": 50})
            targets = await read("action_targets", {"limit": 1})
            report["eligible_action_targets"] = targets["matching_count"]
            libraries = await read("plex_libraries")
            search = await read("plex_search", {"query": "Silo", "limit": 2})
            assert libraries["count"] > 0 and search["results"], "Plex libraries/search empty"
            metadata = await read("plex_metadata", {"rating_key": search["results"][0]["ratingKey"]})
            assert metadata["count"] == 1
            report["plex"] = {"library_count": libraries["count"], "search_rows": search["returned_count"], "metadata_rows": metadata["count"]}
            models = await read("ollama_models")
            assert models["models"] and models["inference_exposed"] is False
            report["ollama"] = {"version": models["version"], "model_count": len(models["models"])}
            rag = await read("rag_inventory")
            retrieval = await read("rag_search", {"query": "What is the current role of 9950x in EdSys?", "limit": 2})
            assert retrieval["retrieval_only"] is True and retrieval["count"] > 0, "Managed retrieval empty"
            report["rag"] = {**rag["statistics"], "retrieved_rows": retrieval["count"], "generation_exposed": rag["generation_exposed"]}
            negatives = (("ha_state", {"entity_id": "../secrets"}), ("rag_search", {"query": "EdSys", "limit": 7}),
                         ("ha_set_power", {"entity_id": "lock.invalid", "state": "on"}),
                         ("ha_light", {"entity_id": "light.invalid", "brightness_pct": 101}),
                         ("ha_media", {"entity_id": "media_player.invalid", "command": "url"}),
                         ("ha_scene", {"entity_id": "../invalid"}),
                         ("camera_snapshot", {"camera": "../invalid"}),
                         ("camera_event_snapshot", {"event_id": "https://invalid"}),
                         ("camera_clip", {"event_id": "../invalid"}))
            for name, args in negatives:
                result = await session.call_tool(name, args)
                assert result.is_error, "Unsafe input did not fail closed"
            report["negative_controls_passed"] = len(negatives)
            if qualify_actions:
                # Explicit opt-in qualification uses a unique disposable native
                # helper, never an existing household actuator or notification.
                helper = ops.module(ops.HA_HELPER, "edsys_live_fixture")
                name = "Codex Qualification " + secrets.token_hex(8)
                created = helper.execute("pve-node3", {"operation": "websocket", "command": {
                    "type": "input_boolean/create", "name": name, "initial": False}, "limit": 1})["result"]
                fixture_id = created["id"]
                entity_id = "input_boolean." + fixture_id
                try:
                    for state in ("on", "off"):
                        changed = await read("ha_set_power", {"entity_id": entity_id, "state": state})
                        assert changed["status"] == "accepted" and changed["after"]["state"] == state
                finally:
                    helper.execute("pve-node3", {"operation": "websocket", "command": {
                        "type": "input_boolean/delete", "input_boolean_id": fixture_id}, "limit": 1})
                    remaining = helper.execute("pve-node3", {"operation": "websocket", "command": {
                        "type": "input_boolean/list"}, "limit": 500})["result"]
                    assert all(row["id"] != fixture_id for row in remaining["items"]), "Fixture cleanup unverified"
                report["action_fixture"] = {"on_readback": True, "off_readback": True, "deleted": True,
                                            "household_actuation": False}
            if qualify_cameras:
                candidates = [row for row in frigate["cameras"] if row.get("camera_fps", 0) > 0]
                assert candidates, "No camera with positive ingest FPS"
                # Prefer an outdoor camera for qualification; don't save pixels.
                selected = next((row for row in candidates if any(word in row["name"].lower()
                                for word in ("front", "yard", "drive", "patio"))), candidates[0])
                samples = []
                for _ in range(2):
                    result = await session.call_tool("camera_snapshot", {"camera": selected["name"]})
                    assert not result.is_error
                    pixels = [item for item in result.content if item.type == "image"]
                    assert len(pixels) == 1
                    metadata = value(result)
                    body = base64.b64decode(pixels[0].data, validate=True)
                    assert hashlib.sha256(body).hexdigest() == metadata["sha256"]
                    samples.append(metadata)
                retained = next((row for row in events["events"] if row.get("has_clip") and row.get("has_snapshot")
                                 and row.get("end_time")), None)
                assert retained, "No completed event with retained media"
                snapshot = await session.call_tool("camera_event_snapshot", {"event_id": retained["id"]})
                assert not snapshot.is_error and any(item.type == "image" for item in snapshot.content)
                clip = await read("camera_clip", {"event_id": retained["id"]})
                path = Path(clip["path"])
                try:
                    assert path.stat().st_mode & 0o777 == 0o600
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == clip["sha256"]
                    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                                            "-of", "json", str(path)], capture_output=True, timeout=20, check=True)
                    streams = json.loads(probe.stdout)["streams"]
                    assert any(row.get("codec_type") == "video" and row.get("width", 0) > 0 for row in streams)
                finally:
                    path.unlink()
                report["camera_acceptance"] = {"samples": len(samples), "changing_bytes": samples[0]["sha256"] != samples[1]["sha256"],
                    "positive_fps": True, "event_image": True, "clip_decodes": True, "test_clip_removed": True,
                    "capture_timestamp_verified": False}
            report["ok"] = True
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--qualify-actions", action="store_true", help="Create/on/off/delete a disposable HA helper; no household action")
    parser.add_argument("--qualify-cameras", action="store_true", help="Read private camera images and a clip; delete the test clip afterward")
    args = parser.parse_args()
    report = asyncio.run(verify(args.qualify_actions, args.qualify_cameras))
    rendered = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered)
    print(rendered)
