// SPDX-License-Identifier: GPL-2.0
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

/*
 * Priority strategy:
 * - mark noisy source traffic with lower skb priority
 * - mark victim traffic with higher skb priority
 *
 * Works with qdisc scheduling semantics and remains tc-only.
 */
SEC("classifier")
int noisy_priority(struct __sk_buff *skb)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return TC_ACT_OK;

    if (eth->h_proto != bpf_htons(ETH_P_IP))
        return TC_ACT_OK;

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return TC_ACT_OK;

    __u32 noisy_ip = bpf_htonl(0x0A000004); /* 10.0.0.4 */
    if (ip->saddr == noisy_ip) {
        skb->priority = 1;
    } else {
        skb->priority = 5;
    }

    return TC_ACT_OK;
}

char __license[] SEC("license") = "GPL";
