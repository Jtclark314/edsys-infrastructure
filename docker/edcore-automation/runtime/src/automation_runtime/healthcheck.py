"""Container health check for a fresh MQTT-connected heartbeat."""

from __future__ import annotations

import argparse
from pathlib import Path
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="/tmp/automation-runtime.healthy")
    parser.add_argument("--max-age", type=float, default=30.0)
    args = parser.parse_args()
    path = Path(args.path)
    if not path.is_file():
        raise SystemExit(1)
    age = time.time() - path.stat().st_mtime
    raise SystemExit(0 if 0 <= age <= args.max_age else 1)


if __name__ == "__main__":
    main()
