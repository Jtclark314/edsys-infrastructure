#!/usr/bin/env python3
"""Apply custom browser icons, then optionally start Frigate's normal init."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from html.parser import HTMLParser
from urllib.parse import urlsplit


class LinkAttributes(HTMLParser):
    def __init__(self, tag):
        super().__init__()
        self.attributes = {}
        self.feed(tag)

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            self.attributes = dict(attrs)


ICON_RELATIONS = {"icon", "shortcut icon", "apple-touch-icon", "mask-icon", "manifest"}


def atomic_write(path, text):
    temporary = path.with_name(path.name + ".edsys-new")
    temporary.write_text(text)
    temporary.chmod(0o644)
    temporary.replace(path)


def apply_icons(web_root, asset_root):
    index_path = web_root / "index.html"
    original = index_path.read_text()
    tags = re.findall(r"<link\b[^>]*>", original, flags=re.I)
    manifest_links = [LinkAttributes(tag).attributes for tag in tags
                      if LinkAttributes(tag).attributes.get("rel", "").lower() == "manifest"]
    if len(manifest_links) != 1 or "</head>" not in original:
        raise ValueError("Unexpected frontend layout; keeping upstream icons")
    manifest_url = urlsplit(manifest_links[0]["href"]).path
    manifest_relative = manifest_url.removeprefix("/BASE_PATH/").lstrip("/")
    manifest_path = (web_root / manifest_relative).resolve()
    if not manifest_path.is_relative_to(web_root.resolve()):
        raise ValueError("Manifest path is outside the frontend")
    manifest = json.loads(manifest_path.read_text())
    needed = ["favicon.ico", "icon-16.png", "icon-32.png", "icon-96.png",
              "icon-180.png", "icon-192.png", "icon-512.png"]
    for name in needed:
        if not (asset_root / name).is_file():
            raise ValueError("Missing icon asset: " + name)
    version = hashlib.sha256((asset_root / "favicon.ico").read_bytes()).hexdigest()[:12]
    destination = web_root / "edsys-icons" / version
    destination.mkdir(parents=True, exist_ok=True)
    for name in needed:
        shutil.copyfile(asset_root / name, destination / name)
        (destination / name).chmod(0o644)
    prefix = "/BASE_PATH/edsys-icons/" + version + "/"
    manifest["icons"] = [
        {"src": prefix + "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": prefix + "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": prefix + "icon-180.png", "sizes": "180x180", "type": "image/png", "purpose": "maskable"},
        {"src": prefix + "icon-96.png", "sizes": "96x96", "type": "image/png", "purpose": "maskable"},
    ]
    manifest_name = "site-" + version + ".webmanifest"
    atomic_write(destination / manifest_name, json.dumps(manifest, indent=2) + "\n")

    def remove_icon_link(match):
        attrs = LinkAttributes(match.group()).attributes
        return "" if attrs.get("rel", "").lower() in ICON_RELATIONS else match.group()

    updated = re.sub(r"<link\b[^>]*>", remove_icon_link, original, flags=re.I)
    links = (
        f'<link rel="icon" href="{prefix}favicon.ico" />\n'
        f'<link rel="icon" type="image/png" sizes="16x16" href="{prefix}icon-16.png" />\n'
        f'<link rel="icon" type="image/png" sizes="32x32" href="{prefix}icon-32.png" />\n'
        f'<link rel="apple-touch-icon" sizes="180x180" href="{prefix}icon-180.png" />\n'
        f'<link rel="manifest" href="{prefix}{manifest_name}" crossorigin="use-credentials" />\n'
    )
    updated = updated.replace("</head>", links + "</head>", 1)
    shutil.copyfile(asset_root / "favicon.ico", web_root / "favicon.ico")
    (web_root / "favicon.ico").chmod(0o644)
    atomic_write(index_path, updated)
    return version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-root", type=Path, default=Path("/opt/frigate/web"))
    parser.add_argument("--asset-root", type=Path, default=Path(__file__).resolve().parent / "assets")
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    try:
        version = apply_icons(args.web_root, args.asset_root)
        print("[EdSys icons] Applied plasma badge " + version, flush=True)
    except Exception as error:
        print("[EdSys icons] Keeping upstream frontend after icon error: " + str(error), file=sys.stderr, flush=True)
        if not args.start:
            return 1
    if args.start:
        os.execv("/init", ["/init"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
