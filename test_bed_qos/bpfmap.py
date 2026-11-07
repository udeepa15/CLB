#!/usr/bin/env python3
"""bpfmap.py - read/write pinned eBPF maps through the bpf(2) syscall (no bpftool subprocess).

Every map operation used to fork /usr/sbin/bpftool and parse its JSON (milliseconds per call). At
100 tenants a controller tick issues dozens of them, so a nominal 200 ms loop silently ran slower.
This module performs the same operations with ctypes: microseconds per call, no dependencies.

    m = BPFMap("/sys/fs/bpf/tc/globals/rate_cfg_map")
    m.update(ip_key("10.0.0.20"), struct.pack("=QQ", rate, burst))
    for key, value in m.items(): ...          # per-CPU maps yield a list of per-CPU values

Supports normal and per-CPU maps: lookup, update, delete, keys, items (batch lookup when the kernel
supports it, else get_next_key iteration). Must run as root (or with CAP_BPF).
"""
import ctypes
import errno
import os
import platform
import socket
import struct

_NR_BPF = {"x86_64": 321, "aarch64": 280, "riscv64": 280, "armv7l": 386, "ppc64le": 361}.get(platform.machine())

BPF_MAP_CREATE, BPF_MAP_LOOKUP_ELEM, BPF_MAP_UPDATE_ELEM, BPF_MAP_DELETE_ELEM, BPF_MAP_GET_NEXT_KEY = 0, 1, 2, 3, 4
BPF_OBJ_GET, BPF_OBJ_GET_INFO_BY_FD, BPF_MAP_LOOKUP_BATCH = 7, 15, 24
BPF_ANY, BPF_NOEXIST, BPF_EXIST = 0, 1, 2
MAP_TYPES = {1: "hash", 2: "array", 5: "percpu_hash", 6: "percpu_array", 9: "lru_hash", 10: "lru_percpu_hash"}
PERCPU_TYPES = {5, 6, 10, 21}
_ATTR_SIZE = 128

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long


def possible_cpus():
    """Number of possible CPUs (per-CPU values are sized by it), e.g. '0-7' -> 8."""
    total = 0
    for part in open("/sys/devices/system/cpu/possible").read().strip().split(","):
        lo, _, hi = part.partition("-")
        total += (int(hi) if hi else int(lo)) - int(lo) + 1
    return total


def _bpf(cmd, attr):
    if _NR_BPF is None:
        raise OSError(errno.ENOSYS, f"bpf syscall number unknown for {platform.machine()}")
    r = _libc.syscall(ctypes.c_long(_NR_BPF), ctypes.c_long(cmd), ctypes.byref(attr), ctypes.c_uint(_ATTR_SIZE))
    if r < 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return r


def _attr():
    return (ctypes.c_ubyte * _ATTR_SIZE)()


def _addr(buf):
    return ctypes.addressof(buf) if buf is not None else 0


def ip_key(ip_str):
    """IPv4 source-address key exactly as the kernel stores iph->saddr (network byte order)."""
    return socket.inet_aton(ip_str)


def ip_from_key(key):
    return socket.inet_ntoa(bytes(key[:4]))


def u32_key(v):
    """Native-endian u32 key (ifindex, array index)."""
    return struct.pack("=I", int(v))


