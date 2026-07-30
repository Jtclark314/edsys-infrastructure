#!/usr/bin/env python3
"""Verify the exact read-only MCP surface and call the status tool."""

from __future__ import annotations

import argparse
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED = [
    "search_code",
    "search_symbol",
    "code_index_status",
    "review_diff_local",
    "triage_test_failure_local",
]


async def verify(url: str) -> None:
    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            if names != EXPECTED:
                raise RuntimeError(f"Unexpected MCP tool surface: {names}")
            for tool in tools.tools:
                annotations = tool.annotations
                if (
                    annotations is None
                    or annotations.readOnlyHint is not True
                    or annotations.destructiveHint is not False
                    or annotations.idempotentHint is not True
                    or annotations.openWorldHint is not False
                ):
                    raise RuntimeError(f"Unsafe annotations on tool: {tool.name}")
            result = await session.call_tool("code_index_status", arguments={})
            if result.isError:
                raise RuntimeError("code_index_status returned an MCP error")
            print(json.dumps({"tools": names, "status_call": "ok"}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:6071/mcp")
    args = parser.parse_args()
    asyncio.run(verify(args.url))


if __name__ == "__main__":
    main()
