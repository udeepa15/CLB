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
 * CONTENTION EXPERIMENT: Flow Stats structure.
 * Modifying these values concurrently under heavy multi-core loads
 * induces cache coherence traffic and lock contention.
 */
struct flow_stats {
    __u64 packets;
    __u64 bytes;
};

/*
 * CONTENTION EXPERIMENT: Shared eBPF Hash Map.
 * Key type changed from 5-tuple struct to plain __u32 so that the
 * shared_global_key below (always == 0) forces EVERY packet to land
 * on the identical hash bucket, serialising all CPUs on the same
 * internal htab bucket spinlock.
 * Pinned globally under the bpffs to allow shared usage across
 * multiple interfaces.
 */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 65536);
    __type(key, __u32);
    __type(value, struct flow_stats);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} flow_map SEC(".maps");

/*
 * CONTENTION EXPERIMENT: Static shared key.
 * Every packet uses key == 0 regardless of its actual 5-tuple,
 * guaranteeing all traffic hits the same bucket and contends on
 * the exact same spinlock.
 */
static __u32 shared_global_key = 0;

SEC("classifier")
int mesh_router(struct __sk_buff *skb) {
    void *data_end = (void *)(long)skb->data_end;
    void *data = (void *)(long)skb->data;

    // Check packet boundary for ethernet header
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return TC_ACT_OK;

    // Only inspect IPv4 packets
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP)
        return TC_ACT_OK;

    // Check packet boundary for IP header
    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end)
        return TC_ACT_OK;

    /*
     * CONTENTION EXPERIMENT: Force all packets — attacker AND victim —
     * to use the identical shared_global_key (== 0).  Because every
     * packet maps to the same bucket in the BPF_MAP_TYPE_HASH table,
     * all CPUs must serialise on the *exact same* internal htab bucket
     * spinlock for every bpf_map_lookup_elem / bpf_map_update_elem
     * call.  This deterministically produces measurable p99 tail-latency
     * spikes proportional to the number of competing CPU cores and the
     * aggregate packet rate, without relying on lucky hash collisions
     * from the 5-tuple key path.
     */
    struct flow_stats *stats = bpf_map_lookup_elem(&flow_map, &shared_global_key);
    struct flow_stats updated_stats = {0};

    if (stats) {
        updated_stats.packets = stats->packets + 1;
        updated_stats.bytes   = stats->bytes + skb->len;
    } else {
        updated_stats.packets = 1;
        updated_stats.bytes   = skb->len;
    }

    // Force write-lock acquisition on the shared bucket
    bpf_map_update_elem(&flow_map, &shared_global_key, &updated_stats, BPF_ANY);

    return TC_ACT_OK;
}

char _license[] SEC("license") = "GPL";
