from __future__ import annotations

import argparse
import signal
import time

from .config import load_config
from .jobs import FleetJobRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="EdSys Fleet Autopilot job worker")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--collect-on-start", action="store_true")
    args = parser.parse_args()
    runner = FleetJobRunner(load_config())
    if args.collect_on_start:
        runner.collector.collect()
    if not args.watch:
        runner.process_one()
        return
    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        processed = runner.process_one()
        if processed is None:
            time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    main()
