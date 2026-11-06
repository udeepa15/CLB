#!/usr/bin/env python3
"""smoke_test.py - verify every arm actually enforces before burning hours on a matrix.

For each arm: build topology, attach, start controller, flood from ns_attacker for a few
seconds with large UDP packets (so the 5 MB/s cap is clearly exceeded), then assert
  * the attacker was rate limited (packets_dropped > 0, or tc police/tbf counters > 0)
  * victim-to-victim traffic still passes (ping from ns_victim2 to 10.0.0.10)
  * the controller (if any) is still alive

Usage:  sudo python3 smoke_test.py [--arms qos_tiered qos_dynamic_fixed ...] [--flood-sec 4]
Exit code is non-zero if any arm fails, so it can gate a matrix run:
        sudo python3 smoke_test.py && sudo bash workloads/run_workload_01.sh 10
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import arms  # noqa: E402

BPFTOOL = "/usr/sbin/bpftool"
ATT = "10.0.0.20"
OFFSET = {"qos_tiered": 48, "qos_dynamic": 40}  # byte offset of packets_dropped in the value struct


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def _ip_key(k):
    if isinstance(k, list):
        return ".".join(str(int(x, 16)) for x in k)
    if isinstance(k, int):
        return ".".join(str(b) for b in k.to_bytes(4, "little"))
    return None


def auto_drops(spec):
    """Auto arms: sum packets_dropped over every entry of the arm's limiter map (who was capped is
    checked separately from the controller log)."""
    pin = "/sys/fs/bpf/tc/globals/" + ("port_rate_limit_map" if spec["auto_key"] == "port" else "percpu_rate_limit_map")
    out = subprocess.run([BPFTOOL, "map", "dump", "pinned", pin, "-j"], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr)
    total = 0
    for e in json.loads(out.stdout or "[]"):
        f = e.get("formatted", e)
        for v in (f.get("values") or []):
            v = v["value"]
            total += int(v["packets_dropped"]) if isinstance(v, dict) else int.from_bytes(
                bytes(int(x, 16) for x in v[40:48]), "little")
    return total


def check_auto_log(spec, log_path):
    """Controller log must show the attacker (and only the attacker) flagged heavy."""
    att = "veth-att-br" if spec["auto_key"] == "port" else ATT
    flagged = set()
    try:
        for line in open(log_path):
            flagged |= set(json.loads(line).get("heavy", []))
    except FileNotFoundError:
        return ["controller log missing"]
    probs = []
    if att not in flagged:
        probs.append(f"attacker {att} never flagged heavy (flagged={sorted(flagged)})")
    wrong = flagged - {att}
    if wrong:
        probs.append(f"non-attackers flagged heavy: {sorted(wrong)}")
    return probs


def attacker_drops(arm):
    """Total packets dropped for the attacker, from the arm's tenant map (or tc counters)."""
    spec = arms.ARMS[arms.resolve(arm)]
    if spec.get("auto_key"):
        return auto_drops(spec)
    if spec.get("tc"):
        out = sh("tc -s filter show dev veth-att-br ingress; ip netns exec ns_attacker tc -s qdisc show dev veth-attacker").stdout
        import re
        # tc police drops; tbf throttles via backpressure and reports overlimits, not drops
        return sum(int(x) for x in re.findall(r"dropped (\d+)", out)) + \
               sum(int(x) for x in re.findall(r"overlimits (\d+)", out))
    tiered = spec["attach"] == "qos_tiered"
    pin = "/sys/fs/bpf/tc/globals/" + ("tenant_qos_map" if tiered else "percpu_rate_limit_map")
    out = subprocess.run([BPFTOOL, "map", "dump", "pinned", pin, "-j"], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr)
    off = OFFSET["qos_tiered" if tiered else "qos_dynamic"]
    total = 0
    for e in json.loads(out.stdout or "[]"):
        f = e.get("formatted", e)
        if _ip_key(e.get("key", f.get("key"))) != ATT:
            continue
        vals = f.get("values") or [{"value": f.get("value")}]
        for v in vals:
            v = v["value"]
            if isinstance(v, dict):
                total += int(v["packets_dropped"])
            else:
                total += int.from_bytes(bytes(int(x, 16) for x in v[off:off + 8]), "little")
    return total


def run_arm(arm, flood_sec):
    problems = []
    sh(f"{BASE}/setup_topology.sh cleanup")
    r = sh(f"{BASE}/setup_topology.sh")
    if r.returncode != 0:
        return [f"topology setup failed: {r.stderr[-300:]}"]
    ctrl = None
    try:
        arms.attach(arm, BASE)  # raises if seeding/verification fails
        with tempfile.TemporaryDirectory() as td:
            ctrl_log = os.path.join(td, "ctrl.jsonl")
            ctrl = arms.start_controller(arm, ctrl_log, BASE)
            # a UDP sink in victim1 so the flood is not just ICMP-unreachable noise
            sink = subprocess.Popen(["ip", "netns", "exec", "ns_victim1", "python3", "-c",
                "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('0.0.0.0',8080))\n"
                "while True: s.recvfrom(2048)"])
            time.sleep(0.5)
            fl = subprocess.Popen(f"ip netns exec ns_attacker hping3 --udp -p 8080 -d 1000 --flood 10.0.0.10",
                                  shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(flood_sec)
            ping = sh("ip netns exec ns_victim2 ping -c 3 -W 2 10.0.0.10")  # during flood
            # Controllers rewrite entries every tick (which zeroes packets_dropped), so read the counter
            # WHILE the flood is still running and take the max of several samples.
            drops = 0
            for _ in range(6):
                if arms.ARMS[arms.resolve(arm)].get("no_limit"):
                    break
                drops = max(drops, attacker_drops(arm))
                time.sleep(0.15)
            fl.kill(); sh("pkill -9 hping3"); sink.kill()
            if ping.returncode != 0:
                problems.append("victim->victim ping failed during flood (victim traffic blocked?)")
            if arms.ARMS[arms.resolve(arm)].get("no_limit"):
                drops = None  # unprotected baseline: no enforcement expected
            elif drops <= 0:
                problems.append("no enforcement observed (drops/overlimits == 0): limiter not enforcing (or seed/rate wrong)")
            print(f"    attacker drops={'n/a (no limiter by design)' if drops is None else drops}")
            if ctrl is not None and ctrl.poll() is not None:
                problems.append(f"controller died (rc={ctrl.returncode})")
            if arms.ARMS[arms.resolve(arm)].get("auto_key"):
                problems += check_auto_log(arms.ARMS[arms.resolve(arm)], ctrl_log)
    except Exception as ex:  # noqa: BLE001
        problems.append(f"{type(ex).__name__}: {ex}")
    finally:
        if ctrl is not None and ctrl.poll() is None:
            ctrl.kill()
        sh("pkill -9 hping3")
        sh(f"{BASE}/setup_topology.sh cleanup")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="+", default=list(dict.fromkeys(arms.DEFAULT_ARMS + arms.FORGET_ARMS + arms.AUTO_ARMS + arms.BASELINE_ARMS)))
    ap.add_argument("--flood-sec", type=float, default=4.0)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root: sudo python3 smoke_test.py")
    failed = {}
    for arm in a.arms:
        print(f"[smoke] {arm}")
        p = run_arm(arm, a.flood_sec)
        print("    " + ("PASS" if not p else "FAIL: " + "; ".join(p)))
        if p:
            failed[arm] = p
    print("\nSUMMARY:", "all arms passed" if not failed else f"{len(failed)} failed: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
