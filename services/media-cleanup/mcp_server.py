"""One normal tool call for user-requested media deletion."""
import logging
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import StrictBool

from backend import BackendError, LiveBackend
from engine import CleanupError, Engine

os.umask(0o077)
logging.basicConfig(level=logging.ERROR, handlers=[logging.NullHandler()], force=True)
mcp = MCPServer("EdSys Media Cleanup", instructions=(
    "For an explicit deletion request, call media_delete once with the requested titles. "
    "It resolves clear matches and deletes their library files and owned download folders. "
    "Ask only when it returns needs_clarification; do not request confirmation again. "
    "Use dry_run=true for previews or tests; building or testing never authorizes deleting real media. "
    "Report removed titles and any exceptions briefly. Resume an interrupted operation by operation_id. "
    "Whole TV series only; never turn an episode/season request into whole-series deletion."
), log_level="ERROR")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def call(method, *args):
    try:
        return getattr(Engine(LiveBackend()), method)(*args)
    except (CleanupError, BackendError) as exc:
        raise ToolError(str(exc)) from None
    except Exception:
        raise ToolError("Media cleanup stopped; private service details were suppressed. Check operation status.") from None


@mcp.tool(annotations=DELETE)
def media_delete(titles: list[str] | None = None, kind: str = "movie", include_collections: StrictBool = False,
                 dry_run: StrictBool = False, operation_id: str | None = None) -> dict:
    """Resolve and delete requested titles in one step. Ask only for ambiguous matches. dry_run=true previews without deletion; operation_id resumes an interrupted deletion."""
    if os.environ.get("EDSYS_MEDIA_CLEANUP_READ_ONLY") == "1" and not dry_run:
        raise ToolError("This qualification session permits dry runs only")
    return call("delete", titles, kind, include_collections, dry_run, operation_id)


@mcp.tool(annotations=READ)
def media_status(plan_id: str) -> dict:
    """Read a deletion result without changing media. Accepts operation IDs and legacy plan IDs."""
    return call("status", plan_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
