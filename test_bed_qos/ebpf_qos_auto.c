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
 * ebpf_qos_auto.c - data plane for automatic attacker/victim identification.
 *
 * Same per-CPU token-bucket limiter as ebpf_qos_dynamic.c, plus per-tenant ACCOUNTING so the
 * user-space controller can compute fair shares instead of using a hardcoded victim/attacker list.
 *
 * Two sections (attached by attach_ebpf.sh):
 *   classifier_ingress : attached ONLY to tenant-facing bridge ports, ingress direction.
 *                        Counts every packet exactly once (a packet crosses 2+ hooks in this
 *                        topology, so counting everywhere would double count), then enforces.
 *   classifier_limit   : attached to every other hook (port egress, br-mesh both ways).
 *                        Source-IP limiter only, identical to ebpf_qos_dynamic.c, so the "ip"
 *                        keying mode behaves like the existing qos_dynamic arms.
 *
 * Two identity keys are maintained in parallel; the controller chooses one:
 *   Stage A "ip"   : key = IPv4 source address. Spoofable (hping3 --rand-source).
 *   Stage B "port" : key = ingress ifindex of the tenant's own veth. A tenant cannot change the
 *                    port it is attached to, so this identity cannot be spoofed within the node.
 */
struct rate_limit_entry {
    __u64 rate_bytes_per_sec; // 0 = uncapped
    __u64 max_burst_bytes;
    __u64 tokens;
    __u64 last_update_ns;
    __u64 packets_passed;
    __u64 packets_dropped;
};

struct src_stat {
    __u64 packets;
    __u64 bytes;
};

/* Stage A enforcement, key = source IP (same layout/name as qos_dynamic so tooling is shared). */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_HASH);
    __uint(max_entries, 65536);
    __type(key, __u32);
    __type(value, struct rate_limit_entry);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} percpu_rate_limit_map SEC(".maps");

/* Stage B enforcement, key = ingress ifindex. */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_HASH);
    __uint(max_entries, 256);
    __type(key, __u32);
    __type(value, struct rate_limit_entry);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} port_rate_limit_map SEC(".maps");

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
    __uint(max_entries, 256);
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

/* Packets processed by this program at ALL hooks - same meaning as in qos_dynamic (Hill input H). */
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

/* Token bucket. entry->rate == 0 means uncapped. Returns TC_ACT_OK or TC_ACT_SHOT. */
static __always_inline int apply_limit(struct rate_limit_entry *entry, __u64 now_ns, __u32 pkt_len)
{
    if (entry->rate_bytes_per_sec == 0) {
        entry->packets_passed += 1;
        return TC_ACT_OK;
    }
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
        return TC_ACT_OK;
    }
    entry->packets_dropped += 1;
    return TC_ACT_SHOT;
}

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

static __always_inline void telemetry(__u64 start_ns)
{
    __u64 duration = bpf_ktime_get_ns() - start_ns;
    __u32 zero_key = 0;
    __u64 *update_cnt = bpf_map_lookup_elem(&update_counter_map, &zero_key);
    if (update_cnt)
        *update_cnt += 1;
    __u32 slot = log2l(duration);
    if (slot >= 64) slot = 63;
    __u64 *count = bpf_map_lookup_elem(&lock_latency_hist, &slot);
    if (count)
        *count += 1;
}

/* Parse to the IPv4 header; returns NULL if not IPv4. */
static __always_inline struct iphdr *parse_ip(struct __sk_buff *skb)
{
    void *data_end = (void *)(long)skb->data_end;
    void *data     = (void *)(long)skb->data;
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return 0;
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP)
        return 0;
    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end)
        return 0;
    return iph;
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
    int action = TC_ACT_OK;

    account(&ip_stats, src_ip, pkt_len);
    account(&port_stats, port, pkt_len);

    /* Stage B limiter (identity = tenant port). Charged once, here only. */
    struct rate_limit_entry *pe = bpf_map_lookup_elem(&port_rate_limit_map, &port);
    if (pe)
        action = apply_limit(pe, start_ns, pkt_len);

    /* Stage A limiter (identity = source IP). */
    if (action == TC_ACT_OK) {
        struct rate_limit_entry *ie = bpf_map_lookup_elem(&percpu_rate_limit_map, &src_ip);
        if (ie)
            action = apply_limit(ie, start_ns, pkt_len);
    }

    telemetry(start_ns);
    return action;
}

SEC("classifier_limit")
int limit_only(struct __sk_buff *skb)
{
    struct iphdr *iph = parse_ip(skb);
    if (!iph)
        return TC_ACT_OK;

    __u64 start_ns = bpf_ktime_get_ns();
    __u32 src_ip = iph->saddr;
    int action = TC_ACT_OK;

    struct rate_limit_entry *ie = bpf_map_lookup_elem(&percpu_rate_limit_map, &src_ip);
    if (ie)
        action = apply_limit(ie, start_ns, skb->len);

    telemetry(start_ns);
    return action;
}

char _license[] SEC("license") = "GPL";
