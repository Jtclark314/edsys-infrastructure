#!/usr/bin/env python3
"""Fixed-scope read collector and disposable Docker rehearsal worker.

No request can supply a command, path, image, network, container, or URL.
Production Docker operations are inspect-only. Mutations name the lab prefix.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

STATE = Path("/opt/edsys-workhorse/edsys-ai-portal/data/time-machine")
LAB_ROOT = Path("/mnt/ai-store/private/time-machine-lab")
SOURCE = Path(__file__).resolve().parent
COLLECTOR_VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PREFIX = "edsys-tm-lab-"
LABEL = "com.edsys.time-machine"
SCENARIOS = {"dns", "storage", "release", "healthy", "ambiguous"}
# Reuse the installed Portal image's Python interpreter; never pull a floating lab image.
PRODUCTION = {"portal": "edsys-ai-portal", "litellm": "workhorse-litellm", "database": "workhorse-litellm-db"}


def now():
    return datetime.now(timezone.utc).isoformat()


def command(args, timeout=30, check=True):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        # Docker errors may contain private arguments. Only report command category.
        raise RuntimeError(f"{args[0]} operation failed (exit {result.returncode})")
    return result


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o2770)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o660)
    os.fchmod(fd, 0o660)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def inspect(name):
    result = command(["docker", "inspect", name], check=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def lab_subnet():
    """Choose a small explicit subnet without altering Docker's global pools."""
    networks = command(["docker", "network", "ls", "-q"]).stdout.split()
    occupied = []
    if networks:
        for network in json.loads(command(["docker", "network", "inspect", *networks]).stdout):
            for config in network.get("IPAM", {}).get("Config", []) or []:
                if config.get("Subnet"):
                    occupied.append(ipaddress.ip_network(config["Subnet"], strict=False))
    for route in json.loads(command(["ip", "-json", "route", "show", "table", "all"]).stdout):
        if route.get("dst") and route["dst"] != "default":
            occupied.append(ipaddress.ip_network(route["dst"], strict=False))
    for candidate in ipaddress.ip_network("10.251.240.0/20").subnets(new_prefix=28):
        if not any(candidate.version == network.version and candidate.overlaps(network) for network in occupied):
            return str(candidate)
    raise RuntimeError("No conflict-free lab subnet is available in the dedicated candidate range")


def state_of(info):
    if not info:
        return "unknown"
    status = info.get("State", {})
    if not status.get("Running"):
        return "down"
    health = status.get("Health", {}).get("Status")
    return "degraded" if health == "unhealthy" else "unknown" if health == "starting" else "ok"


def observation(scope="portal", run_id=None, phase="observation"):
    return {"version": 1, "id": uuid.uuid4().hex, "observed_at": now(), "scope": scope,
            "collector_version": COLLECTOR_VERSION,
            "run_id": run_id, "phase": phase, "nodes": [], "edges": [], "evidence": [], "gaps": []}


def add_node(snap, key, label, kind, state, facts, summary, basis="observed", source="Docker inspect / bounded metadata"):
    evidence_id = f"{key}.observation"
    snap["evidence"].append({"id": evidence_id, "source": source, "summary": summary, "basis": basis})
    snap["nodes"].append({"id": key, "label": label, "kind": kind, "state": state, "facts": facts, "evidence": [evidence_id]})


def add_edge(snap, a, b, relation, confidence="verified", required=True, summary=None):
    eid = f"{a}.{b}.link"
    snap["evidence"].append({"id": eid, "source": "Time Machine dependency collector" if confidence == "verified" else "Reviewed first-application dependency declaration",
        "summary": summary or f"{a} {relation} {b}.", "basis": "observed" if confidence == "verified" else "declared"})
    snap["edges"].append({"source": a, "target": b, "relationship": relation,
        "confidence": confidence, "required": required, "evidence": [eid]})


def save_snapshot(snap):
    write_json(STATE / "observations" / f"{snap['id']}.json", snap)
    return snap["id"]


def stage_history_backup():
    """Stage a transactionally consistent index for the existing encrypted backup."""
    database = STATE / "history.sqlite"
    if not database.exists():
        return
    directory = STATE / "recovery"
    directory.mkdir(exist_ok=True, mode=0o2770)
    target = directory / f"history.{uuid.uuid4().hex}.tmp"
    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("History backup integrity check failed")
        target.chmod(0o660)
        os.replace(target, directory / "history.sqlite")
        write_json(directory / "manifest.json", {"at": now(), "integrity": "ok", "sha256": hashlib.sha256((directory / "history.sqlite").read_bytes()).hexdigest()})
    finally:
        if target.exists():
            target.unlink()


