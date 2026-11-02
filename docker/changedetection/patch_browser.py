#!/usr/bin/env python3
"""Narrow patches to source-pinned Sockpuppet; retain Apache upstream license."""
from pathlib import Path


def replace(path, old, new):
    value = path.read_text()
    if value.count(old) != 1:
        raise RuntimeError(f"Upstream source changed: {path.name}")
    path.write_text(value.replace(old, new))


def main():
    root = Path("/usr/src/app")
    chrome = root / "chrome.py"
    for flag in ("--disable-web-security=true", "--disable-ipc-flooding-protection", "--disable-hang-monitor", "--disable-remote-fonts"):
        replace(chrome, f'        "{flag}",\n', "")
    replace(chrome, '        "--no-sandbox",', '        "--no-sandbox",\n        "--disable-quic",\n        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",\n        "--proxy-bypass-list=<-loopback>",')
    server = root / "server.py"
    replace(server,
            "                await relay(client_ws=websocket, chrome_ws=chrome_ws, tracer=tracer, debug_log=debug_log)",
            "                await asyncio.wait_for(relay(client_ws=websocket, chrome_ws=chrome_ws, tracer=tracer, debug_log=debug_log), timeout=int(os.getenv('MAX_BROWSER_SECONDS', '600')))")
    print("Qualified Sockpuppet origin/egress/session-timeout patches applied")


if __name__ == "__main__":
    main()
