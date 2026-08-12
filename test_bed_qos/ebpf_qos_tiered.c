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
 * Terway-QoS Priority Tier Definitions:
 * L0: High-Priority Victim Traffic (Uncapped, reclaims full bandwidth)
 * L1: Medium-Priority Traffic
 * L2: Low-Priority Attacker/Noisy-Neighbor Traffic (Rate Capped)
 */
#define TIER_L0 0
#define TIER_L1 1
#define TIER_L2 2

struct tenant_qos_state {
    __u32 tier;               // TIER_L0, TIER_L1, TIER_L2
    __u64 tokens;             // Current token balance (bytes)
    __u64 last_update_ns;     // Timestamp of last replenishment
    __u64 rate_bytes_per_sec; // Rate cap in bytes/sec
    __u64 max_burst_bytes;    // Token bucket capacity
    __u64 packets_passed;     // Total forwarded packets
    __u64 packets_dropped;    // Total dropped packets
};

/*
 * Per-Tenant QoS State Map.
 * Rationale (Netflix "Noisy Neighbor Detection with eBPF"):
 * Standard BPF_MAP_TYPE_HASH selected over LRU_HASH to avoid internal
 * global LRU synchronization overhead under high throughput.
 * Key: __u32 (Source IP address - PER-TENANT KEYING, NO SINGLE GLOBAL KEY).
 */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 65536);
    __type(key, __u32);
    __type(value, struct tenant_qos_state);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} tenant_qos_map SEC(".maps");

/*
 * Lock latency histogram (compatibility for collect_ebpf_stats.py).
 */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 64);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} lock_latency_hist SEC(".maps");

/*
 * Per-CPU map update counter (compatibility for collect_ebpf_stats.py).
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

    struct tenant_qos_state *state = bpf_map_lookup_elem(&tenant_qos_map, &src_ip);
    int action = TC_ACT_OK;

    if (state) {
        if (state->tier == TIER_L0) {
            // L0 (Victim / Legitimate): Uncapped, reclaims full bandwidth
            state->packets_passed += 1;
            action = TC_ACT_OK;
        } else {
            // L1 / L2: Token-bucket rate enforcement
            __u64 now_ns = start_ns;
            __u64 elapsed_ns = now_ns - state->last_update_ns;

            if (elapsed_ns > 0 && state->rate_bytes_per_sec > 0) {
                __u64 new_tokens = (elapsed_ns * state->rate_bytes_per_sec) / 1000000000ULL;
                state->tokens += new_tokens;
                if (state->tokens > state->max_burst_bytes)
                    state->tokens = state->max_burst_bytes;
                state->last_update_ns = now_ns;
            }

            if (state->tokens >= pkt_len) {
                state->tokens -= pkt_len;
                state->packets_passed += 1;
                action = TC_ACT_OK;
            } else {
                state->packets_dropped += 1;
                action = TC_ACT_SHOT; // Drop packet
            }
        }
        bpf_map_update_elem(&tenant_qos_map, &src_ip, state, BPF_EXIST);
    } else {
        // Default unclassified traffic passes
        action = TC_ACT_OK;
    }

    __u64 duration = bpf_ktime_get_ns() - start_ns;

    // Track BPF update hits & duration for collectors
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
