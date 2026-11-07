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

#include "qos_limiter.h"

/*
 * ebpf_qos_auto.c - data plane for automatic attacker/victim identification.
 *
 * Same limiter as ebpf_qos_dynamic.c (qos_limiter.h), plus per-tenant ACCOUNTING so the user-space
 * controller can compute fair shares instead of using a hardcoded victim/attacker list.
 *
 * Two sections (attached by attach_ebpf.sh):
 *   classifier_ingress   : tenant-facing bridge ports, ingress. Counts every packet exactly once
 *                          (a packet crosses 2+ hooks in this topology), then enforces.
 *   classifier_telemetry : every other hook. Telemetry only.
 *
 * Two identity keys are maintained in parallel; the controller chooses one:
 *   Stage A "ip"   : key = IPv4 source address. Spoofable (hping3 --rand-source).
 *   Stage B "port" : key = ingress ifindex of the tenant's own veth. A tenant cannot change the
 *                    port it is attached to, so this identity cannot be spoofed within the node.
 */

struct src_stat {
    __u64 packets;
    __u64 bytes;
};

/* Stage A limiter, key = source IP (same names/layout as ebpf_qos_dynamic.c, so tooling is shared). */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 4096);
    __type(key, __u32);
    __type(value, struct rate_cfg);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} rate_cfg_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 4096);
    __type(key, __u32);
    __type(value, struct rate_state);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} rate_state_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_HASH);
    __uint(max_entries, 4096);
    __type(key, __u32);
    __type(value, struct rate_counters);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} rate_cnt_map SEC(".maps");

/* Stage B limiter, key = ingress ifindex. */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, __u32);
    __type(value, struct rate_cfg);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} port_cfg_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, __u32);
    __type(value, struct rate_state);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} port_state_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_HASH);
    __uint(max_entries, 1024);
    __type(key, __u32);
    __type(value, struct rate_counters);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} port_cnt_map SEC(".maps");

/* Accounting, Stage A. LRU so a spoofed-source flood evicts old entries instead of filling the map. */
struct {
    __uint(type, BPF_MAP_TYPE_LRU_PERCPU_HASH);
    __uint(max_entries, 8192);
    __type(key, __u32);
    __type(value, struct src_stat);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} ip_stats SEC(".maps");

/* Accounting, Stage B. */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_HASH);
    __uint(max_entries, 1024);
    __type(key, __u32);
    __type(value, struct src_stat);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} port_stats SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 64);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} lock_latency_hist SEC(".maps");

/* Packets processed at ALL hooks - same meaning as in ebpf_qos_dynamic.c (Hill input H). */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} update_counter_map SEC(".maps");

static __always_inline void account(void *map, __u32 key, __u32 pkt_len)
{
    struct src_stat *st = bpf_map_lookup_elem(map, &key);
    if (st) {
        st->packets += 1;
        st->bytes += pkt_len;
    } else {
        struct src_stat init = { .packets = 1, .bytes = pkt_len };
        bpf_map_update_elem(map, &key, &init, BPF_NOEXIST);
    }
}

SEC("classifier_ingress")
int tenant_ingress(struct __sk_buff *skb)
{
    struct iphdr *iph = parse_ip(skb);
    if (!iph)
        return TC_ACT_OK;

    __u64 start_ns = bpf_ktime_get_ns();
    __u32 src_ip = iph->saddr;
    __u32 port = skb->ifindex;   /* at ingress: the device the packet arrived on */
    __u32 pkt_len = skb->len;

    account(&ip_stats, src_ip, pkt_len);
    account(&port_stats, port, pkt_len);

    /* Stage B (identity = tenant port), then Stage A (identity = source IP). Each charged once. */
    int action = qos_limit(&port_cfg_map, &port_state_map, &port_cnt_map, port, start_ns, pkt_len);
    if (action == TC_ACT_OK)
        action = qos_limit(&rate_cfg_map, &rate_state_map, &rate_cnt_map, src_ip, start_ns, pkt_len);

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
