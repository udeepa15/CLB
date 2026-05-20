#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_helpers.h>

#ifdef USE_SHARED
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, __u32);
    __type(value, __u64);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} shared_counter_map SEC(".maps");
#else
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __type(key, __u32);
    __type(value, __u64);
} tenant_counter_map SEC(".maps");
#endif

SEC("classifier")
int handle_ingress(struct __sk_buff *skb)
{
    __u32 key = skb->ifindex;
    __u64 *val;

#ifdef USE_SHARED
    val = bpf_map_lookup_elem(&shared_counter_map, &key);
    if (val) {
        __sync_fetch_and_add(val, 1);
    } else {
        __u64 one = 1;
        bpf_map_update_elem(&shared_counter_map, &key, &one, BPF_ANY);
    }
#else
    val = bpf_map_lookup_elem(&tenant_counter_map, &key);
    if (val) {
        __sync_fetch_and_add(val, 1);
    } else {
        __u64 one = 1;
        bpf_map_update_elem(&tenant_counter_map, &key, &one, BPF_ANY);
    }
#endif

    return TC_ACT_OK;
}

char LICENSE[] SEC("license") = "GPL";
__u32 VERSION SEC("version") = 0xFFFFFFFE;
