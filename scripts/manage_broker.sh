#!/usr/bin/env bash
set -euo pipefail
# manage_broker.sh - start/stop an in-memory Redis broker bound to the host bridge
# Usage: manage_broker.sh start|stop|status

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$REPO_ROOT/ebpf_research"
RESULTS="$ROOT/results"
mkdir -p "$RESULTS"

BRIDGE=br-queue
BROKER_IP=10.200.0.1
BROKER_PORT=6379
REDIS_CONF="$ROOT/redis_broker.conf"
REDIS_PIDFILE="$RESULTS/redis_broker_6379.pid"

check_cmd(){ command -v "$1" >/dev/null 2>&1 || { echo "Please install $1" >&2; exit 1; } }

ensure_bridge(){
  # ensure a host bridge exists with the broker IP
  if ! ip link show "$BRIDGE" >/dev/null 2>&1; then
    echo "Creating bridge $BRIDGE"
    sudo ip link add name "$BRIDGE" type bridge || true
  fi
  sudo ip addr add ${BROKER_IP}/24 dev "$BRIDGE" 2>/dev/null || true
  sudo ip link set dev "$BRIDGE" up || true
}

start(){
  check_cmd redis-server
  ensure_bridge
  cat > "$REDIS_CONF" <<EOF
bind $BROKER_IP
port $BROKER_PORT
appendonly no
save ""
protected-mode no
daemonize yes
pidfile $REDIS_PIDFILE
logfile "$RESULTS/redis_broker.log"
# optional memory policy to avoid disk pressure; tune as needed
maxmemory 0
maxmemory-policy noeviction
EOF
  echo "Starting redis-server bound to $BROKER_IP:$BROKER_PORT"
  sudo redis-server "$REDIS_CONF"
  sleep 0.5
  if [ -f "$REDIS_PIDFILE" ]; then
    echo "Started redis (pidfile: $REDIS_PIDFILE)"
  else
    echo "Failed to start redis; check $RESULTS/redis_broker.log" >&2
    exit 1
  fi
}

stop(){
  if [ -f "$REDIS_PIDFILE" ]; then
    PID=$(cat "$REDIS_PIDFILE" 2>/dev/null || true)
    if [ -n "$PID" ]; then
      echo "Stopping redis PID $PID"
      sudo kill "$PID" || true
      sleep 0.5
    fi
    sudo rm -f "$REDIS_PIDFILE" || true
  else
    # try pkill
    sudo pkill -f "redis-server .*redis_broker.conf" || true
  fi
}

status(){
  if [ -f "$REDIS_PIDFILE" ]; then
    echo "Redis pidfile exists:" $(cat "$REDIS_PIDFILE")
  else
    echo "Redis pidfile not found; check if redis-server is running";
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  *) echo "Usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
