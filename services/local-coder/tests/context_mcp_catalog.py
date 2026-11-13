"""Read the real local MCP catalog, independently of synthetic model fixtures."""
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    source = Path(__file__).resolve().parents[1]
    server = StdioServerParameters(command=sys.executable, args=[str(source / 'knowledge_mcp.py')])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            assert result.next_cursor is None
            print(json.dumps(sorted(tool.name for tool in result.tools)))


if __name__ == '__main__':
    asyncio.run(main())
