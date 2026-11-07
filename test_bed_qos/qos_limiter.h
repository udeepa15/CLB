/* qos_limiter.h - helpers shared by ebpf_qos_dynamic.c, ebpf_qos_auto.c and ebpf_qos_tiered.c.
 *
 * Exact per-tenant rate limiter (qos_limit). It replaces the earlier per-CPU token bucket, which
 * had two flaws:
 *
 *  1. User space rewrote the WHOLE bucket every controller tick, which reset tokens to a full burst
 *     (10 MB). A capped tenant therefore got an extra burst/interval on top of its cap (about
 *     50 MB/s at 200 ms polling, 500 MB/s at 20 ms). Now configuration and state live in separate
 *     maps: user space writes only struct rate_cfg (rate, burst); the bucket state is written by
 *     the kernel only.
 *
 *  2. Every CPU had its own bucket, so the effective cap was rate x (number of CPUs carrying the
 *     tenant's traffic). Now each CAPPED tenant has ONE bucket shared by all CPUs and guarded by a
 *     bpf_spin_lock, so the cap is exact. Uncapped tenants (rate 0, i.e. the victims) never take
 *     the lock and never write shared memory: they cost one read-only hash lookup plus a per-CPU
 *     counter increment. Only a capped tenant's own packets can contend for its lock.
 *
 * The bucket is GCRA (virtual scheduling): equivalent to a token bucket of depth `burst` bytes
 * refilled at `rate` bytes/s, but it keeps a single timestamp. That removes the refill
 * multiplication that overflowed after long idle periods and the per-packet rounding loss of the
 * old "tokens += elapsed*rate/1e9; last = now" refill.
 */
#ifndef QOS_LIMITER_H
#define QOS_LIMITER_H

#define NSEC_PER_SEC 1000000000ULL
#define QOS_MAX_BURST (1ULL << 32)   /* 4 GiB; keeps burst * NSEC_PER_SEC below 2^64 */

struct rate_cfg {                 /* written by user space only (the controller / static seed) */
    __u64 rate_bytes_per_sec;     /* 0 = uncapped */
    __u64 max_burst_bytes;
};

struct rate_state {               /* written by the kernel only */
    struct bpf_spin_lock lock;
    __u32 _pad;
    __u64 tat_ns;                 /* GCRA theoretical arrival time */
};

struct rate_counters {            /* per-CPU and cumulative: never reset by user space */
    __u64 packets_passed;
    __u64 packets_dropped;
    __u64 bytes_passed;
    __u64 bytes_dropped;
};

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

/* Parse to the IPv4 header; returns NULL if the packet is not IPv4. */
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

/* Controllers' contention signal H (packets seen at every hook) + this program's run time. */
static __always_inline void qos_telemetry(void *counter_map, void *hist_map, __u64 start_ns)
{
    __u64 duration = bpf_ktime_get_ns() - start_ns;
    __u32 zero_key = 0;
    __u64 *cnt = bpf_map_lookup_elem(counter_map, &zero_key);
    if (cnt)
        *cnt += 1;
    __u32 slot = log2l(duration);
    if (slot >= 64)
        slot = 63;
    __u64 *bucket = bpf_map_lookup_elem(hist_map, &slot);
    if (bucket)
        *bucket += 1;
}

static __always_inline void qos_count(void *cnt_map, __u32 key, int dropped, __u32 len)
{
    struct rate_counters *c = bpf_map_lookup_elem(cnt_map, &key);
    if (!c) {
        struct rate_counters zero = {};
        bpf_map_update_elem(cnt_map, &key, &zero, BPF_NOEXIST);
        c = bpf_map_lookup_elem(cnt_map, &key);
        if (!c)
            return;
    }
    if (dropped) {
        c->packets_dropped += 1;
        c->bytes_dropped += len;
    } else {
        c->packets_passed += 1;
        c->bytes_passed += len;
    }
}

/* Police one packet of tenant `key`. Returns TC_ACT_OK or TC_ACT_SHOT.
 * Keys without a config entry are unmanaged: always passed and not counted. */
static __always_inline int qos_limit(void *cfg_map, void *state_map, void *cnt_map,
                                     __u32 key, __u64 now_ns, __u32 len)
{
    struct rate_cfg *cfg = bpf_map_lookup_elem(cfg_map, &key);
    if (!cfg)
        return TC_ACT_OK;
    __u64 rate = cfg->rate_bytes_per_sec;
    __u64 burst = cfg->max_burst_bytes;
    int drop = 0;

    if (rate) {
        if (burst > QOS_MAX_BURST)
            burst = QOS_MAX_BURST;
        __u64 cost = ((__u64)len * NSEC_PER_SEC) / rate;   /* credit this packet uses, in ns */
        __u64 tau  = (burst * NSEC_PER_SEC) / rate;         /* bucket depth, in ns */

        struct rate_state *st = bpf_map_lookup_elem(state_map, &key);
        if (!st) {
            struct rate_state init = {};
            bpf_map_update_elem(state_map, &key, &init, BPF_NOEXIST);
            st = bpf_map_lookup_elem(state_map, &key);
        }
        if (st) {
            bpf_spin_lock(&st->lock);
            __u64 tat = st->tat_ns;
            if (tat < now_ns)
                tat = now_ns;              /* idle long enough: bucket is full */
            if (tat + cost > now_ns + tau)
                drop = 1;                  /* would exceed the burst: police */
            else
                st->tat_ns = tat + cost;
            bpf_spin_unlock(&st->lock);
        }
        /* if the state map is full we fail open (pass): never drop traffic we cannot account for */
    }
    qos_count(cnt_map, key, drop, len);
    return drop ? TC_ACT_SHOT : TC_ACT_OK;
}

#endif /* QOS_LIMITER_H */
