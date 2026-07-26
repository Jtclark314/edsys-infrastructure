#!/usr/bin/env python3
"""Commission the private Gmail/Amazon portion of Kindle Drop on Basecamp."""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from kindle_drop import GMAIL_SCOPE, save_dpapi_json


def split_values(value: str) -> list[str]:
    return sorted({item.strip().lower() for item in value.split(",") if item.strip()})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-json", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()
    if not args.client_json.is_file():
        raise SystemExit("Google OAuth desktop-client JSON was not found.")

    gmail_address = input("Dedicated Gmail address: ").strip()
    kindle_address = getpass.getpass(
        "Private Kindle address (input hidden): "
    ).strip()
    sender_domains = split_values(
        input("Proven Amazon sender domain(s), comma separated: ")
    )
    download_hosts = split_values(
        input("Proven Amazon download host(s), comma separated: ")
    )
    if (
        "@" not in gmail_address
        or "@" not in kindle_address
        or not sender_domains
        or not download_hosts
    ):
        raise SystemExit("All commissioning values are required.")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(args.client_json), scopes=[GMAIL_SCOPE]
    )
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    private = {
        "schema": 1,
        "gmail_address": gmail_address,
        "kindle_address": kindle_address,
        "amazon_sender_domains": sender_domains,
        "amazon_download_hosts": download_hosts,
        "oauth": json.loads(credentials.to_json()),
    }
    save_dpapi_json(args.private_output, private)
    subprocess.run(
        [
            "icacls.exe",
            str(args.private_output.parent),
            "/inheritance:r",
            "/grant:r",
            "SYSTEM:(OI)(CI)(F)",
            "BUILTIN\\Administrators:(OI)(CI)(F)",
            "NT AUTHORITY\\LOCAL SERVICE:(OI)(CI)(M)",
        ],
        check=True,
    )
    print("Commissioning data encrypted with DPAPI LocalMachine and ACL restricted.")
    print("Delete the source OAuth client JSON after the dispatcher health check passes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
