"""Native SDK discovery, live read/preview, and no-write negative controls."""
import asyncio
import argparse
import json
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def value(result):
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    raise AssertionError("No structured result")


async def main(movie_query: str, show_query: str):
    launcher = str(Path(__file__).resolve().with_name("edsys-media-cleanup-mcp"))
    report = {"transport": "native MCP SDK stdio", "live_media_writes": False}
    async with stdio_client(StdioServerParameters(command=launcher, args=[])) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listing = await session.list_tools()
            expected = {"media_find", "media_plan", "media_execute", "media_status"}
            assert {x.name for x in listing.tools} == expected
            for tool in listing.tools:
                assert tool.annotations.destructive_hint is (tool.name == "media_execute")
                assert tool.annotations.read_only_hint is (tool.name in {"media_find", "media_status"})
                assert tool.annotations.open_world_hint is False
            report["tools"] = sorted(expected)

            async def read(name, args):
                result = await session.call_tool(name, args, read_timeout_seconds=900)
                assert not result.is_error, name + " failed"
                return value(result)

            found = await read("media_find", {"queries": [movie_query, show_query], "kind": "any"})
            selected = []
            for row in found["results"]:
                matches = [x for x in row["matches"] if x["exact_title"]]
                assert len(matches) == 1
                selected.append(matches[0]["ref"])
            # This is a read-only acceptance preview, not a deletion request.
            preview = await read("media_plan", {"target_refs": selected, "include_download_copies": False})
            assert preview["title_count"] == 2 and preview["whole_series"] is True
            state = await read("media_status", {"plan_id": preview["plan_id"]})
            assert state["state"] == "planned"
            report.update(preview_titles=preview["title_count"], preview_library_files=preview["library_file_count"],
                          preview_ready=preview["ready"], deletion_performed=False)
            controls = [
                ("media_plan", {"target_refs": ["../../movies"]}),
                ("media_find", {"queries": [""]}),
                ("media_execute", {"plan_id": "0" * 32, "selection_hash": "0" * 64, "user_requested_delete": False}),
                ("media_execute", {"plan_id": preview["plan_id"], "selection_hash": "0" * 64, "user_requested_delete": True}),
                ("media_status", {"plan_id": "../config"}),
                ("media_plan", {"recovery_plan_id": "../config"}),
                ("media_plan", {"target_refs": selected, "recovery_plan_id": preview["plan_id"]}),
                ("media_plan", {"recovery_plan_id": preview["plan_id"]}),
            ]
            for name, args in controls:
                result = await session.call_tool(name, args, read_timeout_seconds=30)
                assert result.is_error, name + " accepted an unsafe input"
            report["negative_controls"] = len(controls)
            assert (await read("media_status", {"plan_id": preview["plan_id"]}))["state"] == "planned"
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--movie-query", required=True, help="One exact currently stored movie title/year; kept out of source")
    parser.add_argument("--show-query", required=True, help="One exact currently stored show title; kept out of source")
    args = parser.parse_args()
    asyncio.run(main(args.movie_query, args.show_query))
