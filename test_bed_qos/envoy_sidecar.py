#!/usr/bin/env python3
"""envoy_sidecar.py - a real Envoy sidecar per tenant: the "existing solution" baseline for workload 11.

Istio sidecar mode puts two proxies on every request: the client pod's sidecar (outbound) and the server pod's
sidecar (inbound). This module reproduces that path on the testbed without Kubernetes:

    client app -> 127.0.0.1:OUT (client's Envoy) -> server_ip:IN (server's Envoy) -> 127.0.0.1:APP (server app)

  * HTTP ports use Envoy's HTTP connection manager (L7, like Istio for HTTP); raw TCP ports use tcp_proxy (L4).
  * --concurrency 2 (two worker threads per proxy), as in Istio's published benchmark profile.
  * mtls=True adds mutual TLS between the sidecars (Istio's default) with a throwaway CA made by openssl.
  * No iptables interception: the client addresses its local sidecar explicitly. Attack floods still go straight
    to the server's IP, as UDP floods do in a real mesh.

Ports: a list of (out_port, in_port, app_port, mode) per app, mode "http" or "tcp".
The Envoy binary is looked up as $ENVOY_BIN, then /usr/local/bin/envoy.
"""
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(BASE_DIR, "run", "envoy")
CERT_DIR = os.path.join(RUN_DIR, "certs")
ADMIN_PORT = 9901


def envoy_bin():
    for cand in (os.environ.get("ENVOY_BIN"), "/usr/local/bin/envoy", shutil.which("envoy")):
        if cand and os.path.exists(cand):
            return cand
    raise FileNotFoundError("Envoy binary not found (set ENVOY_BIN or install /usr/local/bin/envoy)")


def make_certs():
    """CA + one leaf certificate (SAN mesh.local) shared by every sidecar. Enough for mTLS cost measurement."""
    os.makedirs(CERT_DIR, exist_ok=True)
    ca, crt, key = (os.path.join(CERT_DIR, f) for f in ("ca.crt", "leaf.crt", "leaf.key"))
    if all(os.path.exists(f) for f in (ca, crt, key)):
        return ca, crt, key
    run = lambda *a: subprocess.run(list(a), check=True, capture_output=True, cwd=CERT_DIR)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365", "-subj", "/CN=testbed-ca",
        "-keyout", "ca.key", "-out", "ca.crt")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=mesh.local", "-keyout", "leaf.key",
        "-out", "leaf.csr")
    with open(os.path.join(CERT_DIR, "san.ext"), "w") as f:
        f.write("subjectAltName=DNS:mesh.local\n")
    run("openssl", "x509", "-req", "-in", "leaf.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
        "-days", "365", "-extfile", "san.ext", "-out", "leaf.crt")
    return ca, crt, key


def _tls(mtls, side):
    if not mtls:
        return None
    ca, crt, key = make_certs()
    common = {"tls_certificates": [{"certificate_chain": {"filename": crt}, "private_key": {"filename": key}}],
              "validation_context": {"trusted_ca": {"filename": ca},
                                     "match_typed_subject_alt_names": [{"san_type": "DNS", "matcher": {"exact": "mesh.local"}}]}}
    if side == "upstream":
        return {"name": "envoy.transport_sockets.tls", "typed_config": {
            "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext",
            "common_tls_context": common, "sni": "mesh.local"}}
    return {"name": "envoy.transport_sockets.tls", "typed_config": {
        "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext",
        "common_tls_context": common, "require_client_certificate": True}}


def _filter(mode, name, cluster):
    if mode == "http":
        return {"name": "envoy.filters.network.http_connection_manager", "typed_config": {
            "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
            "stat_prefix": name, "codec_type": "AUTO",
            "route_config": {"virtual_hosts": [{"name": "all", "domains": ["*"],
                                                "routes": [{"match": {"prefix": "/"},
                                                            "route": {"cluster": cluster, "timeout": "0s"}}]}]},
            "http_filters": [{"name": "envoy.filters.http.router", "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router"}}]}}
    return {"name": "envoy.filters.network.tcp_proxy", "typed_config": {
        "@type": "type.googleapis.com/envoy.extensions.filters.network.tcp_proxy.v3.TcpProxy",
        "stat_prefix": name, "cluster": cluster}}


