"""Local stdio adapter for the existing EdSys index and private project notes."""
import logging
import os
import sqlite3
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from knowledge import Records, ProjectMemory

os.umask(0o077)
logging.basicConfig(level=logging.ERROR, handlers=[logging.NullHandler()], force=True)
mcp = MCPServer('EdSys records and project continuity', log_level='ERROR')
records = Records()
memory = ProjectMemory(Path.cwd())
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def call(function, *args):
    try:
        return function(*args)
    except (ValueError, OSError) as exc:
        raise ToolError(str(exc)) from None
    except sqlite3.Error:
        raise ToolError('Grounding index unreadable or unsupported; restore the current index before relying on EdSys facts') from None


@mcp.tool(annotations=READ)
def session_context() -> dict:
    """Refresh the automatically supplied root AGENTS guidance, private project checkpoint and index freshness. Use when context needs rechecking or the automatic payload is absent. Notes are advisory; recheck changed evidence."""
    return {'project_memory': call(memory.read), 'project_instructions': call(memory.guidance),
            'edsys_records': records.status()}


@mcp.tool(annotations=READ)
def search_records(query: str, limit: int = 4, include_history: bool = False) -> dict:
    """Search reviewed EdSys records for device aliases, systems, source repos and procedures. Returns cited excerpts and dates, not live health. Historical records excluded unless requested. No cloud calls."""
    return call(records.search, query, limit, include_history)


@mcp.tool(annotations=READ)
def read_record(record_id: str, start_chunk: int = 0, count: int = 3) -> dict:
    """Read more of a returned record by stable ID. Bounded pages only, no arbitrary file paths. Source changes or stale index fail closed."""
    return call(records.read, record_id, start_chunk, count)


@mcp.tool(annotations=WRITE)
def save_checkpoint(expected_revision: int, outcome: str, completed: list[str], decisions: list[str],
                    next_steps: list[str], blockers: list[str], evidence_paths: list[str]) -> dict:
    """Primary agent only: save a compact private checkpoint before ending meaningful project work. Existing relative evidence files required for completed claims. No credentials, raw transcripts, or global EdSys updates. Read session_context for revision first; merge on conflict."""
    return call(memory.save, expected_revision, outcome, completed, decisions, next_steps, blockers, evidence_paths)


if __name__ == '__main__':
    mcp.run(transport='stdio')
