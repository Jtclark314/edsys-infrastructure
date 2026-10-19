"""Explicit live stdio SDK acceptance, emitting only sanitized result summaries."""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


EXPECTED = {"ha_inventory", "ha_entities", "ha_state", "ha_history", "frigate_health", "frigate_events",
            "plex_libraries", "plex_search", "plex_metadata", "ollama_models", "rag_inventory", "rag_search"}


def value(result):
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    raise AssertionError("Tool returned no structured/text JSON response")


async def verify():
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "transport": "stdio MCP SDK", "calls": []}
    launcher = str(Path(__file__).resolve().with_name("edsys-operations-mcp"))
    async with stdio_client(StdioServerParameters(command=launcher, args=[])) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listing = await session.list_tools()
            names = {item.name for item in listing.tools}
            assert names == EXPECTED, "Unexpected MCP tool surface"
            assert all(item.annotations and item.annotations.read_only_hint is True
                       and item.annotations.destructive_hint is False
                       and item.annotations.open_world_hint is False for item in listing.tools), "Unsafe/missing tool annotations"
            report.update(tool_count=len(names), tools=sorted(names), all_read_only=True)

            async def read(name, arguments=None):
                result = await session.call_tool(name, arguments or {}, read_timeout_seconds=75)
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
            await read("frigate_events", {"limit": 2})
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
            for name, args in (("ha_state", {"entity_id": "../secrets"}), ("rag_search", {"query": "EdSys", "limit": 7})):
                result = await session.call_tool(name, args)
                assert result.is_error, "Unsafe input did not fail closed"
            report["negative_controls_passed"] = 2
            report["ok"] = True
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = asyncio.run(verify())
    rendered = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered)
    print(rendered)
