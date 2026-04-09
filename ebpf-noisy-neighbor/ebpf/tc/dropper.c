// SPDX-License-Identifier: GPL-2.0
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/if_xdp.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

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
} dropper_stats_map SEC(".maps");

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

static __always_inline int run_policy(void *data, void *data_end, int pass_code, int drop_code)
{
    __u32 key0 = 0;
    struct stats_rec *stats = bpf_map_lookup_elem(&dropper_stats_map, &key0);
    if (stats)
        stats_inc(&stats->total);

    if (is_noisy_ipv4(data, data_end)) {
        if (stats)
            stats_inc(&stats->dropped);
        return drop_code;
    }

    if (stats)
        stats_inc(&stats->passed);
    return pass_code;
}

SEC("classifier")
int noisy_dropper(struct __sk_buff *skb)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    return run_policy(data, data_end, TC_ACT_OK, TC_ACT_SHOT);
}

SEC("xdp")
int xdp_noisy_dropper(struct xdp_md *ctx)
{
    void *data = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;
    return run_policy(data, data_end, XDP_PASS, XDP_DROP);
}

char __license[] SEC("license") = "GPL";
