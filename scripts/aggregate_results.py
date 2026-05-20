#!/usr/bin/env python3
import json
import re
from pathlib import Path
import csv
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
RESULT_DIR = REPO_ROOT / 'ebpf_research' / 'results' / 'raw'
OUT_CSV = REPO_ROOT / 'ebpf_research' / 'sidecar_vs_sidecarless_metrics.csv'

def find_value(obj, keys):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys:
                return v
            r = find_value(v, keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = find_value(item, keys)
            if r is not None:
                return r
    return None

def parse_fortio(path):
    with open(path) as f:
        j = json.load(f)
    # keys to look for
    qps = find_value(j, {'actualqps','qps','actual_qps','actual_qps'})
    count = find_value(j, {'count','requests','numreq','nrequests'})
    p50 = find_value(j, {'p50','P50','50pct','p50_ms','p50_sec'})
    p95 = find_value(j, {'p95','P95','95pct','p95_ms'})
    p99 = find_value(j, {'p99','P99','99pct','p99_ms'})
    p999 = find_value(j, {'p999','P999','999pct','p999_ms'})
    # convert None to empty
    return {
        'qps': float(qps) if qps is not None else None,
        'count': int(count) if count is not None else None,
        'p50_ms': float(p50)*1000 if isinstance(p50, (int,float)) and p50<10 else (float(p50) if p50 is not None else None),
        'p95_ms': float(p95)*1000 if isinstance(p95, (int,float)) and p95<10 else (float(p95) if p95 is not None else None),
        'p99_ms': float(p99)*1000 if isinstance(p99, (int,float)) and p99<10 else (float(p99) if p99 is not None else None),
        'p999_ms': float(p999)*1000 if isinstance(p999, (int,float)) and p999<10 else (float(p999) if p999 is not None else None),
    }

def parse_wrk(path):
    text = Path(path).read_text()
    m = re.search(r'Requests/sec:\s*([0-9.]+)', text)
    qps = float(m.group(1)) if m else None
    return {'attacker_qps': qps}

def discover():
    rows = []
    for f in RESULT_DIR.glob('*'):
        name = f.name
        parts = name.split('_')
        # filenames follow patterns used in sweep script
        # fortio_v{n}_{mode}_{rate}_{ts}.json
        if name.startswith('fortio_v') and name.endswith('.json'):
            m = re.match(r'fortio_v(\d+)_(\w+)_(\d+)_(\d+)\.json', name)
            if not m:
                continue
            victim = int(m.group(1))
            mode = m.group(2)
            rate = int(m.group(3))
            data = parse_fortio(f)
            row = {'mode': mode, 'attacker_rate': rate, 'victim': victim, 'fortio_file': str(f)}
            row.update(data)
            rows.append(row)
        elif name.startswith('wrk_adv_') and name.endswith('.txt'):
            m = re.match(r'wrk_adv_(\w+)_(\d+)_(\d+)\.txt', name)
            if not m:
                continue
            mode = m.group(1)
            rate = int(m.group(2))
            data = parse_wrk(f)
            row = {'mode': mode, 'attacker_rate': rate, 'victim': 'adv', 'wrk_file': str(f)}
            row.update(data)
            rows.append(row)
    return rows

def write_csv(rows):
    fieldnames = ['mode','attacker_rate','victim','qps','count','p50_ms','p95_ms','p99_ms','p999_ms','attacker_qps','fortio_file','wrk_file']
    with open(OUT_CSV, 'w', newline='') as csvf:
        w = csv.DictWriter(csvf, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in fieldnames})

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