def collect():
    snap = observation()
    containers = {key: inspect(name) for key, name in PRODUCTION.items()}
    add_node(snap, "host", "9950x", "host", "ok", {"collector": "running", "observation_interval": "5 minutes"}, "Collector executed on the 9950x host; this is not a full hardware health check.", source="Local collector process")
    for key, info in containers.items():
        facts = {"container": PRODUCTION[key]}
        if info:
            facts.update({"image": info.get("Image", "unknown"), "container_state": info["State"]["Status"],
                "health_check": info["State"].get("Health", {}).get("Status", "not configured"),
                "started_at": info["State"].get("StartedAt", "unknown")})
        add_node(snap, key, {"portal": "AI Portal", "litellm": "LiteLLM broker", "database": "Broker database"}[key],
            {"portal": "application", "litellm": "service", "database": "database"}[key], state_of(info), facts,
            "Observed container state and image identity. Running state alone does not verify application behavior." if info else "Container inspection did not return evidence.")
        add_edge(snap, key, "host", "runs on", "verified" if info else "unknown")
    portal = containers["portal"]
    broker = containers["litellm"]
    portal_env = dict(item.split("=", 1) for item in (portal or {}).get("Config", {}).get("Env", []) if "=" in item)
    broker_env = dict(item.split("=", 1) for item in (broker or {}).get("Config", {}).get("Env", []) if "=" in item)
    # Inspect only the host portion. Never persist credentials, query strings or env records.
    destination = urlsplit(portal_env.get("LITELLM_BASE_URL", "")).hostname
    database_host = urlsplit(broker_env.get("DATABASE_URL", "")).hostname
    endpoint = urlsplit(portal_env.get("LITELLM_BASE_URL", ""))
    try:
        local_addresses = {address["local"] for interface in json.loads(command(["ip", "-json", "address"]).stdout)
            for address in interface.get("addr_info", [])}
        endpoint_addresses = {item[4][0] for item in socket.getaddrinfo(destination, endpoint.port or 80, type=socket.SOCK_STREAM)} if destination else set()
        bindings = (broker or {}).get("NetworkSettings", {}).get("Ports", {})
        broker_verified = any(str(binding.get("HostPort")) == str(endpoint.port or 80)
            and bool(endpoint_addresses & local_addresses)
            and (binding.get("HostIp") in ("0.0.0.0", "::", "") or binding.get("HostIp") in endpoint_addresses)
            for rows in bindings.values() for binding in rows or [])
    except (OSError, ValueError, RuntimeError):
        broker_verified = False
    db_networks = (containers["database"] or {}).get("NetworkSettings", {}).get("Networks", {})
    broker_networks = (broker or {}).get("NetworkSettings", {}).get("Networks", {})
    db_verified = any(database_host in (info.get("Aliases", []) or []) + (info.get("DNSNames", []) or [])
        for name, info in db_networks.items() if name in broker_networks)
    add_edge(snap, "portal", "litellm", "uses for model requests", "verified" if broker_verified else "declared", required=False,
        summary="Observed Portal broker configuration maps to the inspected broker's published host port. Model behavior is not tested." if broker_verified else "Portal config declares a model broker endpoint; destination identity requires further evidence.")
    add_edge(snap, "litellm", "database", "persists broker state in", "verified" if db_verified else "declared",
        summary="Observed broker database hostname matches the inspected database alias on a shared Docker network. Data integrity is not tested." if db_verified else "Broker database is declared in the first-application scope; connection and data integrity are not probed by this collector.")
    storage = next((m for m in (portal or {}).get("Mounts", []) if m.get("Destination") == "/data"), None)
    present = bool(storage and Path(storage["Source"]).is_dir())
    add_node(snap, "storage", "Portal state storage", "storage", "ok" if present else "unknown",
        {"container_destination": "/data", "directory": "present" if present else "unobserved", "mode": "read-write" if storage and storage.get("RW") else "to be confirmed"},
        "Inspected the /data mount and host directory. Writability, backup freshness and data integrity are not established.", source="Docker mount metadata and host directory check")
    add_edge(snap, "portal", "storage", "mounts persistent state", "verified" if present else "unknown")
    dns_name = "9950x.taile832fe.ts.net"
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(dns_name, 443, type=socket.SOCK_STREAM)})
        dns_state = "ok"
    except OSError:
        addresses, dns_state = [], "down"
    add_node(snap, "dns", "Portal name resolution", "dns", dns_state, {"name": dns_name, "answers": ", ".join(addresses)[:250]},
        "Resolved the Portal name from the collector host; controller DNS paths may differ.", source="Host getaddrinfo")
    add_edge(snap, "portal", "dns", "is reached through", "declared", required=False)
    networks = sorted((portal or {}).get("NetworkSettings", {}).get("Networks", {}).keys())
    add_node(snap, "network", "Portal container network", "network", "ok" if networks else "unknown",
        {"memberships": ", ".join(networks)[:250] or "to be confirmed"}, "Inspected container network membership; packet delivery is not proven.")
    add_edge(snap, "portal", "network", "attaches to", "verified" if networks else "unknown")
    probe = command(["curl", "--silent", "--output", "/dev/null", "--write-out", "%{http_code}", "--max-time", "10", "http://127.0.0.1:3020/api/health"], check=False)
    pnode = next(n for n in snap["nodes"] if n["id"] == "portal")
    pnode["facts"]["health_http"] = probe.stdout.strip() if re.fullmatch(r"[0-9]{3}", probe.stdout.strip()) else "unavailable"
    if probe.returncode or probe.stdout.strip() != "200":
        pnode["state"] = "down"
    snap["evidence"].append({"id": "portal.http", "source": "Loopback /api/health HTTP probe", "summary": f"Portal health endpoint returned {pnode['facts']['health_http']}. This does not test model responses or stored data.", "basis": "observed"})
    pnode["evidence"].append("portal.http")
    # These non-secret endpoint names are useful semantic changes without copying URLs.
    pnode["facts"]["broker_host"] = destination or "to be confirmed"
    next(n for n in snap["nodes"] if n["id"] == "litellm")["facts"]["database_host"] = database_host or "to be confirmed"
    snap["gaps"] = ["Scope covers the AI Portal and selected dependencies; it is not the complete EdSys graph.",
        "Configuration and Docker topology can establish dependency identity; no authenticated model request or production data read is performed.",
        "Provider availability, controller routes, redundancy and backup recoverability remain to be confirmed."]
    return save_snapshot(snap)


