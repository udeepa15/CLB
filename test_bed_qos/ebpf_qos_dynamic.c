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
 * ebpf_qos_dynamic.c - data plane of the qos_static, qos_dynamic_*, qos_markov* and qos_open arms.
 *
 * Identity: IPv4 source address. Limiter: qos_limit() in qos_limiter.h (exact per-tenant GCRA
 * bucket; config written by user space, state owned by the kernel).
 *
 * Attachment (attach_ebpf.sh):
 *   classifier_ingress   : tenant-facing bridge ports, ingress. Enforces, so every packet is charged
 *                          exactly once, at its source (previously every hook enforced, so a packet
 *                          crossing two hooks was charged twice and the effective cap was halved).
 *   classifier_telemetry : every other hook. Counts packets only (controllers' signal H).
 *
 * qos_open uses this program with an empty rate_cfg_map: same data plane, nothing is limited.
 */

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

/* Histogram of this program's run time (log2 ns buckets). */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 64);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} lock_latency_hist SEC(".maps");

/* Packets processed at all hooks (the controllers' contention signal H). */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} update_counter_map SEC(".maps");

SEC("classifier_ingress")
int tenant_ingress(struct __sk_buff *skb)
{
    struct iphdr *iph = parse_ip(skb);
    if (!iph)
        return TC_ACT_OK;
    __u64 start_ns = bpf_ktime_get_ns();
    int action = qos_limit(&rate_cfg_map, &rate_state_map, &rate_cnt_map, iph->saddr, start_ns, skb->len);
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
