# Queue Testbed Runbook

This runbook shows how to run the Redis queue benchmark on a remote Linux server from a shell or Remote Desktop session.

## What this testbed does

- Starts a Redis broker bound to `10.200.0.1` on a host bridge named `br-queue`.
- Creates tenant network namespaces and veth pairs.
- Starts one queue worker per tenant namespace.
- Optionally runs an attacker process that pushes extra Redis traffic.
- Stores logs and results under `ebpf_research/results/`.

## Prerequisites

Install the required packages on the server:

```bash
sudo apt update
sudo apt install -y redis-server iproute2 python3 python3-pip tmux
```

Install the Python Redis client globally (required for root inside namespaces):

```bash
sudo pip3 install redis
```

If you plan to run long tests, `tmux` is strongly recommended.

## One-time setup

From the repo root:

```bash
cd ~/CLB
mkdir -p ebpf_research/results
```

If you want to make the shell scripts executable, run:

```bash
chmod +x scripts/manage_broker.sh scripts/deploy_queue_workloads.sh scripts/sweep_queue_matrix.sh
```

You can also run them with `bash` instead of changing permissions.

## Smoke test

Run these commands from the repo root:

```bash
cd ~/CLB
sudo bash scripts/manage_broker.sh start
python3 scripts/seed_queues.py --broker-ip 10.200.0.1 --num-queues 3 --total-items 10000
sudo bash scripts/deploy_queue_workloads.sh --num-tenants 3 --duration 30 --broker-ip 10.200.0.1
python3 scripts/adv_storm.py --broker-ip 10.200.0.1 --rate 1000 --duration 30 --queue-name tenant_queue_v1 > ebpf_research/results/adv_storm_smoke.log 2>&1 &
sleep 35
grep -R "RESULT:" ebpf_research/results
sudo bash scripts/manage_broker.sh stop
```

Expected worker output format:

```text
RESULT: completed=<integer> errors=<integer> duration_sec=<float> throughput_mps=<float>
```

## Full sweep

Example matrix sweep:

```bash
cd ~/CLB
nohup bash scripts/sweep_queue_matrix.sh --tenants "1,3,5" --attacker_rates "0,10000,20000" --duration 60 --seed 1000000 > ebpf_research/results/sweep.log 2>&1 &
```

What the sweep does:

- Starts the Redis broker.
- Seeds the largest queue set needed by the tenant list.
- Deploys tenant namespaces.
- Runs the attacker when the attacker rate is greater than zero.
- Copies logs into `ebpf_research/results/t<tenants>_r<rate>_<timestamp>/`.

## Where to look for outputs

- Broker log: `ebpf_research/results/redis_broker.log`
- Broker pidfile: `ebpf_research/results/redis_broker_6379.pid`
- Tenant worker logs: `ebpf_research/results/worker_tenant<N>.log`
- Sweep subdirectories: `ebpf_research/results/t<tenants>_r<rate>_<timestamp>/`

To inspect completion lines:

```bash
grep -R "RESULT:" ebpf_research/results
```

## Cleanup

Stop the broker:

```bash
sudo bash scripts/manage_broker.sh stop
```

Delete tenant namespaces if you want a clean slate:

```bash
for ns in $(ip netns list | awk '{print $1}'); do
  sudo ip netns delete "$ns" || true
done
```

Remove the bridge if needed:

```bash
sudo ip link set br-queue down || true
sudo ip link delete br-queue type bridge || true
```

## Common issues

- If `sudo manage_broker.sh start` says command not found, use `sudo bash scripts/manage_broker.sh start` or `sudo ./manage_broker.sh start` after `chmod +x`.
- If the attacker log redirection fails, make sure you write to `ebpf_research/results/...`, not a relative `results/` directory inside `scripts/`.
- If `redis` is missing in Python, install it with `sudo pip3 install redis` (must be global, not `--user`, since workers run as root in namespaces).
- If workers show `completed=0 errors=<N>`, the namespaces can't reach the broker. Run `sudo ip link show br-queue` to confirm the bridge exists; if not, restart the broker with `sudo bash scripts/manage_broker.sh start`.
- If you get namespace or veth errors, rerun the commands with `sudo` and confirm your user has permission to create network namespaces.
