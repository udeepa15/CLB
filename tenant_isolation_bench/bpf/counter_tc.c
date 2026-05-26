#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_helpers.h>

struct counter_value {
    __u64 packets;
    __u64 bytes;
};

#ifdef USE_SHARED
#define MAP_ENTRIES 4096
#else
#define MAP_ENTRIES 256
#endif

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, MAP_ENTRIES);
    __type(key, __u32);
    __type(value, struct counter_value);
#ifdef USE_SHARED
    __uint(pinning, LIBBPF_PIN_BY_NAME);
#endif
} packet_count SEC(".maps");

SEC("classifier")
int count_ingress(struct __sk_buff *skb)
{
    __u32 key = skb->ifindex;
    struct counter_value *value;
    struct counter_value init = {
        .packets = 1,
        .bytes = skb->len,
    };

    value = bpf_map_lookup_elem(&packet_count, &key);
    if (value) {
        __sync_fetch_and_add(&value->packets, 1);
        __sync_fetch_and_add(&value->bytes, skb->len);
    } else {
        bpf_map_update_elem(&packet_count, &key, &init, BPF_ANY);
    }

    return TC_ACT_OK;
}

char LICENSE[] SEC("license") = "GPL";
