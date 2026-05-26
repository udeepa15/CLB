#include <linux/bpf.h>
#include <linux/in.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#define REDIS_PORT 6379
#define CLIENT_KEY 0
#define SERVER_KEY 1

struct {
    __uint(type, BPF_MAP_TYPE_SOCKMAP);
    __uint(max_entries, 2);
    __type(key, __u32);
    __type(value, __u32);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} redis_sock_map SEC(".maps");

SEC("sockops")
int bpf_sockmap_ctrl(struct bpf_sock_ops *skops)
{
    __u32 key = 0;
    __u16 lport = bpf_ntohs((__u16)skops->local_port);
    __u16 rport = bpf_ntohs((__u16)skops->remote_port);

    if (skops->op != BPF_SOCK_OPS_ACTIVE_ESTABLISHED_CB &&
        skops->op != BPF_SOCK_OPS_PASSIVE_ESTABLISHED_CB) {
        return 0;
    }

    if (lport == REDIS_PORT) {
        key = SERVER_KEY;
    } else if (rport == REDIS_PORT) {
        key = CLIENT_KEY;
    } else {
        return 0;
    }

    bpf_sock_map_update(skops, &redis_sock_map, &key, BPF_ANY);
    return 0;
}

SEC("sk_msg")
int bpf_redis_redirect(struct sk_msg_md *msg)
{
    __u16 lport = bpf_ntohs((__u16)msg->local_port);
    __u16 rport = bpf_ntohs((__u16)msg->remote_port);
    __u32 key;

    if (lport == REDIS_PORT) {
        key = CLIENT_KEY;
    } else if (rport == REDIS_PORT) {
        key = SERVER_KEY;
    } else {
        return SK_PASS;
    }

    return bpf_msg_redirect_map(msg, &redis_sock_map, key, BPF_F_INGRESS);
}

char LICENSE[] SEC("license") = "GPL";
