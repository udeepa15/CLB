// SPDX-License-Identifier: GPL-2.0
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

/*
 * Adaptive classifier with map-driven control.
 *
 * Identity modes:
 *   0 = IP-based (match source IPv4)
 *   1 = cgroup-based (match packet cgroup id)
 */

struct control_cfg {
    __u32 drop_rate_per_mille; /* 0..1000 */
    __u32 identity_mode;       /* 0=ip, 1=cgroup */
    __u32 noisy_ip_be;         /* network byte-order IPv4 */
    __u32 reserved;
};

struct stats_rec {
    __u64 total;
    __u64 dropped;
    __u64 passed;
};

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct control_cfg);
} control_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __type(key, __u64);
    __type(value, __u32); /* drop_rate_per_mille */
} cgroup_policy_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct stats_rec);
} stats_map SEC(".maps");

static __always_inline int should_drop(__u32 rate_per_mille, struct __sk_buff *skb)
{
    if (rate_per_mille == 0)
        return 0;
    if (rate_per_mille >= 1000)
        return 1;

    /* Deterministic pseudo-random gate based on skb hash. */
    __u32 bucket = skb->hash % 1000;
    return bucket < rate_per_mille;
}

SEC("classifier")
int adaptive_classifier(struct __sk_buff *skb)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    __u32 key0 = 0;

    struct stats_rec *stats = bpf_map_lookup_elem(&stats_map, &key0);
    if (stats)
        __sync_fetch_and_add(&stats->total, 1);

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return TC_ACT_OK;

    if (eth->h_proto != bpf_htons(ETH_P_IP))
        return TC_ACT_OK;

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return TC_ACT_OK;

    struct control_cfg *cfg = bpf_map_lookup_elem(&control_map, &key0);
    if (!cfg)
        return TC_ACT_OK;

    __u32 drop_rate = 0;

    if (cfg->identity_mode == 1) {
        __u64 cgid = bpf_skb_cgroup_id(skb);
        __u32 *cg_rate = bpf_map_lookup_elem(&cgroup_policy_map, &cgid);
        if (cg_rate)
            drop_rate = *cg_rate;
    } else {
        if (cfg->noisy_ip_be != 0 && ip->saddr == cfg->noisy_ip_be)
            drop_rate = cfg->drop_rate_per_mille;
    }

    if (should_drop(drop_rate, skb)) {
        if (stats)
            __sync_fetch_and_add(&stats->dropped, 1);
        return TC_ACT_SHOT;
    }

    if (stats)
        __sync_fetch_and_add(&stats->passed, 1);

    return TC_ACT_OK;
}

char __license[] SEC("license") = "GPL";
