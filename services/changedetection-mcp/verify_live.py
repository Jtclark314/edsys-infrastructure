"""Explicit disposable-candidate acceptance; real official SDK stdio returned data."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from bridge import private_file


def returned(result):
    if result.is_error:
        raise AssertionError("Sanitized MCP error: " + " ".join(item.text for item in result.content if item.type == "text"))
    if result.structured_content is not None:
        return result.structured_content
    for item in result.content:
        if item.type == "text":
            return json.loads(item.text)
    raise AssertionError("No returned tool data")


def payload(result):
    if "data" in result:
        return result["data"]
    if "text" in result:
        return result["text"]
    artifact = result["artifact"]
    raw = Path(artifact["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == artifact["sha256"]
    return json.loads(raw) if "json" in artifact["content_type"] else raw.decode()


async def exercise_api(call, *, work, fixture_url, fixture_state_file, browser=False, visual=False,
                       report=None, secret_markers=()):
    """Reuse with any async call(tool_name, arguments)->dict adapter that raises on tool errors.

    The caller owns managed-Codex or stdio transport. This exercises only explicitly
    selected synthetic fixtures and cleans up its recorded UUIDs, never all watches.
    work must be a new private evidence directory prepared by the caller.
    """
    os.umask(0o077)
    work = Path(work).resolve()
    assert work.is_dir() and work.stat().st_mode & 0o077 == 0
    assert not (work / "private-update.json").exists(), "Choose a fresh work directory"
    report = report if report is not None else {"calls": []}
    report.setdefault("calls", [])
    report_file = work / "acceptance.json"
    state_file = Path(fixture_state_file).resolve()
    assert state_file.is_file()
    original_state = state_file.read_bytes()
    assert original_state.strip() in {b"ALPHA", b"BETA"}
    name = "mcp-acceptance-" + uuid.uuid4().hex[:12]
    watch_ids, tag_ids = [], []
    fixture_secret = "synthetic-private-header-" + uuid.uuid4().hex
    raw_call = call

    async def call(tool, arguments):
        value = await raw_call(tool, arguments)
        markers = (fixture_secret, *secret_markers)
        raw = json.dumps(value)
        assert all(marker not in raw for marker in markers), "Credential leaked into tool result"
        artifact = value.get("artifact")
        if artifact:
            data = Path(artifact["path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
            assert all(marker.encode() not in data for marker in markers)
            assert Path(artifact["path"]).stat().st_mode & 0o077 == 0
        report["calls"].append({"tool": tool, "operation": arguments.get("operation_id", arguments.get("action")), "ok": True})
        report_file.write_text(json.dumps(report, indent=2) + "\n")
        return value

    async def read(operation, identity=None, **extra):
        return await call("changedetection_read", {"operation_id": operation,
            **({"path_parameters": {"uuid": identity}} if identity else {}), **extra})

    async def mutate(operation, identity=None, **extra):
        return await call("changedetection_mutate", {"operation_id": operation,
            **({"path_parameters": {"uuid": identity}} if identity else {}), **extra})

    async def recheck(identity, actions=False):
        return await call("changedetection_control", {"target_type": "watch", "uuid": identity,
            "action": "recheck", "allow_network_activity": True, "allow_target_actions": actions})

    async def history(identity, minimum):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            data = payload(await read("getWatchHistory", identity))
            if len(data) >= minimum:
                return data
            await asyncio.sleep(1)
        raise AssertionError("Synthetic watch history did not reach the expected count")

    try:
        system = payload(await read("getSystemInfo"))
        assert system["version"] == "0.60.10"
        report["backend_version"] = system["version"]
        schema = await call("changedetection_operation_schema", {"operation_id": "createWatch"})
        if "schema_artifact" in schema:
            schema = json.loads(Path(schema["schema_artifact"]["path"]).read_text())
        assert schema["method"] == "POST"
        tag = payload(await mutate("createTag", parameters={"title": name}))
        tag_id = tag["uuid"]
        tag_ids.append(tag_id)
        assert payload(await read("getTag", tag_id))["title"] == name
        created = await mutate("createWatch", parameters={"url": fixture_url, "title": name, "tag": tag_id,
                                                            "fetch_backend": "html_requests"})
        watch_id = payload(created)["uuid"]
        watch_ids.append(watch_id)
        assert created["applied_safety_defaults"] == {"paused": True, "notification_muted": True}
        detail = payload(await read("getWatch", watch_id))
        assert detail["paused"] and detail["notification_muted"]
        assert detail["llm_backend_profile"] is False, "New watches must not silently opt into AI"
        private_body = work / "private-update.json"
        private_body.write_text(json.dumps({"headers": {"X-MCP-Synthetic": fixture_secret}, "include_filters": ["h1"]}))
        private_body.chmod(0o600)
        await mutate("updateWatch", watch_id, body_file=str(private_body))
        detail = payload(await read("getWatch", watch_id))
        assert detail["headers"]["redacted"] and detail["headers"]["present"]
        await mutate("updateTag", tag_id, parameters={"title": name + "-updated"})
        await read("listTags")
        await read("listWatches", query_parameters={"tag": name + "-updated"})
        await read("getNotifications")
        # This GET flag must be rejected before any backend request.
        rejected = False
        try:
            await call("changedetection_read", {"operation_id": "getWatch", "path_parameters": {"uuid": watch_id},
                                               "query_parameters": {"recheck": "false"}})
        except Exception:
            rejected = True
        assert rejected, "Read tool accepted a mutating GET flag"
        state_file.write_text("ALPHA\n")
        await recheck(watch_id)
        first = await history(watch_id, 1)
        first_time = min(int(value) for value in first)
        first_snapshot = await call("changedetection_read", {"operation_id": "getWatchSnapshot",
            "path_parameters": {"uuid": watch_id, "timestamp": first_time}})
        assert "ALPHA" in payload(first_snapshot)
        await asyncio.sleep(1.1)
        state_file.write_text("BETA\n")
        await recheck(watch_id)
        await history(watch_id, 2)
        diff = await call("changedetection_read", {"operation_id": "getWatchHistoryDiff",
            "path_parameters": {"uuid": watch_id, "from_timestamp": "previous", "to_timestamp": "latest"},
            "query_parameters": {"format": "text"}})
        assert "ALPHA" in payload(diff) and "BETA" in payload(diff)
        report["text_history_diff"] = {"old_label": "ALPHA", "new_label": "BETA", "artifact": diff["artifact"]}
        imported = payload(await mutate("importWatches", urls=[fixture_url + "?mcp=" + name + suffix for suffix in ("-one", "-two")]))
        assert isinstance(imported, list) and len(imported) == 2
        watch_ids.extend(imported)
        for identity in imported:
            item = payload(await read("getWatch", identity))
            assert item["paused"] and item["notification_muted"]
        if browser:
            browser_input = work / "private-browser.json"
            browser_input.write_text(json.dumps({"url": fixture_url + "?browser=" + name, "title": name + "-browser",
                "fetch_backend": "html_webdriver", "include_filters": ["h1"],
                "browser_steps": [{"operation": "Wait for seconds", "selector": "", "optional_value": "1"}]}))
            browser_input.chmod(0o600)
            browser_id = payload(await mutate("createWatch", body_file=str(browser_input)))["uuid"]
            watch_ids.append(browser_id)
            await recheck(browser_id, actions=True)
            await history(browser_id, 1)
            screenshot = await call("changedetection_screenshot", {"uuid": browser_id})
            image_bytes = Path(screenshot["artifact"]["path"]).read_bytes()
            assert image_bytes.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
            report["browser_steps_screenshot"] = screenshot["artifact"]
        if visual:
            visual_id = payload(await mutate("createWatch", parameters={
                "url": fixture_url + "?visual=" + name, "title": name + "-visual", "fetch_backend": "html_webdriver",
                "processor": "image_ssim_diff", "processor_config_min_change_percentage": 1,
                "processor_config_pixel_difference_threshold_sensitivity": "20",
                "processor_config_bounding_box": "200,0,200,64", "processor_config_selection_mode": "draw"}))["uuid"]
            watch_ids.append(visual_id)
            settings = payload(await read("getWatch", visual_id))
            assert settings["processor_config_bounding_box"] == "200,0,200,64"
            assert settings["processor_config_min_change_percentage"] == 1
            assert settings["llm_backend_profile"] is False
            state_file.write_text("ALPHA\n")
            await recheck(visual_id)
            await history(visual_id, 1)
            first_image = await call("changedetection_read", {"operation_id": "getWatchSnapshot",
                "path_parameters": {"uuid": visual_id, "timestamp": "latest"}})
            assert "text" not in first_image and "data" not in first_image
            await asyncio.sleep(1.1)
            state_file.write_text("BETA\n")
            await recheck(visual_id)
            await history(visual_id, 2)
            second_image = await call("changedetection_read", {"operation_id": "getWatchSnapshot",
                "path_parameters": {"uuid": visual_id, "timestamp": "latest"}})
            assert "text" not in second_image and "data" not in second_image
            assert first_image["artifact"]["sha256"] != second_image["artifact"]["sha256"]
            report["visual_change"] = {"history_count": 2, "before": first_image["artifact"], "after": second_image["artifact"]}
        await mutate("deleteWatchHistory", watch_id)
        assert payload(await read("getWatchHistory", watch_id)) == {}
        report["native_key_absent_from_results"] = bool(secret_markers)
        report["private_header_redacted"] = True
        report["passed"] = True
    finally:
        failures = []
        for identity in reversed(watch_ids):
            try:
                await mutate("deleteWatch", identity)
            except Exception:
                failures.append({"kind": "watch", "uuid": identity})
        for identity in reversed(tag_ids):
            try:
                await mutate("deleteTag", identity)
            except Exception:
                failures.append({"kind": "tag", "uuid": identity})
        state_file.write_bytes(original_state)
        report["cleanup"] = {"owned_watches": len(watch_ids), "owned_tags": len(tag_ids), "failed": failures}
        report_file.write_text(json.dumps(report, indent=2) + "\n")
        assert not failures, "Some explicitly recorded synthetic fixtures still require cleanup"
    return report


async def verify(args):
    os.umask(0o077)
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "transport": "official SDK stdio", "calls": []}
    env = {"CHANGEDETECTION_ARTIFACT_DIR": str(work / "artifacts"), "PYTHONDONTWRITEBYTECODE": "1"}

    def process(backend, key):
        return StdioServerParameters(command=args.python, args=[str(Path(args.server).resolve())],
            env={**env, "CHANGEDETECTION_BASE_URL": backend, "CHANGEDETECTION_API_KEY_FILE": key})

    async with stdio_client(process("http://127.0.0.1:1", "/nonexistent/changedetection-key")) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert len(listing.tools) == 6
            for tool in listing.tools:
                assert tool.annotations.read_only_hint is (tool.name not in {"changedetection_mutate", "changedetection_control"})
            catalog = returned(await session.call_tool("changedetection_operations", {}))
            assert catalog["native_operations"] == 23
            report["offline_initialization"] = {"backend_down": True, "key_missing": True, "tools": 6, "operations": 23}
    if args.offline_only:
        (work / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"ok": True, "offline_only": True, "report": str(work / "acceptance.json")}))
        return
    assert args.backend and args.key_file and args.fixture_url and args.fixture_state_file
    if urlsplit(args.backend).hostname == "10.254.230.18":
        assert args.allow_production_fixtures, "Production fixtures require the explicit commissioning switch"
    key = private_file(args.key_file, 1024).decode().strip()
    async with stdio_client(process(args.backend, args.key_file)) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()

            async def call(tool, arguments):
                return returned(await session.call_tool(tool, arguments, read_timeout_seconds=150))

            await exercise_api(call, work=work, fixture_url=args.fixture_url, fixture_state_file=args.fixture_state_file,
                               browser=args.browser, visual=args.visual, report=report, secret_markers=(key,))
    print(json.dumps({"ok": True, "report": str(work / "acceptance.json"), "calls": len(report["calls"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--server", default=str(Path(__file__).with_name("server.py")))
    parser.add_argument("--work-dir", required=True, help="New private evidence directory; must not exist")
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--backend", help="Qualified candidate/production origin")
    parser.add_argument("--key-file", help="Private native API key path, never its value")
    parser.add_argument("--fixture-url", help="Selected synthetic HTTP(S) fixture URL")
    parser.add_argument("--fixture-state-file", help="Explicitly authorized existing ALPHA/BETA state file")
    parser.add_argument("--browser", action="store_true", help="Also qualify saved browser steps and a returned PNG/JPEG")
    parser.add_argument("--visual", action="store_true", help="Qualify the local native visual-schema extension and binary image history")
    parser.add_argument("--allow-production-fixtures", action="store_true", help="Explicitly authorized synthetic production commissioning only")
    asyncio.run(verify(parser.parse_args()))
