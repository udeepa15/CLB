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

#include "qos_limiter.h"   /* parse_ip, log2l, qos_telemetry (this program keeps its own limiter) */

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

/*
 * Attachment (attach_ebpf.sh), same as the other QoS programs:
 *   classifier_ingress   : tenant-facing bridge ports, ingress -> every packet charged once, at source
 *   classifier_telemetry : every other hook -> telemetry only
 *
 * The limiter is the original "tiered" design: ONE shared HASH entry per tenant, read-modify-written
 * by every packet without a lock (so concurrent CPUs can lose updates). It is kept as the static
 * baseline from the interim report; only two arithmetic bugs were fixed:
 *   - a freshly seeded entry (last_update_ns == 0) multiplied the whole uptime by the rate (u64 overflow);
 *   - "tokens += elapsed*rate/1e9; last = now" discarded the fractional byte every packet, which at
 *     small caps and high packet rates under-refilled the bucket. last_update now advances only by the
 *     time actually credited.
 */
SEC("classifier_ingress")
int tenant_ingress(struct __sk_buff *skb)
{
    struct iphdr *iph = parse_ip(skb);
    if (!iph)
        return TC_ACT_OK;

    __u64 start_ns = bpf_ktime_get_ns();
    __u32 src_ip = iph->saddr;
    __u32 pkt_len = skb->len;
    int action = TC_ACT_OK;

    struct tenant_qos_state *state = bpf_map_lookup_elem(&tenant_qos_map, &src_ip);
    if (state) {
        if (state->tier == TIER_L0) {
            // L0 (Victim / Legitimate): Uncapped, reclaims full bandwidth
            state->packets_passed += 1;
        } else {
            // L1 / L2: Token-bucket rate enforcement
            __u64 now_ns = start_ns;
            __u64 rate = state->rate_bytes_per_sec;
            if (state->last_update_ns == 0) {
                state->last_update_ns = now_ns;              /* freshly seeded: bucket starts full */
            } else if (rate > 0 && now_ns > state->last_update_ns) {
                __u64 elapsed_ns = now_ns - state->last_update_ns;
                if (elapsed_ns >= NSEC_PER_SEC) {            /* idle >= 1 s: refill to full */
                    state->tokens = state->max_burst_bytes;
                    state->last_update_ns = now_ns;
                } else {
                    __u64 credit = (elapsed_ns * rate) / NSEC_PER_SEC;   /* elapsed < 1e9: no overflow */
                    if (credit) {
                        state->tokens += credit;
                        state->last_update_ns += (credit * NSEC_PER_SEC) / rate;  /* keep the remainder */
                    }
                }
                if (state->tokens > state->max_burst_bytes)
                    state->tokens = state->max_burst_bytes;
            }

            if (state->tokens >= pkt_len) {
                state->tokens -= pkt_len;
                state->packets_passed += 1;
            } else {
                state->packets_dropped += 1;
                action = TC_ACT_SHOT; // Drop packet
            }
        }
        bpf_map_update_elem(&tenant_qos_map, &src_ip, state, BPF_EXIST);
    }

    qos_telemetry(&update_counter_map, &lock_latency_hist, start_ns);
    return action;
}

SEC("classifier_telemetry")
int telemetry_only(struct __sk_buff *skb)
{
    if (!parse_ip(skb))
        return TC_ACT_OK;
    __u64 start_ns = bpf_ktime_get_ns();
    qos_telemetry(&update_counter_map, &lock_latency_hist, start_ns);
    return TC_ACT_OK;
}

char _license[] SEC("license") = "GPL";