def _cluster(name, ip, port, transport=None):
    c = {"name": name, "type": "STATIC", "connect_timeout": "2s",
         "load_assignment": {"cluster_name": name, "endpoints": [{"lb_endpoints": [
             {"endpoint": {"address": {"socket_address": {"address": ip, "port_value": port}}}}]}]}}
    if transport:
        c["transport_socket"] = transport
    return c


def config(outbound, inbound, mtls=False):
    """outbound: [(out_port, server_ip, in_port, mode)]; inbound: [(in_port, app_port, mode)]."""
    listeners, clusters = [], []
    for out_port, ip, in_port, mode in outbound:
        name = f"out_{out_port}"
        listeners.append({"name": name, "address": {"socket_address": {"address": "127.0.0.1", "port_value": out_port}},
                          "filter_chains": [{"filters": [_filter(mode, name, f"up_{out_port}")]}]})
        clusters.append(_cluster(f"up_{out_port}", ip, in_port, _tls(mtls, "upstream")))
    for in_port, app_port, mode in inbound:
        name = f"in_{in_port}"
        chain = {"filters": [_filter(mode, name, f"app_{app_port}")]}
        if mtls:
            chain["transport_socket"] = _tls(True, "downstream")
        listeners.append({"name": name, "address": {"socket_address": {"address": "0.0.0.0", "port_value": in_port}},
                          "filter_chains": [chain]})
        clusters.append(_cluster(f"app_{app_port}", "127.0.0.1", app_port))
    return {"admin": {"address": {"socket_address": {"address": "127.0.0.1", "port_value": ADMIN_PORT}}},
            "static_resources": {"listeners": listeners, "clusters": clusters}}


class Sidecars:
    """Start one Envoy per tenant that needs one (client and/or server side); stop() kills them all."""

    def __init__(self, pairs, ports, cpus=None, mtls=False, log_dir=None):
        self.pairs, self.ports, self.cpus, self.mtls = pairs, ports, cpus, mtls
        self.log_dir = log_dir or RUN_DIR
        self.procs = {}

    def _plan(self):
        plan = {}
        for cli, srv in self.pairs:
            o = plan.setdefault(cli.name, {"t": cli, "out": [], "in": []})
            for out_port, in_port, _app, mode in self.ports:
                o["out"].append((out_port, srv.ip, in_port, mode))
            i = plan.setdefault(srv.name, {"t": srv, "out": [], "in": []})
            for _out, in_port, app_port, mode in self.ports:
                if (in_port, app_port, mode) not in i["in"]:
                    i["in"].append((in_port, app_port, mode))
        return plan

    def start(self, timeout=20):
        binary = envoy_bin()
        os.makedirs(RUN_DIR, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        pin = ["taskset", "-c", self.cpus] if self.cpus else []
        for name, p in self._plan().items():
            cfg = os.path.join(RUN_DIR, f"{name}.json")
            with open(cfg, "w") as f:
                json.dump(config(p["out"], p["in"], self.mtls), f)
            log = open(os.path.join(self.log_dir, f"envoy_{name}.log"), "w")
            self.procs[name] = (p["t"], subprocess.Popen(
                ["ip", "netns", "exec", p["t"].ns, *pin, binary, "-c", cfg, "--concurrency", "2",
                 "--disable-hot-restart", "--log-level", "warn"], stdout=log, stderr=subprocess.STDOUT))

        def ready(item):
            t, proc = item
            end = time.time() + timeout
            while time.time() < end:
                if proc.poll() is not None:
                    return t.name, f"exited rc={proc.returncode}"
                r = subprocess.run(["ip", "netns", "exec", t.ns, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                                    f"http://127.0.0.1:{ADMIN_PORT}/ready"], capture_output=True, text=True)
                if r.stdout.strip() == "200":
                    return t.name, None
                time.sleep(0.2)
            return t.name, "not ready"
        with ThreadPoolExecutor(16) as ex:
            bad = {n: e for n, e in ex.map(ready, self.procs.values()) if e}
        if bad:
            self.stop()
            raise RuntimeError(f"Envoy sidecars failed: {bad}")
        return len(self.procs)

    def stop(self):
        for _t, p in self.procs.values():
            p.kill()
        for _t, p in self.procs.values():
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                pass
        self.procs = {}
