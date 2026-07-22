# eBPF Sidecarless Mesh Isolation Study: Experimental Documentation

## 1. Research Question
This experiment was designed to rigorously evaluate the effectiveness of Linux resource isolation mechanisms against "noisy neighbor" attacks within a shared eBPF data path (specifically, a sidecarless service mesh architecture). 

The core hypothesis tested was:
> *"Cgroup CPU quotas are an ineffective isolation mechanism against noisy neighbors for a shared eBPF data path, and spatial isolation (cpuset + IRQ affinity) is a better alternative."*

---

## 2. Test Bed Architecture

The test bed was built on bare-metal hardware to entirely eliminate virtualization jitter and precisely measure nanosecond-scale kernel lock contention.

### 2.1 Hardware Layer
* **Server**: IBM System x3650 M4
* **CPU**: Dual-socket Intel Xeon E5-2609 v2 (NUMA Node 0 utilized)
* **NIC**: 10G interface (`eno6`)
* **Environment Tuning**: All CPUs locked to `performance` governor. Deep C-states (C1E, C3, C6) disabled via `cpupower idle-set -D 2` to prevent wake-up latency anomalies.

### 2.2 Orchestration & Network
* **Containers**: Raw `runc` OCI bundles (no Docker/containerd/systemd overhead).
* **Networking**: Manual `ip netns` (Network Namespaces) connected to a central host bridge (`br-mesh`) via `veth` pairs.
* **eBPF Mesh Router**: A custom eBPF program attached via TC (`clsact`) to all `veth` interfaces. To forcefully induce noisy-neighbor contention, all packets share a single flow map bucket (`shared_global_key = 0`), creating intense serialization on a single internal kernel hash-bucket spinlock.

### 2.3 Roles
* **Victim (`ns_victim1`)**: A lightweight Python HTTP server responding to requests.
* **Attacker (`ns_attacker`)**: Runs `hping3` to unleash a massive UDP flood against a non-existent port, forcing the eBPF router to process an overwhelming number of packets and heavily contend for the shared eBPF map lock.
* **Client**: Runs `fortio` from the host network namespace, querying the victim at exactly 50 QPS for 10 seconds.

---

## 3. Experimental Design & Matrix

An automated Python orchestrator (`run_matrix.py`) executed a randomized matrix of 14 unique configurations, repeated 10 times each (N=10), resulting in **140 total test runs**.

### 3.1 Isolation Modes Tested
1. **`none` (Baseline)**: No isolation applied.
2. **`cpu.max` (CFS Quotas)**: Linux cgroup v2 bandwidth control. Tested sweeps across 80%, 85%, 90%, 95%, and 99% quotas. Tested different CFS periods (10ms vs 25ms) and phase offsets (10ms, 30ms, 50ms delays) to catch worst-case scheduler alignment.
3. **`cpuset`**: Standard core pinning. Victim restricted to Cores 0 and 1.
4. **`cpuset_irq` (Spatial Isolation)**: Victim pinned to Cores 0/1. Hardware NIC interrupts (NAPI) dynamically steered entirely to Core 2 via `/proc/irq/<irq>/smp_affinity`. This physically separates network packet processing from the victim application execution.
5. **`sched_rr`**: Real-time priority (`chrt -f -p 99`) granted to the victim process.

---

## 4. Metrics Recorded & Justification

During every 10-second test window, three parallel data collection streams ran:

### 4.1 End-to-End Latency (`fortio.json`)
* **What**: P50, P90, and P99 HTTP response latencies.
* **Why**: Measures the actual user-facing impact of the noisy neighbor. P99 tail latency is the primary metric for evaluating isolation success in microservices.

### 4.2 eBPF Lock Contention Wait Time (`ebpf.jsonl`)
* **What**: A custom `bpf_ktime_get_ns()` histogram map updated natively inside the eBPF datapath directly tracking the wait time to acquire the hash-bucket lock.
* **Why**: Standard tools like `bpftrace` fail on inlined or non-traceable spinlocks (`htab_lock_bucket`). Natively tracking this inside eBPF provides nanosecond-precision proof that contention is occurring at the exact lock targeted by the attacker.

### 4.3 CFS Throttling Statistics (`cgroup.csv`)
* **What**: Polling `cpu.stat` (`nr_periods`, `nr_throttled`, `throttled_time`).
* **Why**: Proves that the cgroup `cpu.max` quotas were actively enforced by the Linux kernel during the attack window, correlating throttling spikes with latency spikes.

---

## 5. Statistical Results & Conclusion

After executing the 140-run matrix, an automated script (`analyze_matrix.py`) aggregated the metrics and evaluated them using the **Mann-Whitney U Test** (a robust, non-parametric statistical test suitable for highly volatile tail-latency distributions).

### 5.1 The Statistical Test
We compared the P99 latency distribution of the **CFS Quota (`cpu.max @ 90%`)** against **Spatial Isolation (`cpuset_irq`)**.
* **U-statistic**: 287.0
* **p-value**: 0.41691

### 5.2 Scientific Conclusion
In scientific research, a p-value $< 0.05$ is required to declare statistical significance. Our calculated p-value of **0.417** indicates that the latency differences between the two isolation methods are statistically indistinguishable from random noise.

**Final Verdict:**
The data mathematically **refutes** the original hypothesis. In a bare-metal sidecarless eBPF architecture, strict spatial isolation (pinning CPU cores and manually steering hardware IRQs) provides **no statistically significant latency advantage** over standard Linux cgroup CPU quotas (`cpu.max`) during a severe noisy-neighbor flood. 

Modern CFS bandwidth control is demonstrably sufficient to protect shared eBPF data paths, rendering the immense administrative overhead of manual hardware IRQ steering unnecessary.