class Lab:
    def __init__(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Invalid lab run ID")
        self.id = run_id
        self.name = PREFIX + run_id
        self.root = LAB_ROOT / run_id
        self.app = self.name + "-app"
        self.db = self.name + "-db"
        self.release = "stable"
        self.image = None

    def docker(self, *args, check=True):
        return command(["docker", *args], timeout=60, check=check)

    def start_app(self, release="stable"):
        self.release = release
        self.start_container(self.app, "application", release)

    def start_container(self, name, role, release="stable"):
        self.docker("run", "--detach", "--pull=never", "--name", name, "--label", f"{LABEL}={self.id}",
            "--network", self.name, "--network-alias", "database" if role == "database" else "application",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=128m", "--cpus=0.5",
            "--user", "1000:1000", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--mount", f"type=bind,source={self.root},target=/fixture,readonly",
            "--env", f"LAB_ROLE={role}", "--env", f"LAB_RELEASE={release}", "--entrypoint", "python", self.image, "/fixture/lab_service.py")

    def setup(self):
        if not Path("/mnt/ai-store").is_mount():
            raise RuntimeError("AI Store mount is required for disposable lab state")
        if self.root.exists():
            raise RuntimeError("Lab run directory already exists")
        self.root.mkdir(parents=True, mode=0o700)
        shutil.copyfile(SOURCE / "lab_service.py", self.root / "lab_service.py")
        with sqlite3.connect(self.root / "records.sqlite") as db:
            db.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.executemany("INSERT INTO records VALUES (?, ?)", [(1, "synthetic-alpha"), (2, "synthetic-beta"), (3, "synthetic-gamma")])
        (self.root / "destination").write_text("database")
        self.original_hash = hashlib.sha256((self.root / "records.sqlite").read_bytes()).hexdigest()
        current = inspect(PRODUCTION["portal"])
        if not current or not re.fullmatch(r"sha256:[a-f0-9]{64}", current.get("Image", "")):
            raise RuntimeError("A local Portal image with Python is required")
        self.image = current["Image"]
        self.docker("network", "create", "--internal", "--subnet", lab_subnet(), "--label", f"{LABEL}={self.id}", self.name)
        self.start_container(self.db, "database")
        self.start_app()

    def probe(self, container=None):
        code = "import json,urllib.request,urllib.error;\ntry:\n r=urllib.request.urlopen('http://127.0.0.1:8080',timeout=4);print(r.read().decode())\nexcept urllib.error.HTTPError as e: print(e.read().decode())"
        result = self.docker("exec", container or self.app, "python", "-c", code, check=False)
        try:
            return json.loads(result.stdout)
        except ValueError:
            return {"state": "unknown", "reason": "probe unavailable"}

    def wait(self, expected):
        for _ in range(20):
            result = self.probe()
            if result.get("state") == expected:
                return result
            time.sleep(.5)
        raise RuntimeError(f"Lab application did not reach {expected}")

    def remove_container(self, name):
        if name not in (self.app, self.db):
            raise RuntimeError("Refusing non-lab container mutation")
        existing = inspect(name)
        if existing and existing.get("Config", {}).get("Labels", {}).get(LABEL) != self.id:
            raise RuntimeError("Lab ownership label mismatch")
        if existing:
            self.docker("rm", "--force", name)

    def cleanup(self):
        for name in (self.app, self.db):
            self.remove_container(name)
        result = self.docker("network", "inspect", self.name, check=False)
        if result.returncode == 0:
            network = json.loads(result.stdout)[0]
            if network.get("Labels", {}).get(LABEL) != self.id:
                raise RuntimeError("Lab network ownership mismatch")
            self.docker("network", "rm", self.name)
        # Retain tiny synthetic fixture + database as recovery evidence. No pruning.

    def snapshot(self, phase, ambiguous=False):
        snap = observation("rehearsal", self.id, phase)
        app_result, db_result = self.probe(), self.probe(self.db)
        add_node(snap, "application", "Rehearsal application", "application", "down" if ambiguous else app_result["state"],
            {"release": self.release, "probe": "symptom supplied; dependency probes withheld" if ambiguous else app_result.get("reason", "records returned")},
            "Synthetic ambiguous symptom: application unavailable; dependency evidence withheld." if ambiguous else "HTTP probe executed inside the lab application container.", source="Isolated lab / application probe")
        storage = (self.root / "records.sqlite").exists()
        dns = (self.root / "destination").read_text().strip() == "database"
        for key, label, kind, state, facts in [
            ("database", "Rehearsal database", "database", db_result["state"], {"probe": db_result.get("reason", "records returned")}),
            ("storage", "Synthetic records", "storage", "ok" if storage else "down", {"file": "present" if storage else "withdrawn"}),
            ("dns", "Database DNS destination", "dns", "ok" if dns else "down", {"destination": (self.root / "destination").read_text().strip()}),
        ]:
            add_node(snap, key, label, kind, "unknown" if ambiguous else state,
                {"probe": "withheld"} if ambiguous else facts,
                "Evidence deliberately withheld for the ambiguity control." if ambiguous else "Observed within the isolated rehearsal stack.",
                source="Isolated lab / bounded probe")
        add_edge(snap, "application", "database", "reads records from", "unknown" if ambiguous else "verified", summary="Fixture application source and successful baseline request establish its database dependency." if not ambiguous else "Dependency declared; its observation was withheld.")
        add_edge(snap, "application", "dns", "resolves destination through", "unknown" if ambiguous else "verified")
        add_edge(snap, "database", "storage", "reads SQLite records from", "unknown" if ambiguous else "verified")
        if ambiguous:
            snap["gaps"] = ["Synthetic ambiguity control: root cause is intentionally unknown; no fault was injected."]
        save_snapshot(snap)
        return snap


def run_rehearsal(run_id, scenario):
    if scenario not in SCENARIOS:
        raise ValueError("Invalid rehearsal scenario")
    lab = Lab(run_id)
    started = time.monotonic()
    result = {"id": run_id, "scenario": scenario, "status": "running", "created_at": now(), "checks": []}
    path = STATE / "results" / f"{run_id}.json"
    write_json(path, result)

    def check(name, passed, detail):
        result["checks"].append({"name": name, "passed": bool(passed), "detail": detail})
        write_json(path, result)
        if not passed:
            raise RuntimeError(name + " failed")

    try:
        lab.setup()
        baseline = lab.wait("ok")
        check("Baseline data", baseline.get("records") == [[1, "synthetic-alpha"], [2, "synthetic-beta"], [3, "synthetic-gamma"]], "All three synthetic records returned through the application.")
        network = json.loads(lab.docker("network", "inspect", lab.name).stdout)[0]
        isolated = network.get("Internal") is True
        for name in (lab.app, lab.db):
            info = inspect(name)
            isolated = isolated and not info["HostConfig"].get("PortBindings") and set(info["NetworkSettings"]["Networks"]) == {lab.name}
        check("Isolation", isolated, "Internal network, no host ports, fixed lab containers, read-only fixture mounts.")
        lab.snapshot("baseline")
        if scenario == "dns":
            (lab.root / "destination").write_text("missing.time-machine.invalid")
        elif scenario == "storage":
            (lab.root / "records.sqlite").rename(lab.root / "records.withdrawn")
        elif scenario == "release":
            lab.remove_container(lab.app)
            lab.start_app("broken")
        if scenario in {"dns", "storage", "release"}:
            fault = lab.wait("down")
            expected = {"dns": "DNS resolution failed", "storage": "database unavailable", "release": "release health check failed"}[scenario]
            check("Failure observed", fault.get("reason") == expected, expected)
            lab.snapshot("fault observed")
        elif scenario == "ambiguous":
            snap = lab.snapshot("evidence withheld", ambiguous=True)
            check("Ambiguity preserved", all(n["state"] == "unknown" for n in snap["nodes"] if n["id"] != "application"), "Only the synthetic symptom is known. Dependencies remain unknown.")
        else:
            lab.snapshot("healthy control")
            check("Healthy control", lab.probe().get("state") == "ok", "No fault introduced; application remains healthy.")
        if scenario == "dns":
            (lab.root / "destination").write_text("database")
        elif scenario == "storage":
            (lab.root / "records.withdrawn").rename(lab.root / "records.sqlite")
        elif scenario == "release":
            lab.remove_container(lab.app)
            lab.start_app()
        recovered = lab.wait("ok")
        check("Application recovery", recovered == baseline, "Application returns the exact baseline records after recovery.")
        check("Data integrity", hashlib.sha256((lab.root / "records.sqlite").read_bytes()).hexdigest() == lab.original_hash, "SQLite file SHA-256 equals the pre-rehearsal baseline.")
        lab.snapshot("recovered" if scenario in {"dns", "storage", "release"} else "control complete")
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    finally:
        try:
            lab.cleanup()
            result["checks"].append({"name": "Cleanup", "passed": True, "detail": "Removed only containers and network bearing this lab run's ownership label."})
        except Exception:
            result["status"] = "failed"
            result["checks"].append({"name": "Cleanup", "passed": False, "detail": "Lab cleanup requires inspection; no production resources were targeted."})
        result["duration_seconds"] = round(time.monotonic() - started, 2)
        result["finished_at"] = now()
        write_json(path, result)
    return result


def process_requests():
    requests = STATE / "requests"
    requests.mkdir(exist_ok=True, mode=0o2770)
    for path in sorted(requests.glob("*.json")):
        try:
            if path.is_symlink() or path.stat().st_size > 1024 or not re.fullmatch(r"[a-f0-9]{32}", path.stem):
                continue
            value = json.loads(path.read_bytes())
            if set(value) != {"id", "scenario"} or value["id"] != path.stem or value["scenario"] not in SCENARIOS:
                continue
            result_path = STATE / "results" / path.name
            if result_path.exists():
                prior = json.loads(result_path.read_bytes())
                if prior.get("status") == "running":
                    # Never replay an interrupted mutation. Clean only this run's lab resources.
                    try:
                        Lab(path.stem).cleanup()
                        detail = "Worker restarted; lab cleaned. Start a new rehearsal to retry."
                    except Exception:
                        detail = "Worker restarted; inspect remaining lab resources before retrying."
                    write_json(result_path, {**prior, "status": "interrupted", "finished_at": now(), "error": detail})
                path.unlink()
                continue
            run_rehearsal(value["id"], value["scenario"])
            path.unlink()
        except (OSError, ValueError):
            continue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["collect", "serve", "rehearse"])
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="healthy")
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True, mode=0o2770)
    with (STATE / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "collect":
            print(json.dumps({"snapshot_id": collect()}))
        elif args.action == "rehearse":
            result = run_rehearsal(uuid.uuid4().hex, args.scenario)
            print(json.dumps(result))
            return 0 if result["status"] == "passed" else 1
        else:
            last_collect = 0
            while True:
                write_json(STATE / "heartbeat.json", {"at": now()})
                if time.monotonic() - last_collect >= 300:
                    try:
                        collect()
                        stage_history_backup()
                    except Exception as exc:
                        print(json.dumps({"event": "collection_failed", "type": type(exc).__name__}), flush=True)
                    last_collect = time.monotonic()
                process_requests()
                time.sleep(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
