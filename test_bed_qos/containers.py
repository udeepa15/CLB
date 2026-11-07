#!/usr/bin/env python3
"""containers.py - run the victim server containers (runc) for every server tenant of a tenants file.

All containers share ONE read-only rootfs (victim_bundle/rootfs). Each container gets its own small
bundle directory holding only config.json (network namespace, cpuset, command); the old harness
copied the whole 65 MB bundle per container per trial. Containers are started in parallel.

    sudo python3 containers.py start [--tenants FILE] [--protocol http] [--port 8080]
    sudo python3 containers.py stop
"""
import argparse
import copy
import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import tenants as tn  # noqa: E402

TEMPLATE = os.path.join(BASE_DIR, "victim_bundle", "config.json")
ROOTFS = os.path.join(BASE_DIR, "victim_bundle", "rootfs")
BUNDLE_DIR = os.path.join(BASE_DIR, "run", "bundles")
PREFIX = "victim_"


def container_id(t):
    return PREFIX + t.name


def write_bundle(t, protocol, port, template):
    cfg = copy.deepcopy(template)
    cfg["root"] = {"path": ROOTFS, "readonly": True}
    cfg["hostname"] = t.name
    for ns in cfg["linux"]["namespaces"]:
        if ns["type"] == "network":
            ns["path"] = f"/var/run/netns/{t.ns}"
    res = cfg["linux"].setdefault("resources", {})
    if t.cpus:
        res["cpu"] = {"cpus": t.cpus}
    else:
        res.pop("cpu", None)
    cfg["process"]["args"] = ["sh", "-c", f"exec python3 /victim_server.py {protocol} {port}"]
    d = os.path.join(BUNDLE_DIR, t.name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump(cfg, f, indent=1)
    return d


def _port_open(ip, port, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((ip, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def running_ids():
    out = subprocess.run(["runc", "list", "-q"], capture_output=True, text=True).stdout
    return [c for c in out.split() if c.startswith((PREFIX, "victim_container_"))]


def stop(parallel=16):
    """Kill and delete every victim container (also the old victim_container_N ids)."""
    def _one(cid):
        subprocess.run(["runc", "kill", cid, "KILL"], capture_output=True)
        subprocess.run(["runc", "delete", "-f", cid], capture_output=True)
    ids = running_ids()
    with ThreadPoolExecutor(parallel) as ex:
        list(ex.map(_one, ids))
    return len(ids)


def start(topo, protocol="http", port=None, parallel=16, timeout=30):
    """Start one container per server tenant; returns {tenant name: ready?}. Raises if any fails."""
    port = port or topo.server_port
    stop(parallel)
    template = json.load(open(TEMPLATE))
    servers = topo.servers()

    def _one(t):
        d = write_bundle(t, protocol, port, template)
        log_path = os.path.join(d, "runc.log")
        # NEVER give `runc run -d` a pipe: the detached container inherits it and the caller would wait
        # forever for EOF. A plain file is safe.
        with open(log_path, "w") as log:
            r = subprocess.run(["runc", "run", "--bundle", d, "-d", container_id(t)],
                               stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            return t.name, f"runc run failed: {open(log_path).read().strip()[-200:]}"
        return t.name, None if _port_open(t.ip, port, timeout) else f"port {t.ip}:{port} not open after {timeout}s"

    with ThreadPoolExecutor(parallel) as ex:
        results = dict(ex.map(_one, servers))
    bad = {k: v for k, v in results.items() if v}
    if bad:
        raise RuntimeError(f"{len(bad)}/{len(servers)} containers failed: " +
                           "; ".join(f"{k}: {v}" for k, v in list(bad.items())[:5]))
    return len(servers)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["start", "stop"])
    ap.add_argument("--tenants", default=None)
    ap.add_argument("--protocol", default="http")
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("ERROR: must be root")
    if a.action == "stop":
        print(f"stopped {stop()} containers")
        return
    t0 = time.time()
    n = start(tn.load(a.tenants), a.protocol, a.port)
    print(f"{n} victim containers ready in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
