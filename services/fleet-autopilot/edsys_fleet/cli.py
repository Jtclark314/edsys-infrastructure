from __future__ import annotations

import argparse
import json

from .collector import FleetCollector
from .config import load_config
from .io import read_json
from .jobs import FleetJobRunner, list_jobs, queue_job


def main() -> None:
    parser = argparse.ArgumentParser(description="EdSys Fleet Autopilot")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect")
    sub.add_parser("show")
    sub.add_parser("jobs")
    queue = sub.add_parser("queue")
    queue.add_argument("action", choices=["inspect", "verify", "upgrade", "rollback", "proxmox"])
    queue.add_argument("--target", default="all")
    queue.add_argument("--component", default="")
    queue.add_argument("--parameters", default="{}")
    sub.add_parser("worker-once")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "collect":
        value = FleetCollector(config).collect()
    elif args.command == "show":
        value = read_json(config.state_root / "snapshot.json", {})
    elif args.command == "jobs":
        value = list_jobs(config)
    elif args.command == "queue":
        value = queue_job(config, args.action, args.target, args.component, json.loads(args.parameters), "cli")
    else:
        value = FleetJobRunner(config).process_one()
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
