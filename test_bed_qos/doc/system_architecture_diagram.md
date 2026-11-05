# Sidecarless eBPF Service Mesh Architecture (Dynamic Stackelberg QoS)

> **Academic Interim Report Reference Diagram**  
> *Architecture*: Generalized N-Node Sidecarless eBPF Service Mesh implementing Dynamic Stackelberg Game-Theoretic Rate Limiting.

---

## 1. System Architecture Diagram

```mermaid
flowchart TD
    %% Styling Definitions
    classDef control fill:#DCFCE7,stroke:#15803D,stroke-width:2px,color:#14532D;
    classDef kernel fill:#DBEAFE,stroke:#1D4ED8,stroke-width:2px,color:#1E3A8A;
    classDef userpool fill:#F3E8FF,stroke:#6B21A8,stroke-width:2px,color:#581C87;
    classDef traffic fill:#FEF3C7,stroke:#B45309,stroke-width:2px,color:#78350F;
    classDef nodeN fill:#F3F4F6,stroke:#9CA3AF,stroke-width:2px,stroke-dasharray:5 5,color:#374151;

    %% External Network Layer
    subgraph EXT ["1. External Network Layer"]
        TRAFFIC["External Ingress Network Traffic<br>(Victim Requests & Noisy-Neighbor Floods)"]
    end
    class TRAFFIC traffic;

    %% Global Control Plane
    subgraph CTRL_PLANE ["2. Global Control Plane (User Space)"]
        CONTROLLER["Game-Theoretic QoS Controller<br>(Stackelberg Leader-Follower Solver)"]
    end
    class CONTROLLER control;

    %% Generalized Worker Nodes
    subgraph NODES ["3. Generalized Worker Node Infrastructure"]
        subgraph NODE1 ["Worker Node 1"]
            subgraph KERNEL1 ["Kernel Space (Linux TC Subsystem)"]
                TC_HOOK1["eBPF Fast-Path Limiter<br>(tc clsact Hook Filter)"]
                MAPS1[("eBPF PERCPU_HASH Maps<br>• Token Bucket Limits (r_i)<br>• Lock Contention Telemetry")]
                DROP1["Packet Drop<br>(TC_ACT_SHOT)"]
            end

            subgraph USER1 ["User Space (Container Runtime)"]
                PODS1["Multi-Tenant Pod Pool<br>• Real-Time Workloads (L0)<br>• Background Workloads (L1/L2)"]
            end
        end

        subgraph NODEN ["Worker Node N (Scalable System)"]
            NODE_N_CONTENT["Node N Infrastructure<br>• TC eBPF Fast-Path Filter<br>• Per-CPU Maps & Pods"]
        end
    end
    class TC_HOOK1,MAPS1,DROP1 kernel;
    class PODS1 userpool;
    class NODE_N_CONTENT nodeN;

    %% Connections & Flow Paths
    TRAFFIC -->|Ingress Packets| TC_HOOK1
    TRAFFIC -.->|Scalable Distribution| NODE_N_CONTENT

    TC_HOOK1 -->|Tokens Available: TC_ACT_OK| PODS1
    TC_HOOK1 -->|Rate Limit Exceeded| DROP1

    MAPS1 -.->|1. Async Telemetry Polling (Hits/sec)| CONTROLLER
    CONTROLLER -.->|2. Push Stackelberg Limits (r_i)| MAPS1
    CONTROLLER -.->|N-Node Control Sync| NODE_N_CONTENT
```

---

## 2. Architectural Component Description

### 1. Global Control Plane (User Space)
* **Game-Theoretic QoS Controller**: A central control loop executing the Stackelberg Leader-Follower equilibrium solver. It asynchronously polls lock contention telemetry (`update_counter_map` / `lock_latency_hist`) from worker node eBPF maps and computes dynamic optimal follower rate caps $r_i$ to shield leader (victim) pods from contention degradation.

### 2. Kernel-Space Fast-Path Layer (Worker Node 1)
* **eBPF Fast-Path Limiter**: Attached to the Traffic Control (`tc`) `clsact` hook on network interfaces. Performs zero-copy, low-overhead token bucket checks on incoming packets.
* **eBPF `PERCPU_HASH` Maps**: Per-CPU sharded BPF maps (`percpu_rate_limit_map`) storing rate limits ($r_i$), token balances, and telemetry counters. Per-CPU sharding eliminates cross-CPU spinlock contention (`htab_lock_bucket`).
* **Packet Action**:
  * Forward (`TC_ACT_OK`): Allowed packets are delivered directly to tenant pods.
  * Drop (`TC_ACT_SHOT`): Packets exceeding tenant rate limits are dropped immediately in kernel space.

### 3. User-Space Pod Pool (Worker Node 1)
* **Multi-Tenant Pod Pool**: Heterogeneous containerized microservices split into priority tiers (L0 High-Priority Victim Pods vs. L1/L2 Background Noisy Neighbor Pods).

### 4. Horizontal Scalability (Worker Node N)
* **Worker Node N**: Represented as a dashed node demonstrating horizontal scalability across $N$ worker nodes in a production cluster managed by the central controller.
