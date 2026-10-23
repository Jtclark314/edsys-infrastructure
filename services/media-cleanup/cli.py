#!/usr/bin/env python3
"""CLI counterpart to the four fixed media-cleanup tools."""
import argparse
import json

from backend import BackendError, LiveBackend
from engine import CleanupError, Engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    find = commands.add_parser("find", help="Resolve titles, read-only")
    find.add_argument("titles", nargs="+")
    find.add_argument("--kind", choices=["movie", "show", "any"], default="any")
    find.add_argument("--offset", type=int, default=0)
    plan = commands.add_parser("plan", help="Create a private preview; no media changes")
    plan.add_argument("refs", nargs="+")
    plan.add_argument("--include-collections", action="store_true")
    plan.add_argument("--omit-download-copies", action="store_true")
    execute = commands.add_parser("execute", help="Execute an exact user-authorized preview")
    execute.add_argument("plan_id")
    execute.add_argument("selection_hash")
    execute.add_argument("--user-requested-delete", action="store_true", required=True)
    status = commands.add_parser("status", help="Read plan status without writes")
    status.add_argument("plan_id")
    args = parser.parse_args()
    try:
        engine = Engine(LiveBackend())
        if args.command == "find":
            result = engine.find(args.titles, args.kind, args.offset)
        elif args.command == "plan":
            result = engine.plan(args.refs, args.include_collections, not args.omit_download_copies)
        elif args.command == "execute":
            result = engine.execute(args.plan_id, args.selection_hash, args.user_requested_delete)
        else:
            result = engine.status(args.plan_id)
        print(json.dumps(result, indent=2))
        return 0 if result.get("state") not in {"partial", "uncertain"} else 2
    except (CleanupError, BackendError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"error": "Media cleanup failed; upstream details suppressed. Inspect status before retrying a deletion."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
