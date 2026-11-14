#!/usr/bin/env python3
"""Workload 11 - industry benchmarks on the 100-tenant testbed (config B by default).

Each app replicates the methodology an existing solution publishes, so results can be set beside theirs and,
more importantly, compared on the same machine with a real existing solution (envoy_sidecar arm):

  istio     Istio data-plane benchmark: fortio, HTTP/1.1, 1 KB payload, 1000 RPS, 16 (classic) or 4 (newer
            profile) connections, ONE client -> server pair. Reported as latency added over the bare bridge.
  netperf   Cilium CNI benchmark: netperf TCP_RR (1-byte request/response), TCP_CRR (new connection per
            transaction), TCP_STREAM (bulk), with 1 or 8 parallel client/server pairs.
  memcached Isolation-literature style latency-critical app: memcached + memtier_benchmark, fixed-QPS sweep,
            p99/p99.9 per offered load (needs memtier_benchmark: set MEMTIER_BIN).

Conditions: idle (only the measured app) or noisy (5 attackers flood servers 1..5; the measured servers are
victims 1..k, so they are targeted). Every arm runs on the same topology; envoy arms add a sidecar per measured
tenant (client -> its Envoy -> server's Envoy -> server). App servers run directly in the tenant namespaces.

  sudo TENANTS_FILE=tenants/scale100.json QOS_CALIBRATION=../../calibration/scale100_rps.json \\
       python3 run_bench.py --app istio --reps 10 --tenant-cpus 1-5 --attacker-cpus 6-7 --rps
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "shared"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "10_scale"))
sys.path.insert(0, BASE_DIR)
import run_matrix as rm                                   # noqa: E402  (shared helpers)
import scale_lib as sl                                    # noqa: E402
import arms                                               # noqa: E402
import containers                                         # noqa: E402
import tenants                                            # noqa: E402
import topology                                           # noqa: E402
import envoy_sidecar                                      # noqa: E402
from workload_lib import log, write_env_json              # noqa: E402


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def wait_port(ns, port, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if sh(["ip", "netns", "exec", ns, "bash", "-c", f"exec 3<>/dev/tcp/127.0.0.1/{port}"]).returncode == 0:
            return True
        time.sleep(0.2)
    return False


# ============================================================================ apps
class IstioApp:
    """Istio's published data-plane profile (fortio)."""
    name = "istio"
    # (connections, qps, extra fortio flags). fortio fires all connections in the same instant by default, which
    # is what Istio's published profile does (requests queue behind the proxy's 2 workers); "-uniform" spreads
    # them evenly - the best case for a proxy, cf. the "jitter" variant in Istio's documentation.
    variants = {"c16_q1000": (16, 1000, ()), "c4_q1000": (4, 1000, ()), "c16_q1000_uniform": (16, 1000, ("-uniform",))}
    ports = [(15001, 15006, 8080, "http")]
    metrics = ["p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps", "errors"]

    def pairs(self, topo, variant):
        s = topo.servers()
        return [(s[9], s[0])]                        # client victim10 -> server victim1 (flooded when noisy)

    def all_servers(self, topo):
        return [topo.servers()[0]]

    def start_servers(self, topo, cpus, raw):
        procs = []
        for t in self.all_servers(topo):
            log_f = open(os.path.join(raw, f"server_{t.name}.log"), "w")
            procs.append(subprocess.Popen(["ip", "netns", "exec", t.ns, "taskset", "-c", cpus, "fortio", "server",
                                           "-http-port", "8080", "-grpc-port", "disabled", "-redirect-port", "disabled",
                                           "-tcp-port", "disabled", "-udp-port", "disabled"], stdout=log_f, stderr=log_f))
            if not wait_port(t.ns, 8080):
                raise RuntimeError(f"fortio server in {t.ns} did not start")
        return procs

    def run(self, pairs, variant, via_envoy, raw, tid, dur, cpus):
        conns, qps, extra = self.variants[variant]
        (cli, srv), = pairs
        url = "http://127.0.0.1:15001/echo" if via_envoy else f"http://{srv.ip}:8080/echo"
        jp = os.path.join(raw, f"fortio_{tid}.json")
        r = sh(["ip", "netns", "exec", cli.ns, "taskset", "-c", cpus, "fortio", "load", "-c", str(conns), "-qps", str(qps),
                "-t", f"{dur}s", "-payload-size", "1024", "-r", "0.0001", "-p", "50,90,99,99.9",
                "-allow-initial-errors", *extra, "-json", jp, url], timeout=dur + 60)
        m = sl.merge_fortio([jp])
        return {"p50_ms": m["p50_ms"], "p90_ms": m["p90_ms"], "p99_ms": m["p99_ms"], "p999_ms": m["p999_ms"],
                "actual_qps": m["actual_qps"], "errors": m["errors"], "requests": m["requests"],
                "ok": r.returncode == 0 and m["requests"] > 0}


