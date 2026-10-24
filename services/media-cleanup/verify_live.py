"""Native MCP qualification; a server-side guard forbids live deletion."""
import asyncio
import argparse
import json
import os
from pathlib import Path
import time

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from backend import LiveBackend
from workflow import label


def value(result):
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    raise AssertionError("No structured result")


async def main(movie_query=None):
    if movie_query is None:
        catalog = LiveBackend().catalog("movie")
        candidates = [x for x in catalog if x.get("has_files") and not x.get("mapping_blocker")]
        movie_query = next(label(x) for x in candidates if sum(label(y) == label(x) for y in candidates) == 1)
    launcher = str(Path(__file__).resolve().with_name("edsys-media-cleanup-mcp"))
    env = {**os.environ, "EDSYS_MEDIA_CLEANUP_READ_ONLY": "1"}
    report = {"transport": "native MCP SDK stdio", "live_media_writes": False, "deletion_guard": True}
    async with stdio_client(StdioServerParameters(command=launcher, args=[], env=env)) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert {x.name for x in listing.tools} == {"media_delete", "media_status"}
            for tool in listing.tools:
                assert tool.annotations.destructive_hint is (tool.name == "media_delete")
                assert tool.annotations.read_only_hint is (tool.name == "media_status")
                assert tool.annotations.open_world_hint is False
            started = time.monotonic()
            preview = await session.call_tool("media_delete", {"titles": [movie_query], "dry_run": True}, read_timeout_seconds=900)
            assert not preview.is_error, "Dry-run lookup failed"
            data = value(preview)
            assert data["state"] == "dry_run" and data["deletion_performed"] is False
            assert len(data["titles"]) == 1 and data["library_files"] > 0
            report.update(preview_titles=len(data["titles"]), library_files=data["library_files"],
                          download_files=data["download_files"], skipped_titles=len(data["skipped"]),
                          roundtrip_seconds=round(time.monotonic() - started, 2))
            controls = [
                ("media_delete", {"titles": [movie_query]}),
                ("media_delete", {"titles": [], "dry_run": True}),
                ("media_delete", {"titles": [""], "dry_run": True}),
                ("media_delete", {"titles": [movie_query], "kind": "episode", "dry_run": True}),
                ("media_delete", {"operation_id": "../config", "dry_run": True}),
                ("media_status", {"plan_id": "../config"}),
            ]
            for name, args in controls:
                result = await session.call_tool(name, args, read_timeout_seconds=30)
                assert result.is_error, name + " accepted an invalid input or a guarded live write"
            report["negative_controls"] = len(controls)
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--movie-query", help="Optional exact stored movie title/year; otherwise select a unique stored title read-only")
    args = parser.parse_args()
    asyncio.run(main(args.movie_query))
