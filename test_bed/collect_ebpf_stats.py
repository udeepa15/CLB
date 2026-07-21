#!/usr/bin/env python3
import sys
import time
import subprocess
import json

def get_hist():
    res = subprocess.run(['sudo', 'bpftool', 'map', 'dump', 'name', 'lock_latency_hist', '-j'], capture_output=True, text=True)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except:
        return None

def main():
    if len(sys.argv) < 2:
        print('Usage: collect_ebpf_stats.py <output_jsonl>')
        sys.exit(1)
    
    out_file = sys.argv[1]
    
    with open(out_file, 'w') as f:
        try:
            while True:
                ts = time.time()
                data = get_hist()
                if data:
                    record = {'timestamp': ts, 'histogram': data}
                    f.write(json.dumps(record) + '\n')
                    f.flush()
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        
        # One final dump
        ts = time.time()
        data = get_hist()
        if data:
            record = {'timestamp': ts, 'histogram': data}
            f.write(json.dumps(record) + '\n')

if __name__ == '__main__':
    main()
