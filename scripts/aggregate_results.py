#!/usr/bin/env python3
import csv
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
RESULT_DIR = REPO_ROOT / 'ebpf_research' / 'results' / 'raw'
OUT_CSV = REPO_ROOT / 'ebpf_research' / 'sidecar_vs_sidecarless_metrics.csv'


def parse_key_value_log(path):
    text = Path(path).read_text()

    def grab(pattern):
        match = re.search(pattern, text, re.MULTILINE)
        return float(match.group(1)) if match else None

    return {
        'target_qps': grab(r'^Target QPS:\s*([0-9.+-eE]+)\s*$'),
        'actual_qps': grab(r'^Actual QPS:\s*([0-9.+-eE]+)\s*$'),
        'p50_us': grab(r'^p50:\s*([0-9.+-eE]+)us\s*$'),
        'p95_us': grab(r'^p95:\s*([0-9.+-eE]+)us\s*$'),
        'p99_us': grab(r'^p99:\s*([0-9.+-eE]+)us\s*$'),
        'p999_us': grab(r'^p99\.9:\s*([0-9.+-eE]+)us\s*$'),
    }


def parse_bpftrace_histogram(path):
    lines = Path(path).read_text().splitlines()
    capture = False
    histogram = []
    for line in lines:
        if '@lookup_latency_ns' in line:
            capture = True
            histogram.append(line.strip())
            continue
        if capture:
            if not line.strip():
                if histogram:
                    break
                continue
            histogram.append(line.rstrip())
    return ' | '.join(histogram) if histogram else ''


def parse_run_name(name):
    match = re.match(r'wrk2_(v\d+|adv)_(\w+)_(\d+)_(\d+)\.log$', name)
    if not match:
        return None
    role = match.group(1)
    mode = match.group(2)
    rate = int(match.group(3))
    ts = match.group(4)
    return role, mode, rate, ts


def discover():
    rows = []
    lookup_histograms = {}

    for f in RESULT_DIR.glob('bpftrace_*.txt'):
        match = re.match(r'bpftrace_(\w+)_(\d+)_(\d+)\.txt$', f.name)
        if not match:
            continue
        key = (match.group(1), int(match.group(2)), match.group(3))
        lookup_histograms[key] = parse_bpftrace_histogram(f)

    for f in RESULT_DIR.glob('wrk2_*.log'):
        parsed = parse_run_name(f.name)
        if not parsed:
            continue
        role, mode, rate, ts = parsed
        row = {
            'mode': mode,
            'attacker_rate': rate,
            'victim': int(role[1:]) if role.startswith('v') else 'adv',
            'wrk2_file': str(f),
            'lookup_latency_ns_hist': lookup_histograms.get((mode, rate, ts), ''),
        }
        row.update(parse_key_value_log(f))
        rows.append(row)

    return rows


def write_csv(rows):
    fieldnames = [
        'mode',
        'attacker_rate',
        'victim',
        'target_qps',
        'actual_qps',
        'p50_us',
        'p95_us',
        'p99_us',
        'p999_us',
        'wrk2_file',
        'lookup_latency_ns_hist',
    ]
    with open(OUT_CSV, 'w', newline='') as csvf:
        writer = csv.DictWriter(csvf, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, '') for key in fieldnames})


def main():
    if not RESULT_DIR.exists():
        print('Result directory not found:', RESULT_DIR)
        sys.exit(1)
    rows = discover()
    if not rows:
        print('No result files found in', RESULT_DIR)
        sys.exit(0)
    write_csv(rows)
    print('Wrote CSV to', OUT_CSV)


if __name__ == '__main__':
    main()
