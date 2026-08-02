#!/usr/bin/env python3
"""Strict acceptance checks for the edcore-sdr guest."""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import urllib.error
import urllib.request


EXPECTED_HOST = "edcore-sdr"
EXPECTED_USB = "0bda:2838"
SETTINGS = pathlib.Path("/var/lib/openwebrx/settings.json")
ARCHIVE = pathlib.Path("/srv/edsys-sdr-data")
EXPECTED_FEATURES = {
    "core",
    "rtl_sdr",
    "rtl_tcp",
    "digital_voice_freedv",
    "digital_voice_m17",
    "wsjt-x",
    "packet",
    "pocsag",
    "js8call",
    "drm",
    "adsb",
    "uat",
    "ism",
    "hfdl",
    "vdl2",
    "acars",
    "page",
    "selcall",
    "eas",
    "wxsat",
    "rds",
    "dab",
    "hdradio",
    "skimmer",
    "sonde",
    "lora",
    "meshtastic",
}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def main() -> int:
    errors: list[str] = []
    if socket.gethostname() != EXPECTED_HOST:
        errors.append(f"hostname={socket.gethostname()!r}, expected {EXPECTED_HOST!r}")

    for unit in ("openwebrx.service", "cockpit.socket", "qemu-guest-agent.service"):
        result = run("systemctl", "is-active", unit)
        if result.returncode != 0:
            errors.append(f"{unit} is not active: {result.stdout.strip()}")

    usb = run("lsusb", "-d", EXPECTED_USB)
    if usb.returncode != 0 or "NESDR" not in usb.stdout and "RTL2838" not in usb.stdout:
        errors.append(f"NESDR {EXPECTED_USB} not detected: {usb.stdout.strip()}")

    try:
        settings = json.loads(SETTINGS.read_text())
        device = settings["sdrs"]["nesdr-smart-v5"]
        profiles = device["profiles"]
        if len(profiles) < 50:
            errors.append(f"only {len(profiles)} profiles are installed")
        if next(iter(profiles)) != "pass-aprs-2m":
            errors.append("idle/default profile is not pass-aprs-2m")
        for profile_id, profile in profiles.items():
            half = profile["samp_rate"] / 2
            if not profile["center_freq"] - half <= profile["start_freq"] <= profile["center_freq"] + half:
                errors.append(f"{profile_id}: start frequency outside profile passband")
            if profile_id.startswith("enable-") and profile.get("lfo_offset") != 125000000:
                errors.append(f"{profile_id}: missing 125 MHz Ham It Up offset")
            if profile_id.startswith("pass-") and profile.get("lfo_offset", 0) != 0:
                errors.append(f"{profile_id}: pass-through profile has an oscillator offset")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot validate {SETTINGS}: {exc}")

    features: dict[str, object] = {}
    try:
        with urllib.request.urlopen("http://127.0.0.1:8073/", timeout=10) as response:
            if response.status != 200:
                errors.append(f"OpenWebRX HTTP status={response.status}")
    except (OSError, urllib.error.URLError) as exc:
        errors.append(f"OpenWebRX HTTP check failed: {exc}")

    try:
        with urllib.request.urlopen("http://127.0.0.1:8073/api/features", timeout=10) as response:
            features = json.load(response)
        unavailable = sorted(
            name
            for name in EXPECTED_FEATURES
            if not isinstance(features.get(name), dict)
            or not features[name].get("available")
        )
        if unavailable:
            errors.append(f"expected decoder features unavailable: {', '.join(unavailable)}")
    except (OSError, TypeError, ValueError, urllib.error.URLError) as exc:
        errors.append(f"OpenWebRX feature report check failed: {exc}")

    if not os.path.ismount(ARCHIVE):
        # The direct autofs mount point reports separately from its nested NFS
        # mount on some systemd versions, so accept a successful read test too.
        try:
            (ARCHIVE / "metadata").stat()
        except OSError as exc:
            errors.append(f"SDR archive is unavailable: {exc}")

    listeners = run("ss", "-lntH").stdout.splitlines()
    if any(":1234 " in line and "127.0.0.1:1234" not in line for line in listeners):
        errors.append("rtl_tcp is listening outside loopback")

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("PASS host: edcore-sdr")
    print(f"PASS USB: {usb.stdout.strip()}")
    print(f"PASS profiles: {len(profiles)} with 125 MHz HF offset contract")
    print(f"PASS decoders: {len(EXPECTED_FEATURES)} required feature groups available")
    print("PASS services: OpenWebRX, Cockpit, and QEMU guest agent active")
    print("PASS access: OpenWebRX HTTP 200 and rtl_tcp has no non-loopback listener")
    print("PASS storage: protected SDR NFS archive is readable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