class BPFMap:
    def __init__(self, path):
        self.path = path
        p = ctypes.create_string_buffer(os.fsencode(path))
        attr = _attr()
        struct.pack_into("=QII", attr, 0, ctypes.addressof(p), 0, 0)
        self.fd = _bpf(BPF_OBJ_GET, attr)
        try:
            info = (ctypes.c_ubyte * 80)()
            attr = _attr()
            struct.pack_into("=IIQ", attr, 0, self.fd, len(info), ctypes.addressof(info))
            _bpf(BPF_OBJ_GET_INFO_BY_FD, attr)
            self.type, self.id, self.key_size, self.value_size, self.max_entries, self.map_flags = \
                struct.unpack_from("=6I", info, 0)
        except Exception:
            os.close(self.fd)
            raise
        self.percpu = self.type in PERCPU_TYPES
        self.ncpus = possible_cpus() if self.percpu else 1
        self._stride = (self.value_size + 7) & ~7 if self.percpu else self.value_size
        self._vsize = self._stride * self.ncpus
        self._batch_ok = True

    # ------------------------------------------------------------------ helpers
    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _key_buf(self, key):
        key = bytes(key)
        if len(key) != self.key_size:
            raise ValueError(f"{self.path}: key is {len(key)} bytes, map wants {self.key_size}")
        return (ctypes.c_ubyte * self.key_size).from_buffer_copy(key)

    def _split(self, raw):
        """Raw value buffer -> bytes (normal map) or list of per-CPU bytes."""
        raw = bytes(raw)
        if not self.percpu:
            return raw[:self.value_size]
        return [raw[i * self._stride:i * self._stride + self.value_size] for i in range(self.ncpus)]

    def _elem_op(self, cmd, key_buf, value_buf, flags=0):
        attr = _attr()
        struct.pack_into("=IIQQQ", attr, 0, self.fd, 0, _addr(key_buf), _addr(value_buf), flags)
        return _bpf(cmd, attr)

    # ------------------------------------------------------------------ operations
    def lookup(self, key):
        """Value for `key`, or None if absent. Per-CPU maps return a list (one bytes per CPU)."""
        vbuf = (ctypes.c_ubyte * self._vsize)()
        try:
            self._elem_op(BPF_MAP_LOOKUP_ELEM, self._key_buf(key), vbuf)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return None
            raise
        return self._split(vbuf)

    def update(self, key, value, flags=BPF_ANY):
        """Write `value`. For a per-CPU map, a single bytes value is replicated to every CPU;
        a list gives one value per CPU."""
        if self.percpu:
            vals = value if isinstance(value, (list, tuple)) else [value] * self.ncpus
            if len(vals) != self.ncpus:
                raise ValueError(f"{self.path}: need {self.ncpus} per-CPU values, got {len(vals)}")
            raw = b"".join(bytes(v).ljust(self._stride, b"\0") for v in vals)
        else:
            raw = bytes(value)
        if len(raw) != self._vsize or any(len(bytes(v)) != self.value_size for v in
                                          (value if isinstance(value, (list, tuple)) else [value])):
            raise ValueError(f"{self.path}: value must be {self.value_size} bytes")
        vbuf = (ctypes.c_ubyte * self._vsize).from_buffer_copy(raw)
        self._elem_op(BPF_MAP_UPDATE_ELEM, self._key_buf(key), vbuf, flags)

    def delete(self, key):
        """Delete `key`; returns False if it was absent."""
        try:
            self._elem_op(BPF_MAP_DELETE_ELEM, self._key_buf(key), None)
            return True
        except OSError as e:
            if e.errno == errno.ENOENT:
                return False
            raise

    def keys(self):
        """All keys (get_next_key iteration; bounded so a map changing underneath cannot loop forever)."""
        out, seen, cur = [], set(), None
        nxt = (ctypes.c_ubyte * self.key_size)()
        for _ in range(2 * self.max_entries + 2):
            try:
                self._elem_op(BPF_MAP_GET_NEXT_KEY, cur, nxt)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    break
                raise
            k = bytes(nxt)
            if k not in seen:
                seen.add(k)
                out.append(k)
            cur = (ctypes.c_ubyte * self.key_size).from_buffer_copy(k)
        return out

    def _items_batch(self, chunk=256):
        tok = max(self.key_size, 8)
        in_b, out_b = (ctypes.c_ubyte * tok)(), (ctypes.c_ubyte * tok)()
        keys = (ctypes.c_ubyte * (self.key_size * chunk))()
        vals = (ctypes.c_ubyte * (self._vsize * chunk))()
        res, first = [], True
        while True:
            attr = _attr()
            struct.pack_into("=QQQQIIQQ", attr, 0, 0 if first else ctypes.addressof(in_b),
                             ctypes.addressof(out_b), ctypes.addressof(keys), ctypes.addressof(vals),
                             chunk, self.fd, 0, 0)
            done = False
            try:
                _bpf(BPF_MAP_LOOKUP_BATCH, attr)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    raise
                done = True
            count = struct.unpack_from("=I", attr, 32)[0]
            kb, vb = bytes(keys), bytes(vals)
            for i in range(count):
                res.append((kb[i * self.key_size:(i + 1) * self.key_size],
                            self._split(vb[i * self._vsize:(i + 1) * self._vsize])))
            if done:
                return res
            ctypes.memmove(in_b, out_b, tok)
            first = False

    def items(self):
        """[(key, value)] for the whole map; per-CPU values are lists."""
        if self._batch_ok:
            try:
                return self._items_batch()
            except OSError as e:
                if e.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSPC, 524):  # 524 = ENOTSUPP
                    raise
                self._batch_ok = False
        out = []
        for k in self.keys():
            v = self.lookup(k)
            if v is not None:
                out.append((k, v))
        return out


# ---------------------------------------------------------------- convenience
_cache = {}


def open_pinned(path):
    """Cached BPFMap for a pinned path (reopened if the pin was replaced, e.g. by a re-attach)."""
    m = _cache.get(path)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        if m:
            m.close()
            _cache.pop(path, None)
        raise
    ident = (st.st_ino, st.st_dev)
    if m is None or getattr(m, "_ident", None) != ident:
        if m:
            m.close()
        m = BPFMap(path)
        m._ident = ident
        _cache[path] = m
    return m


def sum_u64(values, offset=0):
    """Sum the u64 at `offset` over a list of per-CPU value bytes (or one bytes value)."""
    if isinstance(values, (bytes, bytearray)):
        values = [values]
    return sum(struct.unpack_from("=Q", v, offset)[0] for v in values)


def create_map(map_type, key_size, value_size, max_entries, flags=0):
    """Create an (unpinned) map and return its fd; used by the self-test."""
    attr = _attr()
    struct.pack_into("=5I", attr, 0, map_type, key_size, value_size, max_entries, flags)
    return _bpf(BPF_MAP_CREATE, attr)
