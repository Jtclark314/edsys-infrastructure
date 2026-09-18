"""Desktop tools for an xvfb-run-owned session, never the operator's display."""
from __future__ import annotations

import atexit
import io
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Literal

from mcp.server.mcpserver import MCPServer, Image
from mcp.server.mcpserver.exceptions import ToolError
from PIL import ImageGrab

WIDTH, HEIGHT = 1280, 800
logging.basicConfig(level=logging.ERROR, handlers=[logging.NullHandler()], force=True)
mcp = MCPServer("EdSys local desktop", log_level="ERROR")
children: list[subprocess.Popen] = []
profile = tempfile.TemporaryDirectory(prefix="edsys-coder-desktop-")


def guard() -> None:
    display = os.environ.get("DISPLAY", "")
    if (os.environ.get("EDSYS_LOCAL_DESKTOP") != "1"
            or not re.fullmatch(r":(?:9\d|[1-9]\d{2,})(?:\.0)?", display)
            or not os.environ.get("XAUTHORITY")):
        raise RuntimeError("Launch through desktop-mcp.sh; a private Xvfb display is required")


def command(*args: str) -> None:
    guard()
    subprocess.run(["/usr/bin/xdotool", *args], check=True, timeout=10,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def point(x: int, y: int) -> tuple[int, int]:
    if not 0 <= x <= 1000 or not 0 <= y <= 1000:
        raise ToolError("Use normalized coordinates from 0 to 1000 on each axis")
    return min(WIDTH - 1, round(x * WIDTH / 1000)), min(HEIGHT - 1, round(y * HEIGHT / 1000))


@mcp.tool()
def desktop_screenshot() -> Image:
    """Return the desktop image. Use normalized 0-1000 x/y coordinates for click and drag, not pixels."""
    guard()
    shot = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
    output = io.BytesIO()
    shot.save(output, format="PNG")
    return Image(data=output.getvalue(), format="png")


@mcp.tool()
def desktop_open(application: Literal["scratchpad", "browser", "text_editor", "calculator"]) -> str:
    """Open a scratchpad, fresh Chrome, Mousepad editor, or calculator on this private desktop."""
    guard()
    if application == "scratchpad":
        argv = ["/usr/bin/python3", str(Path(__file__).with_name("scratchpad.py"))]
    elif application == "text_editor":
        argv = ["/usr/bin/mousepad", "--disable-server"]
    elif application == "calculator":
        argv = ["/usr/bin/xcalc"]
    else:
        argv = ["/usr/bin/google-chrome", f"--user-data-dir={profile.name}",
                "--no-first-run", "--no-default-browser-check", "--disable-sync",
                "--disable-background-networking", "--window-size=1280,800",
                "--window-position=0,0", "about:blank"]
    children.append(subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True))
    return f"Opened {application}; take a screenshot to inspect its controls."


@mcp.tool()
def desktop_click(x: int, y: int, button: Literal["left", "middle", "right"] = "left",
                  double: bool = False) -> str:
    """Click using normalized 0-1000 coordinates on each axis. (500,500) is screen center. Not pixels."""
    x, y = point(x, y)
    command("mousemove", "--sync", str(x), str(y), "click", "--repeat",
            "2" if double else "1", "--delay", "120",
            {"left": "1", "middle": "2", "right": "3"}[button])
    return "Clicked; take another screenshot to verify the result."


@mcp.tool()
def desktop_type(text: str) -> str:
    """Type literal text into the focused control; does not interpret shell syntax."""
    if len(text) > 10000 or "\x00" in text:
        raise ToolError("Text must be at most 10000 characters without NUL")
    command("type", "--clearmodifiers", "--delay", "5", "--", text)
    return "Typed text."


@mcp.tool()
def desktop_key(key: str) -> str:
    """Press a key/chord such as Return, Tab, Escape, ctrl+a, or ctrl+l."""
    if len(key) > 80 or not re.fullmatch(r"[A-Za-z0-9_+]+", key):
        raise ToolError("Use one X11 key name or a modifier+key chord")
    command("key", "--clearmodifiers", "--", key)
    return "Pressed key."


@mcp.tool()
def desktop_scroll(direction: Literal["up", "down"], amount: int = 3) -> str:
    """Scroll the focused view by 1 to 20 wheel steps."""
    if not 1 <= amount <= 20:
        raise ToolError("Scroll amount must be between 1 and 20")
    command("click", "--repeat", str(amount), "--delay", "80",
            "4" if direction == "up" else "5")
    return "Scrolled."


@mcp.tool()
def desktop_drag(x1: int, y1: int, x2: int, y2: int) -> str:
    """Drag using normalized 0-1000 coordinates on each axis, not pixels."""
    x1, y1 = point(x1, y1)
    x2, y2 = point(x2, y2)
    command("mousemove", "--sync", str(x1), str(y1), "mousedown", "1",
            "mousemove", "--sync", str(x2), str(y2), "mouseup", "1")
    return "Dragged."


@atexit.register
def cleanup() -> None:
    import signal
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    profile.cleanup()


if __name__ == "__main__":
    guard()
    children.append(subprocess.Popen(["/usr/bin/xfwm4", "--compositor=off"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True))
    mcp.run(transport="stdio")
