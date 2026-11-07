#!/usr/bin/env python3
"""topology.py - build / tear down the tenant network described by a tenants file (see tenants.py).

    sudo python3 topology.py up   [--tenants FILE]     # bridge + one netns/veth pair per tenant
    sudo python3 topology.py down                       # remove every testbed namespace and the bridge

`down` removes every namespace that looks like a testbed tenant (ns_victim*, ns_attacker*, ns_client*),
not just the ones in the current file, so switching tenants files never leaves stale tenants behind.
Commands are batched (`ip -batch`), so 100 tenants take a few seconds.
setup_topology.sh is a thin wrapper around this script (kept for the old command line).
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tenants as tn  # noqa: E402


def _run(cmd, inp=None, check=True):
    r = subprocess.run(cmd, input=inp, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip()[-500:]}")
    return r


def _batch(lines, netns=None):
    if not lines:
        return
    cmd = ["ip"] + (["-n", netns] if netns else []) + ["-batch", "-"]
    _run(cmd, "\n".join(lines) + "\n")


def testbed_namespaces():
    out = _run(["ip", "netns", "list"], check=False).stdout
    return [ln.split()[0] for ln in out.splitlines() if ln.split() and ln.split()[0].startswith(tn.NS_PREFIXES)]


def down(topo=None, bridge="br-mesh"):
    """Remove all testbed namespaces (their veth peers disappear with them) and the bridge."""
    for ns in testbed_namespaces():
        _run(["ip", "netns", "delete", ns], check=False)
    br = topo.bridge if topo else bridge
    _run(["ip", "link", "delete", br], check=False)
    # host-side veths whose namespace end was already gone (interrupted earlier runs)
    out = _run(["ip", "-o", "link", "show", "type", "veth"], check=False).stdout
    for ln in out.splitlines():
        name = ln.split(":")[1].strip().split("@")[0]
        if name.startswith(("veth-vic", "veth-att", "veth-cli")):
            _run(["ip", "link", "delete", name], check=False)


def up(topo):
    t0 = time.time()
    down(topo)
    _batch([f"link add name {topo.bridge} type bridge",
            f"addr add {topo.gateway}/{topo.prefix} dev {topo.bridge}",
            f"link set dev {topo.bridge} up"])
    for t in topo.tenants:
        _run(["ip", "netns", "add", t.ns])
    host = []
    for t in topo.tenants:
        host += [f"link add name {t.ns_veth} netns {t.ns} type veth peer name {t.br_veth}",
                 f"link set dev {t.br_veth} master {topo.bridge}",
                 f"link set dev {t.br_veth} up"]
    _batch(host)
    for t in topo.tenants:
        _batch([f"addr add {t.ip}/{topo.prefix} dev {t.ns_veth}",
                f"link set dev {t.ns_veth} up",
                "link set dev lo up",
                f"route add default via {topo.gateway}"], netns=t.ns)
    _run(["sysctl", "-qw", "net.ipv4.ip_forward=1"])
    return time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["up", "down"])
    ap.add_argument("--tenants", default=None, help="tenants file (default $TENANTS_FILE or tenants/default.json)")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("ERROR: must be root")
    topo = tn.load(a.tenants)
    if a.action == "down":
        down(topo)
        print("Topology removed.")
        return
    secs = up(topo)
    print(f"Topology '{topo.name}' up: bridge {topo.bridge} {topo.gateway}/{topo.prefix}, "
          f"{len(topo.tenants)} tenants ({len(topo.victims())} victims, {len(topo.attackers())} attackers) in {secs:.1f}s")


if __name__ == "__main__":
    main()
