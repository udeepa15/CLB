#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$REPO_ROOT/ebpf_research"
LOG_DIR="$ROOT_DIR/results/raw"
mkdir -p "$LOG_DIR"

ns_list=(v_netns1 v_netns2 v_netns3 v_netns4 v_netns5 v_netns_adv)
ports=(8080 8080 8080 8080 8080 9090)

start_one() {
  local ns="$1"
  local port="$2"
  local log_file="$LOG_DIR/${ns}_${port}.log"

  if sudo ip netns exec "$ns" ss -lnt | grep -q ":${port} "; then
    echo "Listener already running in $ns:$port"
    return 0
  fi

  sudo ip netns exec "$ns" bash -lc "nohup python3 '$SCRIPT_DIR/fast_http_server.py' '$port' > '$log_file' 2>&1 & echo \$!"

  for _ in $(seq 1 20); do
    if sudo ip netns exec "$ns" ss -lnt | grep -q ":${port} "; then
      return 0
    fi
    sleep 0.5
  done

  echo "Listener failed to start in $ns:$port" >&2
  tail -n 20 "$log_file" >&2 || true
  return 1
}

stop_all() {
  for ns in "${ns_list[@]}"; do
    ip netns pids "$ns" | xargs -r sudo kill 2>/dev/null || true
  done
}

case "${1:-start}" in
  start)
    for idx in "${!ns_list[@]}"; do
      start_one "${ns_list[$idx]}" "${ports[$idx]}"
    done
    ;;
  stop)
    stop_all
    ;;
  restart)
    stop_all
    for idx in "${!ns_list[@]}"; do
      start_one "${ns_list[$idx]}" "${ports[$idx]}"
    done
    ;;
  *)
    echo "Usage: $0 {start|stop|restart}" >&2
    exit 1
    ;;
esac
