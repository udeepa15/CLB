#!/usr/bin/env python3
import sys
import time
import os

def main():
    if len(sys.argv) < 3:
        print("Usage: collect_cgroup_stats.py <cgroup_path> <output_file>")
        sys.exit(1)
    
    cgroup_path = sys.argv[1]
    out_file = sys.argv[2]
    
    cpu_stat_path = os.path.join(cgroup_path, "cpu.stat")
    if not os.path.exists(cpu_stat_path):
        print(f"Error: {cpu_stat_path} not found.")
        sys.exit(1)
    
    with open(out_file, "w") as f:
        f.write("timestamp,nr_periods,nr_throttled,throttled_time\n")
        try:
            while True:
                ts = time.time()
                with open(cpu_stat_path, "r") as stat_f:
                    stats = stat_f.read()
                
                parsed = {}
                for line in stats.strip().split("\n"):
                    parts = line.split()
                    if len(parts) == 2:
                        parsed[parts[0]] = parts[1]
                
                f.write(f"{ts},{parsed.get('nr_periods', 0)},{parsed.get('nr_throttled', 0)},{parsed.get('throttled_time', 0)}\n")
                f.flush()
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
