#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$REPO_ROOT/ebpf_research"
LOG_DIR="$ROOT_DIR/results/raw"
mkdir -p "$LOG_DIR"

ns_list=(v_netns1 v_netns2 v_netns3 v_netns4 v_netns5 v_netns_adv)
ports=(8080 8080 8080 8080 8080 9090)
binds=(10.200.1.2 10.200.2.2 10.200.3.2 10.200.4.2 10.200.5.2 10.200.6.2)

start_one() {
  local ns="$1"
  local bind_ip="$2"
  local port="$3"
  local log_file="$LOG_DIR/${ns}_${port}.log"

  if ip netns pids "$ns" | xargs -r kill -0 2>/dev/null; then
    echo "Listener already running in $ns:$port"
    return 0
  fi

  sudo ip netns exec "$ns" bash -lc "nohup python3 -m http.server '$port' --bind '$bind_ip' > '$log_file' 2>&1 & echo \$!"
}

stop_all() {
  for ns in "${ns_list[@]}"; do
    ip netns pids "$ns" | xargs -r sudo kill 2>/dev/null || true
  done
}

case "${1:-start}" in
  start)
    for idx in "${!ns_list[@]}"; do
      start_one "${ns_list[$idx]}" "${binds[$idx]}" "${ports[$idx]}"
    done
    ;;
  stop)
    stop_all
    ;;
  restart)
    stop_all
    for idx in "${!ns_list[@]}"; do
      start_one "${ns_list[$idx]}" "${binds[$idx]}" "${ports[$idx]}"
    done
    ;;
  *)
    echo "Usage: $0 {start|stop|restart}" >&2
    exit 1
    ;;
esac
