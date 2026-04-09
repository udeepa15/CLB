// SPDX-License-Identifier: GPL-2.0
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/if_xdp.h>
#include <linux/ip.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

/*
 * Approximate software rate-limit policy for noisy source by probabilistic drop.
 * This is deterministic per packet hash and easy to compare experimentally.
 */
struct stats_rec {
    __u64 total;
    __u64 dropped;
    __u64 passed;
};

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct stats_rec);
} rate_limit_stats_map SEC(".maps");

static __always_inline int is_noisy_ipv4(void *data, void *data_end)
{
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return 0;

    if (eth->h_proto != bpf_htons(ETH_P_IP))
        return 0;

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return 0;

    __u32 noisy_ip = bpf_htonl(0x0A000004); /* 10.0.0.4 */
    return ip->saddr == noisy_ip;
}

static __always_inline void stats_inc(__u64 *slot)
{
    if (slot)
        __sync_fetch_and_add(slot, 1);
}

static __always_inline int run_policy(void *data, void *data_end, __u32 hash, int pass_code, int drop_code)
{
    __u32 key0 = 0;
    struct stats_rec *stats = bpf_map_lookup_elem(&rate_limit_stats_map, &key0);
    if (stats)
        stats_inc(&stats->total);

    if (!is_noisy_ipv4(data, data_end)) {
        if (stats)
            stats_inc(&stats->passed);
        return pass_code;
    }

    /* Drop roughly 50% of noisy packets. */
    if ((hash & 1) == 0) {
        if (stats)
            stats_inc(&stats->dropped);
        return drop_code;
    }

    if (stats)
        stats_inc(&stats->passed);
    return pass_code;
}

SEC("classifier")
int noisy_rate_limit(struct __sk_buff *skb)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    return run_policy(data, data_end, skb->hash, TC_ACT_OK, TC_ACT_SHOT);
}

SEC("xdp")
int xdp_noisy_rate_limit(struct xdp_md *ctx)
{
    void *data = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;
    __u32 h = bpf_get_prandom_u32();
    return run_policy(data, data_end, h, XDP_PASS, XDP_DROP);
}

char __license[] SEC("license") = "GPL";
