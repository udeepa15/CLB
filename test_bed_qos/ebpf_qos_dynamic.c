#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <linux/ip.h>
#include <linux/in.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <linux/if_ether.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#ifndef SEC
# define SEC(NAME) __attribute__((section(NAME), used))
#endif

/*
 * Dynamic Stackelberg Rate Limiting State (eBPF Fast Path).
 * Beeswax (SIGCOMM '26) rationale:
 * Fast-path eBPF programs minimize in-kernel inline computation.
 * Sharded per CPU via BPF_MAP_TYPE_PERCPU_HASH to prevent cross-CPU lock contention.
 */
struct rate_limit_entry {
    __u64 rate_bytes_per_sec; // Dynamic rate limit set by qos_controller.py
    __u64 max_burst_bytes;    // Max token capacity
    __u64 tokens;             // Token balance (bytes)
    __u64 last_update_ns;     // Timestamp of last replenishment
    __u64 packets_passed;     // Forwarded count
    __u64 packets_dropped;    // Dropped count
};

/*
 * Per-CPU Sharded Rate Limit Map.
 * Key: __u32 (Source IP address - PER-TENANT KEYING).
 */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_HASH);
    __uint(max_entries, 65536);
    __type(key, __u32);
    __type(value, struct rate_limit_entry);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} percpu_rate_limit_map SEC(".maps");

/*
 * Lock latency histogram (instrumentation input for qos_controller.py & collector).
 */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 64);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} lock_latency_hist SEC(".maps");

/*
 * Per-CPU map update counter (instrumentation input for qos_controller.py & collector).
 */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} update_counter_map SEC(".maps");

static __always_inline __u32 log2l(__u64 v)
{
    __u32 r = 0;
    if (v >= 0x100000000ULL) { v >>= 32; r += 32; }
    if (v >= 0x10000ULL) { v >>= 16; r += 16; }
    if (v >= 0x100ULL) { v >>= 8; r += 8; }
    if (v >= 0x10ULL) { v >>= 4; r += 4; }
    if (v >= 0x4ULL) { v >>= 2; r += 2; }
    if (v >= 0x2ULL) { r += 1; }
    return r;
}

SEC("classifier")
int mesh_router(struct __sk_buff *skb) {
    void *data_end = (void *)(long)skb->data_end;
    void *data     = (void *)(long)skb->data;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return TC_ACT_OK;

    if (bpf_ntohs(eth->h_proto) != ETH_P_IP)
        return TC_ACT_OK;

    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end)
        return TC_ACT_OK;

    __u64 start_ns = bpf_ktime_get_ns();
    __u32 src_ip = iph->saddr;
    __u32 pkt_len = skb->len;

    // Fast-path lookup on per-CPU sharded rate map (zero cross-CPU spinlock contention)
    struct rate_limit_entry *entry = bpf_map_lookup_elem(&percpu_rate_limit_map, &src_ip);
    int action = TC_ACT_OK;

    if (entry) {
        if (entry->rate_bytes_per_sec == 0) {
            // Uncapped tenant (Leader / Victim)
            entry->packets_passed += 1;
            action = TC_ACT_OK;
        } else {
            // Fast-path token-bucket check
            __u64 now_ns = start_ns;
            __u64 elapsed_ns = now_ns - entry->last_update_ns;

            if (elapsed_ns > 0) {
                __u64 new_tokens = (elapsed_ns * entry->rate_bytes_per_sec) / 1000000000ULL;
                entry->tokens += new_tokens;
                if (entry->tokens > entry->max_burst_bytes)
                    entry->tokens = entry->max_burst_bytes;
                entry->last_update_ns = now_ns;
            }

            if (entry->tokens >= pkt_len) {
                entry->tokens -= pkt_len;
                entry->packets_passed += 1;
                action = TC_ACT_OK;
            } else {
                entry->packets_dropped += 1;
                action = TC_ACT_SHOT; // Fast-path drop
            }
        }
    }

    __u64 duration = bpf_ktime_get_ns() - start_ns;

    // Log stats for controller & collectors
    __u32 zero_key = 0;
    __u64 *update_cnt = bpf_map_lookup_elem(&update_counter_map, &zero_key);
    if (update_cnt) {
        *update_cnt += 1;
    }

    __u32 slot = log2l(duration);
    if (slot >= 64) slot = 63;
    __u64 *count = bpf_map_lookup_elem(&lock_latency_hist, &slot);
    if (count) {
        *count += 1;
    }

    return action;
}

char _license[] SEC("license") = "GPL";
