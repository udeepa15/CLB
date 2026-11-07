#!/usr/bin/env python3
"""scale_lib.py - helpers for many-tenant workloads (workload 10 and later).

  start_verified_floods : launch hping3 floods from attacker namespaces IN PARALLEL, verify each one is
                          alive and delivering (rx_packets on its host-side veth), retry failures
  start_clients         : one fortio client per victim tenant, inside the tenant's own namespace, so
                          victim requests cross tenant ports exactly like east-west mesh traffic
  merge_fortio          : pooled percentiles over many fortio JSON files (histogram mixture), per-client P99
  CpuSampler            : per-CPU utilisation between two points in time (/proc/stat)
  port_rx_packets       : packets entering the bridge from tenants (sum of host-side veth rx counters)

fortio's default histogram resolution is 1 ms (-r 0.001): P99 values were interpolated inside 1 ms buckets.
Clients here use -r 0.0001 (0.1 ms buckets below 1.2 ms, then 1.4/1.6/1.8/2.0/2.5/3 ms ...).
"""
import json
import os
import subprocess
import time

FORTIO_RESOLUTION = 0.0001


def rx_packets(dev):
    try:
        with open(f"/sys/class/net/{dev}/statistics/rx_packets") as f:
            return int(f.read())
    except OSError:
        return 0


def port_rx_packets(devs):
    return sum(rx_packets(d) for d in devs)


# ---------------------------------------------------------------- floods
def _launch(spec, raw_dir, trial_id, attempt, cpus, cpu_quota, port):
    logf = open(os.path.join(raw_dir, f"hping_{trial_id}_{spec['ns']}_try{attempt}.log"), "w")
    wrap = f"systemd-run --scope --quiet -p CPUQuota={cpu_quota}% " if cpu_quota else ""
    pin = f"taskset -c {cpus} " if cpus else ""
    cmd = (f"exec {wrap}ip netns exec {spec['ns']} {pin}hping3 -n --udp -p {port} {spec['args']} {spec['target']}")
    return subprocess.Popen(cmd, shell=True, stdout=logf, stderr=subprocess.STDOUT)


def start_verified_floods(specs, raw_dir, trial_id, attempts=3, min_pps=300, cpus=None, cpu_quota=None, port=8080,
                          log=print):
    """specs: [{"ns", "dev" (host-side veth of the attacker), "args" (hping3 args), "target" (IP)}].

    All floods start together; after 1 s each one's rate is measured on its own host-side veth over 0.5 s.
    A flood that is dead or below min_pps is killed and relaunched (hping3 occasionally crashes at
    start-up). Returns (procs, {ns: verified_pps}, launch_epoch). A trial whose floods are not all
    verified must be flagged (flood_ok = False) and excluded from the analysis."""
    procs, verified, t_launch = {}, {}, time.time()
    pending = list(specs)
    for attempt in range(1, attempts + 1):
        if not pending:
            break
        for s in pending:
            procs[s["ns"]] = _launch(s, raw_dir, trial_id, attempt, cpus, cpu_quota, port)
        time.sleep(1.0)
        r0 = {s["ns"]: rx_packets(s["dev"]) for s in pending}
        time.sleep(0.5)
        failed = []
        for s in pending:
            pps = (rx_packets(s["dev"]) - r0[s["ns"]]) / 0.5
            p = procs[s["ns"]]
            if p.poll() is None and pps >= min_pps:
                verified[s["ns"]] = pps
            else:
                log(f"WARNING: flood {s['ns']} not delivering (alive={p.poll() is None}, {pps:.0f} pps), "
                    f"attempt {attempt}/{attempts}")
                p.kill()
                # ip netns exec / taskset exec in place, so the argv is just "hping3 ..." - kill by namespace
                subprocess.run(f"ip netns pids {s['ns']} | xargs -r kill -9", shell=True)
                failed.append(s)
        pending = failed
    for s in pending:
        verified[s["ns"]] = 0.0
    return [p for ns, p in procs.items() if verified.get(ns, 0) > 0], verified, t_launch


def stop_floods(procs):
    for p in procs:
        p.kill()
    subprocess.run("pkill -9 hping3 2>/dev/null", shell=True)


