#!/usr/bin/env python3
"""smoke_test.py - verify every arm actually enforces before burning hours on a matrix.

For each arm: build topology, attach, start controller, flood from the first attacker for a few
seconds with large UDP packets (so the 5 MB/s cap is clearly exceeded), then assert
  * the attacker was rate limited (packets_dropped > 0, or tc police/tbf counters > 0)
  * victim-to-victim traffic still passes (ping from the second victim to the first)
Tenants come from the tenants file ($TENANTS_FILE, default tenants/default.json).
  * the controller (if any) is still alive

Usage:  sudo python3 smoke_test.py [--arms qos_tiered qos_dynamic_fixed ...] [--flood-sec 4]
Exit code is non-zero if any arm fails, so it can gate a matrix run:
        sudo python3 smoke_test.py && sudo bash workloads/run_workload_01.sh 10
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import arms  # noqa: E402
import bpfmap  # noqa: E402

import tenants  # noqa: E402
TOPO = tenants.load()                      # $TENANTS_FILE or tenants/default.json
ATT_T = TOPO.attackers()[0]                # the smoke test floods from the first attacker only
VIC1, VIC2 = TOPO.victims()[0], TOPO.victims()[1]
ATT = ATT_T.ip


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def _sum_dropped(map_name, key=None):
    """packets_dropped summed over CPUs from a kernel-owned rate_counters map (cumulative: the
    controller never resets it). key=None sums every entry."""
    m = bpfmap.BPFMap("/sys/fs/bpf/tc/globals/" + map_name)
    if key is not None:
        v = m.lookup(key)
        return 0 if v is None else bpfmap.sum_u64(v, 8)
    return sum(bpfmap.sum_u64(v, 8) for _, v in m.items())


def auto_drops(spec):
    """Auto arms: drops over every entry of the arm's counter map (who was capped is checked
    separately from the controller log)."""
    return _sum_dropped("port_cnt_map" if spec["auto_key"] == "port" else "rate_cnt_map")


def check_auto_log(spec, log_path):
    """Controller log must show the attacker (and only the attacker) flagged heavy."""
    att = ATT_T.br_veth if spec["auto_key"] == "port" else ATT
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
        out = sh(f"tc -s filter show dev {ATT_T.br_veth} ingress; ip netns exec {ATT_T.ns} tc -s qdisc show dev {ATT_T.ns_veth}").stdout
        import re
        # tc police drops; tbf throttles via backpressure and reports overlimits, not drops
        return sum(int(x) for x in re.findall(r"dropped (\d+)", out)) + \
               sum(int(x) for x in re.findall(r"overlimits (\d+)", out))
    if spec["attach"] == "qos_tiered":
        v = bpfmap.BPFMap("/sys/fs/bpf/tc/globals/tenant_qos_map").lookup(bpfmap.ip_key(ATT))
        return 0 if v is None else struct.unpack_from("=Q", v, 48)[0]   # packets_dropped @48 (56 B struct)
    return _sum_dropped("rate_cnt_map", bpfmap.ip_key(ATT))


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
            sink = subprocess.Popen(["ip", "netns", "exec", VIC1.ns, "python3", "-c",
                "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('0.0.0.0',8080))\n"
                "while True: s.recvfrom(2048)"])
            time.sleep(0.5)
            fl = subprocess.Popen(f"ip netns exec {ATT_T.ns} hping3 -n --udp -p 8080 -d 1000 --flood {VIC1.ip}",
                                  shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(flood_sec)
            ping = sh(f"ip netns exec {VIC2.ns} ping -c 3 -W 2 {VIC1.ip}")  # during flood
            # Counters are cumulative now (kernel-owned), but tc counters can still be reset by a re-attach;
            # read WHILE the flood is still running and keep the max of several samples.
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
