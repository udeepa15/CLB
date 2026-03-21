# Troubleshooting

## 1) `runc` command not found

Install dependencies:

```bash
sudo ./environments/base-setup.sh
```

## 2) eBPF/tc attach fails

Check bridge exists:

```bash
ip link show clb-br0
```

Check tc status:

```bash
sudo ./ebpf/tc/attach.sh status
```

If needed, reset and retry:

```bash
sudo ./ebpf/tc/attach.sh detach
sudo ./scripts/stop-containers.sh
sudo ./scripts/start-containers.sh
sudo ./ebpf/tc/build.sh
sudo ./ebpf/tc/attach.sh attach
```

## 3) Containers start but client cannot connect

Verify runC states:

```bash
sudo runc --root /run/runc-ebpf-noisy-neighbor list
```

Verify IPs are assigned:

```bash
ip addr show clb-br0
```

From host test:

```bash
curl -v http://10.0.0.2:8080/
curl -v http://10.0.0.3:8080/
```

## 4) WSL2 kernel limitations

If tc/eBPF is unavailable, use a newer WSL2 kernel.

Check:

```bash
uname -a
```

## 5) Permission errors

Most scripts require root privileges. Run with `sudo`.

## 6) Clean reset

```bash
sudo ./scripts/stop-containers.sh
sudo ./networking/teardown-network.sh
sudo ./ebpf/tc/attach.sh detach
```

Then re-run setup and experiments.
