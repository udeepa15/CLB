#!/usr/bin/env python3
"""tenants.py - the testbed topology as data: ONE file that every script reads.

A tenants file lists the tenants on the node. Each tenant has a network namespace, an IPv4 address,
a veth pair (host side enslaved to the bridge), a role, and optionally a server container and a cpuset.

  role   : ground truth, "victim" or "attacker". Read ONLY by the oracle arms (static caps, reactive and
           Markov controllers are told whom to cap) and by the evaluation (truth labels).
           The automatic-identification arms never read it.
  server : the tenant runs a victim HTTP server container (runc) on `server_port`.
  cpus   : cpuset for the tenant's container/processes ("" = not pinned).

Default: tenants/default.json (the original 3 victims + 1 attacker on 10.0.0.0/24). Select another
file with the TENANTS_FILE environment variable. Generate a large topology:

    python3 tenants.py gen --victims 95 --attackers 5 --out tenants/scale100.json
    python3 tenants.py show [--tenants FILE]
    python3 tenants.py attackers [--tenants FILE]      # 'ns ns_veth br_veth ip' per attacker
"""
import argparse
import ipaddress
import json
import os
import sys
from dataclasses import asdict, dataclass, field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(BASE_DIR, "tenants", "default.json")
IFNAMSIZ = 15                     # max interface name length (16 incl. NUL)
ROLES = ("victim", "attacker")
NS_PREFIXES = ("ns_victim", "ns_attacker", "ns_client")   # what topology.py may delete


@dataclass
class Tenant:
    name: str
    ns: str
    ip: str
    br_veth: str
    ns_veth: str
    role: str = "victim"
    server: bool = False
    cpus: str = ""


@dataclass
class Topology:
    name: str
    bridge: str
    gateway: str
    prefix: int
    tenants: list = field(default_factory=list)
    server_port: int = 8080
    path: str = ""

    # ------------------------------------------------------------ queries
    def victims(self):
        return [t for t in self.tenants if t.role == "victim"]

    def attackers(self):
        return [t for t in self.tenants if t.role == "attacker"]

    def servers(self):
        return [t for t in self.tenants if t.server]

    def clients(self):
        """Tenants that run a victim load client: every victim (server or client-only)."""
        return [t for t in self.tenants if t.role == "victim"]

    def ips(self, role=None):
        return [t.ip for t in self.tenants if role is None or t.role == role]

    def by(self, **kw):
        """First tenant whose attributes match, e.g. topo.by(ns="ns_attacker")."""
        for t in self.tenants:
            if all(getattr(t, k) == v for k, v in kw.items()):
                return t
        raise KeyError(f"no tenant with {kw}")

    def network(self):
        return ipaddress.ip_network(f"{self.gateway}/{self.prefix}", strict=False)

    # ------------------------------------------------------------ checks
    def validate(self):
        net = self.network()
        for attr in ("name", "ns", "ip", "br_veth", "ns_veth"):
            vals = [getattr(t, attr) for t in self.tenants]
            dup = {v for v in vals if vals.count(v) > 1}
            if dup:
                raise ValueError(f"{self.path}: duplicate tenant {attr}: {sorted(dup)}")
        for t in self.tenants:
            if t.role not in ROLES:
                raise ValueError(f"{t.name}: role must be one of {ROLES}, got {t.role!r}")
            if ipaddress.ip_address(t.ip) not in net or t.ip == self.gateway:
                raise ValueError(f"{t.name}: {t.ip} is not a host address in {net} (gateway {self.gateway})")
            for dev in (t.br_veth, t.ns_veth):
                if len(dev) > IFNAMSIZ:
                    raise ValueError(f"{t.name}: interface name {dev!r} longer than {IFNAMSIZ} chars")
            if not t.ns.startswith(NS_PREFIXES):
                raise ValueError(f"{t.name}: namespace {t.ns!r} must start with one of {NS_PREFIXES}")
        return self

    def to_json(self):
        d = asdict(self)
        d.pop("path")
        return json.dumps(d, indent=2)


def load(path=None):
    """Load and validate a tenants file (default: $TENANTS_FILE or tenants/default.json)."""
    path = path or os.environ.get("TENANTS_FILE") or DEFAULT_FILE
    if not os.path.isabs(path) and not os.path.exists(path):
        path = os.path.join(BASE_DIR, path)
    with open(path) as f:
        d = json.load(f)
    topo = Topology(name=d["name"], bridge=d["bridge"], gateway=d["gateway"], prefix=int(d["prefix"]),
                    tenants=[Tenant(**t) for t in d["tenants"]], server_port=int(d.get("server_port", 8080)),
                    path=os.path.abspath(path))
    return topo.validate()