class NetperfApp:
    """Cilium's CNI benchmark tests (netperf)."""
    name = "netperf"
    variants = {"rr1": ("TCP_RR", 1), "rr8": ("TCP_RR", 8), "crr1": ("TCP_CRR", 1), "crr8": ("TCP_CRR", 8),
                "stream1": ("TCP_STREAM", 1), "stream8": ("TCP_STREAM", 8)}
    ports = [(12865, 15865, 12865, "tcp"), (12866, 15866, 12866, "tcp")]   # control + fixed data port
    metrics = ["throughput", "p50_us", "p90_us", "p99_us", "mean_us", "p99_us_worst"]
    RR_SEL = "THROUGHPUT,THROUGHPUT_UNITS,MEAN_LATENCY,P50_LATENCY,P90_LATENCY,P99_LATENCY"

    def pairs(self, topo, variant):
        s = topo.servers()
        k = self.variants[variant][1]
        return [(s[10 + i], s[i]) for i in range(k)]   # clients victim11.. -> servers victim1..k

    def all_servers(self, topo):
        return topo.servers()[:8]

    def start_servers(self, topo, cpus, raw):
        procs = []
        for t in self.all_servers(topo):
            log_f = open(os.path.join(raw, f"server_{t.name}.log"), "w")
            procs.append(subprocess.Popen(["ip", "netns", "exec", t.ns, "taskset", "-c", cpus, "netserver", "-D",
                                           "-p", "12865"], stdout=log_f, stderr=log_f))
            if not wait_port(t.ns, 12865):
                raise RuntimeError(f"netserver in {t.ns} did not start")
        return procs

    def run(self, pairs, variant, via_envoy, raw, tid, dur, cpus):
        test, _k = self.variants[variant]
        procs = []
        for cli, srv in pairs:
            host = "127.0.0.1" if via_envoy else srv.ip
            extra = ["-r", "1,1", "-o", self.RR_SEL] if test != "TCP_STREAM" else ["-m", "16384", "-o",
                                                                                  "THROUGHPUT,THROUGHPUT_UNITS"]
            cmd = ["ip", "netns", "exec", cli.ns, "taskset", "-c", cpus, "netperf", "-H", host, "-p", "12865", "-t", test,
                   "-l", str(dur), "-P", "0", "--", "-P", ",12866", *extra]
            procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
        per = []
        for (cli, _s), p in zip(pairs, procs):
            out, _ = p.communicate(timeout=dur + 60)
            with open(os.path.join(raw, f"netperf_{tid}_{cli.name}.txt"), "w") as f:
                f.write(out)
            vals = None
            for ln in reversed(out.strip().splitlines()):
                parts = [x.strip() for x in ln.split(",")]
                try:
                    float(parts[0])
                    vals = parts
                    break
                except (ValueError, IndexError):
                    continue
            if p.returncode == 0 and vals:
                per.append(vals)
        if len(per) < len(pairs):
            return {"ok": False}
        thr = sum(float(v[0]) for v in per)
        res = {"throughput": round(thr, 2), "throughput_units": per[0][1], "ok": True}
        if test != "TCP_STREAM":
            med = lambda xs: sorted(xs)[len(xs) // 2]   # noqa: E731
            res.update({"mean_us": round(med([float(v[2]) for v in per]), 2),
                        "p50_us": med([float(v[3]) for v in per]), "p90_us": med([float(v[4]) for v in per]),
                        "p99_us": med([float(v[5]) for v in per]), "p99_us_worst": max(float(v[5]) for v in per)})
        return res


class MemcachedApp:
    """memcached + memtier_benchmark fixed-QPS sweep (latency-load curve)."""
    name = "memcached"
    # the 8 pairs saturate near 90k requests/s on this host: sweep below that, plus q300k = saturation point
    variants = {f"q{q}k": q * 1000 for q in (10, 20, 40, 60, 80, 300)}
    ports = [(11211, 21211, 11211, "tcp")]
    metrics = ["p50_ms", "p99_ms", "p999_ms", "ops_per_sec"]
    THREADS, CONNS = 2, 4                     # per client pair

    def __init__(self):
        self.bin = os.environ.get("MEMTIER_BIN", "/usr/local/bin/memtier_benchmark")
        if not os.path.exists(self.bin):
            raise SystemExit(f"memtier_benchmark not found at {self.bin} (set MEMTIER_BIN)")

    def pairs(self, topo, variant):
        s = topo.servers()
        return [(s[10 + i], s[i]) for i in range(8)]

    def all_servers(self, topo):
        return topo.servers()[:8]

    def start_servers(self, topo, cpus, raw):
        procs = []
        for t in self.all_servers(topo):
            log_f = open(os.path.join(raw, f"server_{t.name}.log"), "w")
            procs.append(subprocess.Popen(["ip", "netns", "exec", t.ns, "taskset", "-c", cpus, "memcached", "-u", "nobody",
                                           "-l", "0.0.0.0", "-p", "11211", "-U", "0", "-t", "2", "-m", "256"],
                                          stdout=log_f, stderr=log_f))
            if not wait_port(t.ns, 11211):
                raise RuntimeError(f"memcached in {t.ns} did not start")
        for t in self.all_servers(topo):     # preload the key space so GETs hit
            sh(["ip", "netns", "exec", t.ns, self.bin, "-s", "127.0.0.1", "-p", "11211", "--protocol=memcache_text",
                "--ratio=1:0", "--key-pattern=P:P", "--key-maximum=100000", "--data-size=64", "-n", "allkeys",
                "-t", "1", "-c", "1", "--hide-histogram"], timeout=120)
        return procs

    def run(self, pairs, variant, via_envoy, raw, tid, dur, cpus):
        total = self.variants[variant]
        per_conn = max(1, int(total / len(pairs) / (self.THREADS * self.CONNS)))
        procs, files = [], []
        for cli, srv in pairs:
            host, port = ("127.0.0.1", 11211) if via_envoy else (srv.ip, 11211)
            jf = os.path.join(raw, f"memtier_{tid}_{cli.name}.json")
            files.append(jf)
            procs.append(subprocess.Popen(
                ["ip", "netns", "exec", cli.ns, "taskset", "-c", cpus, self.bin, "-s", host, "-p", str(port),
                 "--protocol=memcache_text", "--ratio=1:10", "--key-maximum=100000", "--data-size=64",
                 f"--test-time={dur}", "-t", str(self.THREADS), "-c", str(self.CONNS), f"--rate-limiting={per_conn}",
                 "--print-percentiles=50,99,99.9", "--hide-histogram", f"--json-out-file={jf}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for p in procs:
            p.wait(timeout=dur + 60)
        ops, p50, p99, p999 = 0.0, [], [], []
        for jf in files:
            try:
                tot = json.load(open(jf))["ALL STATS"]["Totals"]
            except (OSError, ValueError, KeyError):
                return {"ok": False}
            ops += float(tot.get("Ops/sec", 0))
            pl = tot.get("Percentile Latencies", {})
            p50.append(float(pl.get("p50.00", "nan")))
            p99.append(float(pl.get("p99.00", "nan")))
            p999.append(float(pl.get("p99.90", "nan")))
        med = lambda xs: sorted(xs)[len(xs) // 2]   # noqa: E731
        return {"ops_per_sec": round(ops, 1), "p50_ms": med(p50), "p99_ms": med(p99), "p999_ms": med(p999),
                "p99_ms_worst": max(p99), "ok": True}


APPS = {"istio": IstioApp, "netperf": NetperfApp, "memcached": MemcachedApp}
DEFAULT_ARMS = {"istio": arms.INDUSTRY_ARMS,
                "netperf": ["bare", "envoy_sidecar", "sidecarless", "qos_open", "qos_static", "qos_auto_port"],
                "memcached": ["bare", "qos_open", "qos_static", "qos_auto_port"]}


# ============================================================================ trial
def run_trial(app, topo, arch, cond, variant, rep, raw_root, a):
    tid = f"{arch}_{cond}_{variant}_rep_{rep}"
    raw = os.path.join(raw_root, tid)
    os.makedirs(raw, exist_ok=True)
    log(f"--- trial {tid} ---")
    spec = arms.ARMS[arms.resolve(arch)]
    t0 = time.time()
    arms.attach(arch, BASE_DIR)
    attach_s = round(time.time() - t0, 2)
    ctrl = arms.start_controller(arch, os.path.join(raw, "controller.jsonl"), BASE_DIR, cpus=a.controller_cpus)
    pairs = app.pairs(topo, variant)
    side = None
    flood_procs, flood_pps = [], {}
    try:
        if spec.get("sidecar") == "envoy":
            side = envoy_sidecar.Sidecars(pairs, app.ports, cpus=a.tenant_cpus, mtls=bool(spec.get("mtls")), log_dir=raw)
            side.start()
        if cond == "noisy":
            servers = topo.servers()
            specs = [{"ns": t.ns, "dev": t.br_veth, "args": "--flood -d 1000", "target": servers[j].ip, "ip": t.ip}
                     for j, t in enumerate(topo.attackers())]
            flood_procs, flood_pps, _ = sl.start_verified_floods(specs, raw, tid, cpus=a.attacker_cpus or a.tenant_cpus,
                                                                 port=8080, log=log)
        time.sleep(a.warmup)
        cpu = sl.CpuSampler().start()
        res = app.run(pairs, variant, side is not None, raw, tid, a.duration, a.tenant_cpus)
        util = cpu.stop()
    finally:
        sl.stop_floods(flood_procs)
        if side:
            side.stop()
        if ctrl is not None:
            ctrl.kill()
            ctrl.wait(5)
    flood_ok = cond == "idle" or (len(flood_pps) == len(topo.attackers()) and all(v >= 300 for v in flood_pps.values()))
    row = {"app": app.name, "arch": arch, "condition": cond, "variant": variant, "rep": rep, "attach_s": attach_s,
           "flood_ok": flood_ok, "app_ok": res.get("ok", False),
           "cpu_busy_tenants": rm_cpu(util, a.tenant_cpus), **{k: v for k, v in res.items() if k != "ok"}}
    with open(os.path.join(raw, "summary.json"), "w") as f:
        json.dump({**row, "cpu": util}, f, indent=1)
    log("    " + " ".join(f"{k}={row.get(k)}" for k in app.metrics if k in row) + f" flood_ok={flood_ok} ok={row['app_ok']}")
    return row


def rm_cpu(util, cpus):
    names = [f"cpu{c}" for c in tenants._expand_cpus(cpus)]
    vals = [util[c]["busy"] for c in names if c in util]
    return round(sum(vals) / len(vals), 4) if vals else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True, choices=list(APPS))
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--architectures", nargs="+", default=None)
    ap.add_argument("--conditions", nargs="+", default=["idle", "noisy"], choices=["idle", "noisy"])
    ap.add_argument("--variants", nargs="+", default=None)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--warmup", type=float, default=3.0)
    ap.add_argument("--tenant-cpus", default="1-7")
    ap.add_argument("--controller-cpus", default="0")
    ap.add_argument("--attacker-cpus", default=None)
    ap.add_argument("--rps", action="store_true")
    ap.add_argument("--seed", type=int, default=21)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root")
    app = APPS[a.app]()
    archs = a.architectures or DEFAULT_ARMS[a.app]
    variants = a.variants or list(app.variants)
    for x in archs:
        arms.resolve(x)
        if arms.ARMS[arms.resolve(x)].get("sidecar"):
            envoy_sidecar.envoy_bin()                      # fail now, not after an hour
    for v in variants:
        if v not in app.variants:
            sys.exit(f"unknown variant {v}; known: {list(app.variants)}")
    os.sched_setaffinity(0, set(tenants._expand_cpus(str(a.controller_cpus))))
    topo = tenants.load()
    run_dir = a.out or os.path.join(HERE, "results", f"{a.app}_N{a.reps}_{topo.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    raw_root = os.path.join(run_dir, "raw")
    os.makedirs(raw_root, exist_ok=True)
    write_env_json(run_dir, archs)
    with open(os.path.join(run_dir, "config.json"), "w") as f:
        json.dump({**vars(a), "architectures": archs, "variants": variants, "tenants_file": topo.path,
                   "calibration": os.environ.get("QOS_CALIBRATION")}, f, indent=2)
    trials = [(x, c, v, r) for x in archs for c in a.conditions for v in variants for r in range(1, a.reps + 1)]
    random.Random(a.seed).shuffle(trials)
    log(f"workload 11 ({a.app}): {len(trials)} trials -> {run_dir}")
    containers.stop()                                      # app servers run directly in the namespaces
    secs = topology.up(topo)
    if a.rps:
        log(f"RPS mask {rm.set_rps(topo, a.tenant_cpus)}")
    srv_procs = app.start_servers(topo, a.tenant_cpus, raw_root)
    log(f"topology {secs:.1f}s, {len(srv_procs)} {a.app} servers up")
    rows, failures, consecutive = [], [], 0
    jsonl = os.path.join(run_dir, "results.jsonl")
    try:
        for i, (x, c, v, r) in enumerate(trials, 1):
            log(f"[Trial {i}/{len(trials)}] {x} | {c} | {v} | rep {r}")
            try:
                row = run_trial(app, topo, x, c, v, r, raw_root, a)
            except Exception as ex:  # noqa: BLE001
                import traceback
                log(f"TRIAL FAILED {x}/{c}/{v}/{r}: {type(ex).__name__}: {ex}")
                failures.append({"arch": x, "condition": c, "variant": v, "rep": r, "error": str(ex),
                                 "tb": traceback.format_exc()})
                json.dump(failures, open(os.path.join(run_dir, "failures.json"), "w"), indent=1)
                rm.cleanup_after_failure()
                subprocess.run(["pkill", "-9", "-f", "[e]nvoy -c"], capture_output=True)
                consecutive += 1
                if consecutive >= 3:
                    raise
                continue
            consecutive = 0
            rows.append(row)
            with open(jsonl, "a") as f:
                f.write(json.dumps(row) + "\n")
    finally:
        sl.stop_floods([])
        for p in srv_procs:
            p.kill()
        topology.down(topo)
    import csv
    cols = sorted({k for r in rows for k in r})
    with open(os.path.join(run_dir, "results_summary_raw.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"workload 11 ({a.app}) complete: {run_dir}")


if __name__ == "__main__":
    main()
