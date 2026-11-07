#!/usr/bin/env python3
"""test_limiter.py - measure that each eBPF limiter delivers the configured cap (needs root).

Builds the topology, attaches a data plane, caps the attacker, floods from ns_attacker with hping3
and measures the bytes that actually reach victim1 (rx_bytes of veth-victim1).

  case        data plane      what it checks
  single      qos_dynamic     1 flood                                 -> delivered ~= cap
  multi_cpu   qos_dynamic     4 floods pinned to 4 CPUs               -> delivered ~= cap (one exact bucket;
                                                                         per-CPU buckets gave up to 4x)
  rewrite     qos_dynamic     cap rewritten every 20 ms during flood  -> delivered ~= cap (writes no longer
                                                                         refill the bucket)
  port_key    qos_auto        cap on the attacker's port ifindex      -> delivered ~= cap
  tiered      qos_tiered      static seed (reference design)          -> delivered ~= cap
  victims_ok  qos_dynamic     victims uncapped (rate 0) while capped attacker floods -> victim ping works

PASS if delivered is within [0.85, 1.10] x cap and the offered load is at least 3x the cap.

Usage: sudo python3 test_limiter.py [--cap 5000000] [--seconds 4] [--cases single multi_cpu ...]
"""
import argparse
import os
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import qos_controller as qc          # noqa: E402
import qos_controller_auto as qa     # noqa: E402

ATT, VIC1 = "10.0.0.20", "10.0.0.10"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def counter(ns, dev, what):
    return int(sh(f"ip netns exec {ns} cat /sys/class/net/{dev}/statistics/{what}").stdout.strip())


def ifindex(dev):
    return int(open(f"/sys/class/net/{dev}/ifindex").read())


def setup(mode, env=None):
    sh(f"{BASE}/setup_topology.sh cleanup")
    r = sh(f"{BASE}/setup_topology.sh")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    e = {**os.environ, **(env or {})}
    r = subprocess.run([f"{BASE}/attach_ebpf.sh", mode], capture_output=True, text=True, env=e, cwd=BASE)
    if r.returncode != 0:
        raise RuntimeError(f"attach {mode} failed:\n{r.stdout[-600:]}\n{r.stderr[-600:]}")


def measure(n_floods, seconds, during=None):
    """Start n hping3 floods (pinned to CPUs 1..n), return (offered B/s, delivered B/s)."""
    procs = [subprocess.Popen(["ip", "netns", "exec", "ns_attacker", "taskset", "-c", str(1 + i), "hping3", "-n",
                               "--udp", "-p", "8080", "-d", "1000", "--flood", VIC1],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(n_floods)]
    stop = threading.Event()
    th = None
    if during:
        th = threading.Thread(target=during, args=(stop,), daemon=True)
        th.start()
    try:
        time.sleep(1.0)                                   # warm-up: initial burst + ARP
        t0 = time.time()
        o0, d0 = counter("ns_attacker", "veth-attacker", "tx_bytes"), counter("ns_victim1", "veth-victim1", "rx_bytes")
        time.sleep(seconds)
        t1 = time.time()
        o1, d1 = counter("ns_attacker", "veth-attacker", "tx_bytes"), counter("ns_victim1", "veth-victim1", "rx_bytes")
    finally:
        stop.set()
        for p in procs:
            p.kill()
        sh("pkill -9 hping3")
        if th:
            th.join(2)
    dt = t1 - t0
    return (o1 - o0) / dt, (d1 - d0) / dt


def seed_dynamic(cap):
    for v in qc.VICTIM_IPS:
        assert qc.update_tenant_rate_limit(v, 0)
    assert qc.update_tenant_rate_limit(ATT, cap)


def case_single(cap, secs):
    setup("qos_dynamic"); seed_dynamic(cap)
    return measure(1, secs)


def case_multi_cpu(cap, secs):
    setup("qos_dynamic"); seed_dynamic(cap)
    return measure(4, secs)


def case_rewrite(cap, secs):
    setup("qos_dynamic"); seed_dynamic(cap)

    def rewriter(stop):                                   # like the adaptive controller's 20 ms spike polling
        while not stop.is_set():
            qc.update_tenant_rate_limit(ATT, cap)
            time.sleep(0.02)
    return measure(1, secs, during=rewriter)


def case_port_key(cap, secs):
    setup("qos_auto")
    assert qa.write_limit("port", ifindex("veth-att-br"), cap)
    return measure(1, secs)


def case_tiered(cap, secs):
    setup("qos_tiered", env={"TIER_L2_RATE_BPS": str(cap)})
    return measure(1, secs)


def case_victims_ok(cap, secs):
    setup("qos_dynamic"); seed_dynamic(cap)
    result = {}

    def pinger(stop):
        r = sh("ip netns exec ns_victim2 ping -c 5 -i 0.2 -W 1 10.0.0.10")
        result["ok"] = r.returncode == 0
    off, dlv = measure(1, secs, during=pinger)
    if not result.get("ok"):
        raise AssertionError("victim2 -> victim1 ping failed while the capped attacker flooded")
    c = qc.read_tenant_counters(VIC1)       # victim1's own traffic (ICMP replies) passed, never dropped
    if c is not None and c[1] != 0:
        raise AssertionError(f"victim1 had {c[1]} drops although it is uncapped")
    return off, dlv


CASES = {"single": case_single, "multi_cpu": case_multi_cpu, "rewrite": case_rewrite,
         "port_key": case_port_key, "tiered": case_tiered, "victims_ok": case_victims_ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=5_000_000, help="attacker cap, bytes/s")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--cases", nargs="+", default=list(CASES), choices=list(CASES))
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("needs root: sudo python3 test_limiter.py")
    failed = []
    print(f"cap = {a.cap / 1e6:.2f} MB/s, window = {a.seconds}s\n")
    print(f"{'case':<11} {'offered MB/s':>13} {'delivered MB/s':>15} {'delivered/cap':>14}  result")
    try:
        for name in a.cases:
            try:
                off, dlv = CASES[name](a.cap, a.seconds)
                ratio = dlv / a.cap
                ok = 0.85 <= ratio <= 1.10 and off >= 3 * a.cap
                why = "" if ok else ("  (offered load too low)" if off < 3 * a.cap else "  (delivered off target)")
                print(f"{name:<11} {off / 1e6:>13.2f} {dlv / 1e6:>15.3f} {ratio:>14.3f}  {'PASS' if ok else 'FAIL'}{why}")
            except Exception as ex:  # noqa: BLE001
                ok = False
                print(f"{name:<11} {'-':>13} {'-':>15} {'-':>14}  FAIL ({type(ex).__name__}: {ex})")
            if not ok:
                failed.append(name)
    finally:
        sh("pkill -9 hping3")
        sh(f"{BASE}/setup_topology.sh cleanup")
    print("\nALL PASS" if not failed else f"\nFAILED: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
