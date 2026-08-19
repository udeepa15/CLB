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
 * Stall-Hiding eBPF Limiter Variant (Beeswax SIGCOMM '26 Approximation).
 *
 * DESIGN RATIONALE & STRUCTURAL RESTRUCTURING:
 * To hide cache-miss/memory latency during map lookups, this variant extracts
 * the lookup key (src_ip) as early as possible in the packet flow and issues
 * bpf_map_lookup_elem immediately. Non-essential packet parsing (pkt_len extraction,
 * timestamping, register preparation) is deferred to execute concurrently within the
 * lookup window before dereferencing the pointer (*entry).
 *
 * eBPF VERIFIER LIMITATION NOTE:
 * Given Linux eBPF verifier constraints regarding un-checkpointed memory across
 * helper functions, a literal multi-stage pipeline across independent packets is
 * approximated by maximizing the instruction overlap window within the single-packet
 * program flow.
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
 * Full-path latency histogram (compatibility for collect_ebpf_stats.py).
 */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 64);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} lock_latency_hist SEC(".maps");

/*
 * Limiter-ONLY latency histogram.
 * Brackets ONLY the token-bucket map lookup + decision logic.
 */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 64);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} limiter_only_latency_hist SEC(".maps");

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

    __u64 start_full_ns = bpf_ktime_get_ns();
    __u32 src_ip = iph->saddr;

    // --- ISOLATED STALL-HIDING LIMITER LOGIC START ---
    __u64 start_limiter_ns = bpf_ktime_get_ns();

    // 1. Issue map lookup AS EARLY AS POSSIBLE to initiate memory fetch
    struct rate_limit_entry *entry = bpf_map_lookup_elem(&percpu_rate_limit_map, &src_ip);

    // 2. Overlap Window: Perform independent packet operations while lookup completes
    __u32 pkt_len = skb->len;
    __u64 now_ns = start_limiter_ns;
    int action = TC_ACT_OK;

    // 3. Evaluate lookup result after overlap window
    if (entry) {
        if (entry->rate_bytes_per_sec == 0) {
            entry->packets_passed += 1;
            action = TC_ACT_OK;
        } else {
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
                action = TC_ACT_SHOT;
            }
        }
    }

    __u64 limiter_duration = bpf_ktime_get_ns() - start_limiter_ns;
    // --- ISOLATED LIMITER LOGIC END ---

    __u64 full_duration = bpf_ktime_get_ns() - start_full_ns;

    // Log limiter-only latency histogram
    __u32 lim_slot = log2l(limiter_duration);
    if (lim_slot >= 64) lim_slot = 63;
    __u64 *lim_count = bpf_map_lookup_elem(&limiter_only_latency_hist, &lim_slot);
    if (lim_count) {
        *lim_count += 1;
    }

    // Log full-path latency histogram
    __u32 full_slot = log2l(full_duration);
    if (full_slot >= 64) full_slot = 63;
    __u64 *full_count = bpf_map_lookup_elem(&lock_latency_hist, &full_slot);
    if (full_count) {
        *full_count += 1;
    }

    // Log map update counter
    __u32 zero_key = 0;
    __u64 *update_cnt = bpf_map_lookup_elem(&update_counter_map, &zero_key);
    if (update_cnt) {
        *update_cnt += 1;
    }

    return action;
}

char _license[] SEC("license") = "GPL";