def _expand_cpus(pool):
    cpus = []
    for part in pool.split(","):
        lo, _, hi = part.partition("-")
        cpus += list(range(int(lo), int(hi or lo) + 1))
    return cpus


def generate(n_victims, n_attackers, name, cpus="", spread=False, n_clients=0):
    """Victims 10.0.1.x.. (servers), attackers 10.0.128.x.., client-only tenants 10.0.64.x..; bridge 10.0.0.1/16.

    cpus     : cpuset pool given to every tenant (shared, like pods without CPU pinning), or
    spread   : assign the pool's CPUs round-robin, one per tenant.
    n_clients: extra victim tenants WITHOUT a server (they only run a load client). Needed when there is a
               single server: a tenant calling its own IP never leaves its namespace (loopback), so its
               traffic would never cross a tenant port."""
    def addr(base, i):              # i is 1-based; 254 hosts per /24
        return f"10.0.{base + (i - 1) // 254}.{(i - 1) % 254 + 1}"
    pool = _expand_cpus(cpus) if cpus else []
    tenants = []
    for i in range(1, n_victims + 1):
        tenants.append(Tenant(name=f"victim{i}", ns=f"ns_victim{i}", ip=addr(1, i), br_veth=f"veth-vic{i}-br",
                              ns_veth=f"veth-victim{i}", role="victim", server=True))
    for k in range(1, n_clients + 1):
        tenants.append(Tenant(name=f"client{k}", ns=f"ns_client{k}", ip=addr(64, k), br_veth=f"veth-cli{k}-br",
                              ns_veth=f"veth-client{k}", role="victim", server=False))
    for j in range(1, n_attackers + 1):
        sfx = "" if j == 1 else str(j)
        tenants.append(Tenant(name=f"attacker{j}", ns=f"ns_attacker{sfx}", ip=addr(128, j),
                              br_veth=f"veth-att{sfx}-br", ns_veth=f"veth-attacker{sfx}", role="attacker"))
    for k, t in enumerate(tenants):
        t.cpus = (str(pool[k % len(pool)]) if spread else cpus) if pool else ""
    return Topology(name=name, bridge="br-mesh", gateway="10.0.0.1", prefix=16, tenants=tenants).validate()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="generate a tenants file")
    g.add_argument("--victims", type=int, required=True)
    g.add_argument("--attackers", type=int, required=True)
    g.add_argument("--clients", type=int, default=0, help="client-only victim tenants (no server)")
    g.add_argument("--cpus", default="", help="cpuset pool, e.g. 1-7")
    g.add_argument("--spread", action="store_true", help="round-robin one CPU of the pool per tenant")
    g.add_argument("--name", default=None)
    g.add_argument("--out", required=True)
    s = sub.add_parser("show", help="summarise a tenants file")
    s.add_argument("--tenants", default=None)
    r = sub.add_parser("attackers", help="print 'ns ns_veth br_veth ip' per attacker (for shell scripts)")
    r.add_argument("--tenants", default=None)
    a = ap.parse_args()
    if a.cmd == "attackers":
        for t in load(a.tenants).attackers():
            print(t.ns, t.ns_veth, t.br_veth, t.ip)
        return
    if a.cmd == "gen":
        topo = generate(a.victims, a.attackers, a.name or os.path.splitext(os.path.basename(a.out))[0], a.cpus, a.spread,
                        a.clients)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            f.write(topo.to_json() + "\n")
        print(f"wrote {a.out}: {len(topo.victims())} victims, {len(topo.attackers())} attackers, "
              f"{len(topo.servers())} servers, network {topo.network()}")
    else:
        topo = load(a.tenants)
        print(f"{topo.path}: {topo.name}, bridge {topo.bridge} {topo.gateway}/{topo.prefix}, "
              f"{len(topo.victims())} victims, {len(topo.attackers())} attackers, {len(topo.servers())} servers")
        for t in topo.tenants[:6] + ([None] if len(topo.tenants) > 8 else []) + topo.tenants[max(6, len(topo.tenants) - 2):]:
            print("  ..." if t is None else f"  {t.name:<11} {t.role:<8} {t.ip:<14} {t.ns:<15} {t.br_veth:<15} "
                                             f"server={t.server} cpus={t.cpus or '-'}")


if __name__ == "__main__":
    sys.exit(main())