# ---------------------------------------------------------------- clients
def start_clients(pairs, raw_dir, trial_id, qps, duration, conns=1, cpus=None, port=8080):
    """pairs: [(client_tenant, server_tenant)]. One fortio process per client tenant, run inside the
    client's namespace. Returns [(Popen, json_path, client, server)]."""
    out = []
    pin = ["taskset", "-c", cpus] if cpus else []
    for c, s in pairs:
        jp = os.path.join(raw_dir, f"fortio_{trial_id}_{c.name}.json")
        cmd = ["ip", "netns", "exec", c.ns, *pin, "fortio", "load", "-qps", str(qps), "-c", str(conns),
               "-t", f"{duration}s", "-r", str(FORTIO_RESOLUTION), "-p", "50,90,99,99.9",
               "-allow-initial-errors", "-json", jp, f"http://{s.ip}:{port}/"]
        out.append((subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), jp, c, s))
    return out


def wait_clients(clients, timeout):
    end = time.time() + timeout
    for p, *_ in clients:
        try:
            p.wait(timeout=max(0.1, end - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()


# ---------------------------------------------------------------- histogram mixture
def _load(jp):
    try:
        with open(jp) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _percentile(buckets, total, q):
    """buckets: [(start, end, count)], counts spread uniformly inside each bucket (fortio's own model).
    Returns x with  sum_b count_b * frac_b(x) = q/100 * total  (bisection on the mixture CDF)."""
    target = q / 100.0 * total
    lo, hi = min(b[0] for b in buckets), max(b[1] for b in buckets)

    def cdf(x):
        acc = 0.0
        for s, e, c in buckets:
            if x >= e:
                acc += c
            elif x > s:
                acc += c * (x - s) / (e - s) if e > s else c
        return acc
    for _ in range(60):
        mid = (lo + hi) / 2
        if cdf(mid) < target:
            lo = mid
        else:
            hi = mid
    return hi


def merge_fortio(json_paths, percentiles=(50, 90, 99, 99.9)):
    """Pool every client's latency histogram. Returns dict with pooled percentiles (ms), request and
    error counts, achieved/requested QPS, and the per-client P99 list (ms) for fairness statistics."""
    buckets, per_client, total, errors, qps, req_qps, missing = [], [], 0, 0, 0.0, 0.0, 0
    for jp in json_paths:
        d = _load(jp)
        if not d or "DurationHistogram" not in d:
            missing += 1
            continue
        h = d["DurationHistogram"]
        for b in h.get("Data", []):
            buckets.append((float(b["Start"]), float(b["End"]), int(b["Count"])))
        total += int(h.get("Count", 0))
        errors += sum(int(n) for code, n in (d.get("RetCodes") or {}).items() if str(code) != "200")
        qps += float(d.get("ActualQPS", 0.0))
        req_qps += float(d.get("RequestedQPS", 0) or 0)
        p99 = [p["Value"] for p in h.get("Percentiles", []) if float(p["Percentile"]) == 99.0]
        if p99:
            per_client.append(p99[0] * 1000.0)
    res = {"n_clients": len(per_client), "missing_clients": missing, "requests": total, "errors": errors,
           "actual_qps": round(qps, 1), "requested_qps": round(req_qps, 1), "per_client_p99_ms": per_client}
    for q in percentiles:
        key = "p" + str(q).replace(".", "")
        res[f"{key}_ms"] = round(_percentile(buckets, total, q) * 1000.0, 4) if total else None
    return res


# ---------------------------------------------------------------- CPU
class CpuSampler:
    """Per-CPU busy fraction between start() and stop() (from /proc/stat jiffies)."""

    @staticmethod
    def _read():
        out = {}
        with open("/proc/stat") as f:
            for ln in f:
                if ln.startswith("cpu") and ln[3].isdigit():
                    parts = ln.split()
                    vals = list(map(int, parts[1:9]))
                    idle = vals[3] + vals[4]                  # idle + iowait
                    out[parts[0]] = (sum(vals), idle, vals[6])  # total, idle, softirq
        return out

    def start(self):
        self.a = self._read()
        return self

    def stop(self):
        b = self._read()
        res = {}
        for cpu, (tot, idle, sirq) in b.items():
            t0, i0, s0 = self.a.get(cpu, (tot, idle, sirq))
            dt = max(1, tot - t0)
            res[cpu] = {"busy": round(1 - (idle - i0) / dt, 4), "softirq": round((sirq - s0) / dt, 4)}
        return res


# ---------------------------------------------------------------- time-varying attacks
import threading  # noqa: E402


def expected_pps(args):
    """Nominal hping3 rate for '-i uN' (1e6/N pps); None for --flood."""
    for tok in args.split():
        if tok.startswith("u") and tok[1:].isdigit():
            return 1e6 / int(tok[1:])
    return None


class PatternDriver(threading.Thread):
    """Drive every attacker through the same schedule of hping3 rates, in sync.

    schedule: [(seconds, hping3 args)]; repeated until stop() if repeat, else run once and hold the last
    step. lead_s extends the first step (warm-up before the measurement starts).
    Each step kills ONLY this driver's attackers' hping3 (by namespace; the old single-attacker threads
    ran `pkill hping3`, which kills every attacker) and relaunches them with the new rate. The offered
    rate of every attacker in every step is measured on its host-side veth (first 0.3 s skipped), so a
    flood that crashed or never started is detected: steps = [{t, args, pps: {ns: pps}, ok}]."""

    def __init__(self, specs, schedule, raw_dir, tag, cpus=None, port=8080, repeat=False, lead_s=0.0, log=print):
        super().__init__(daemon=True)
        self.specs, self.schedule, self.repeat, self.lead_s = specs, schedule, repeat, lead_s
        self.raw_dir, self.tag, self.cpus, self.port, self.log = raw_dir, tag, cpus, port, log
        self.stop_event = threading.Event()
        self.procs, self.steps, self.t_start = {}, [], None
        self.logs = {s["ns"]: open(os.path.join(raw_dir, f"hping_{tag}_{s['ns']}.log"), "a") for s in specs}

    def _kill(self):
        for p in self.procs.values():
            p.kill()
        for s in self.specs:
            subprocess.run(f"ip netns pids {s['ns']} | xargs -r kill -9", shell=True)
        for p in self.procs.values():
            try:
                p.wait(1)
            except subprocess.TimeoutExpired:
                pass
        self.procs = {}

    def _launch_all(self, args):
        pin = f"taskset -c {self.cpus} " if self.cpus else ""
        for s in self.specs:
            cmd = f"exec ip netns exec {s['ns']} {pin}hping3 -n --udp -p {self.port} {args} {s['target']}"
            self.procs[s["ns"]] = subprocess.Popen(cmd, shell=True, stdout=self.logs[s["ns"]], stderr=subprocess.STDOUT)

    def _step(self, secs, args):
        self._kill()
        t0 = time.time()
        self._launch_all(args)
        if self.stop_event.wait(min(0.3, secs)):
            return False
        for s in self.specs:                                   # one relaunch if it died at start-up
            if self.procs[s["ns"]].poll() is not None:
                pin = f"taskset -c {self.cpus} " if self.cpus else ""
                self.procs[s["ns"]] = subprocess.Popen(
                    f"exec ip netns exec {s['ns']} {pin}hping3 -n --udp -p {self.port} {args} {s['target']}",
                    shell=True, stdout=self.logs[s["ns"]], stderr=subprocess.STDOUT)
        r0 = {s["ns"]: rx_packets(s["dev"]) for s in self.specs}
        ta = time.time()
        stopped = self.stop_event.wait(max(0.0, secs - (ta - t0)))
        dt = max(1e-3, time.time() - ta)
        pps = {s["ns"]: round((rx_packets(s["dev"]) - r0[s["ns"]]) / dt) for s in self.specs}
        exp = expected_pps(args)
        need = 300 if exp is None else min(300, 0.3 * exp)
        self.steps.append({"t": round(t0, 3), "secs": round(time.time() - t0, 3), "args": args, "pps": pps,
                           "ok": dt < 0.2 or all(v >= need for v in pps.values())})   # too short to judge -> ok
        return not stopped

    def run(self):
        self.t_start = time.time()
        first = True
        while not self.stop_event.is_set():
            for i, (secs, args) in enumerate(self.schedule):
                extra = self.lead_s if first and i == 0 else 0.0
                last = (i == len(self.schedule) - 1) and not self.repeat
                if not self._step(1e9 if last else secs + extra, args):
                    return
            first = False
            if not self.repeat:
                return

    def stop(self):
        self.stop_event.set()
        self.join(5)
        self._kill()
        for f in self.logs.values():
            f.close()

    @property
    def ok(self):
        return bool(self.steps) and all(s["ok"] for s in self.steps)
