#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_helpers.h>

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, __u32);
    __type(value, __u64);
} packet_count SEC(".maps");

SEC("tc")
int count_ingress(struct __sk_buff *skb)
{
#ifdef PER_IFINDEX_KEY
    __u32 key = skb->ifindex;
#else
    __u32 key = 0;
#endif
    __u64 init = 1;
    __u64 *current = bpf_map_lookup_elem(&packet_count, &key);

    if (current) {
        __sync_fetch_and_add(current, 1);
    } else {
        bpf_map_update_elem(&packet_count, &key, &init, BPF_ANY);
    }

    return TC_ACT_OK;
}

char LICENSE[] SEC("license") = "GPL";
