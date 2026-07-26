#!/usr/bin/env python3
"""Print Tailnet IPv4 CIDRs excluding the explicitly approved hosts."""

from __future__ import annotations

import argparse
import ipaddress


TAILNET = ipaddress.ip_network("100.64.0.0/10")


def complement(values: list[str]) -> list[ipaddress.IPv4Network]:
    ranges: list[ipaddress.IPv4Network] = [TAILNET]
    for value in sorted(set(values)):
        address = ipaddress.ip_address(value)
        if not isinstance(address, ipaddress.IPv4Address) or address not in TAILNET:
            raise ValueError(f"approved address is not Tailnet IPv4: {value}")
        host = ipaddress.ip_network(f"{address}/32")
        updated: list[ipaddress.IPv4Network] = []
        for network in ranges:
            updated.extend(network.address_exclude(host) if address in network else [network])
        ranges = updated
    return ranges


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("approved", nargs="+")
    args = parser.parse_args()
    print("\n".join(str(network) for network in complement(args.approved)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
