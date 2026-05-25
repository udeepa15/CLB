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
 * Flow Key definition to identify unique network paths.
 * Used as the hash map key to track connection/flow information.
 */
struct flow_key {
    __be32 src_ip;
    __be32 dst_ip;
    __be16 src_port;
    __be16 dst_port;
    __u8 proto;
};

/* 
 * Flow Stats structure containing statistics.
 * Modifying these values concurrently under heavy multi-core loads
 * induces cache coherence traffic and lock contention.
 */
struct flow_stats {
    __u64 packets;
    __u64 bytes;
};

/* 
 * Shared eBPF Hash Map.
 * Pinned globally under the bpffs to allow shared usage across multiple interfaces.
 */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 65536);
    __type(key, struct flow_key);
    __type(value, struct flow_stats);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} flow_map SEC(".maps");

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

    // Extract basic layer 3 fields
    struct flow_key key = {0};
    key.src_ip = iph->saddr;
    key.dst_ip = iph->daddr;
    key.proto = iph->protocol;

    // Parse layer 4 ports if available
    if (iph->protocol == IPPROTO_TCP) {
        struct tcphdr *tcph = (void *)(iph + 1);
        if ((void *)(tcph + 1) <= data_end) {
            key.src_port = tcph->source;
            key.dst_port = tcph->dest;
        }
    } else if (iph->protocol == IPPROTO_UDP) {
        struct udphdr *udph = (void *)(iph + 1);
        if ((void *)(udph + 1) <= data_end) {
            key.src_port = udph->source;
            key.dst_port = udph->dest;
        }
    }

    /*
     * Simulate Shared Map Contention:
     * - Look up the flow statistic record in the shared hash map.
     * - The lookup (htab_map_lookup_elem) grabs bucket spinlocks for read/write checks.
     * - If found: Increment packet/byte counts atomically.
     * - If not found: Insert new stats record (htab_map_update_elem) which acquires write locks,
     *   simulating high write/lock-contention when packets flow concurrently from multiple cores.
     */
    struct flow_stats *stats = bpf_map_lookup_elem(&flow_map, &key);
    if (stats) {
        __sync_fetch_and_add(&stats->packets, 1);
        __sync_fetch_and_add(&stats->bytes, skb->len);
    } else {
        struct flow_stats new_stats = {
            .packets = 1,
            .bytes = skb->len,
        };
        bpf_map_update_elem(&flow_map, &key, &new_stats, BPF_ANY);
    }

    return TC_ACT_OK;
}

char _license[] SEC("license") = "GPL";
