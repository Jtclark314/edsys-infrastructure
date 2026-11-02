#!/usr/bin/env python3
"""Private HTTP/CONNECT egress gateway. No TLS interception or URL logging.

Resolve once, reject the whole result if any address is forbidden, then connect
to an approved numeric address. Every new destination (including redirects,
subresources and WebSockets) gets the same check. Only the private application
network may use this service; it must have no published port.
"""

import ipaddress
import json
import os
import selectors
import socket
import socketserver
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import BoundedSemaphore, Thread, Timer
from urllib.parse import parse_qs, urlencode, urlsplit


DEFAULT_DENIED = (
    "0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4",
    "240.0.0.0/4", "::/128", "::1/128", "fe80::/10", "ff00::/8",
    "fd00:ec2::254/128", "100.100.100.100/32", "100.100.100.200/32", "168.63.129.16/32",
    # Deny address-translation/tunnelling ranges which can conceal an IPv4 target.
    "::/96", "::ffff:0:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48",
    "2002::/16", "2001::/32", "fec0::/10",
)
DENIED_PORTS = {2375, 2376, 6443, 10250, 8141, 8143, 8453}
MAX_BYTES = 128 * 1024 * 1024
MAX_SECONDS = 300
IDLE_SECONDS = 60
DNS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="egress-dns")
DNS_SLOTS = BoundedSemaphore(4)


class Refused(ValueError):
    pass


class DestinationPolicy:
    def __init__(self, own_networks, resolver=socket.getaddrinfo, browser=False,
                 protected_hosts=(), protected_ports=()):
        if not own_networks:
            raise ValueError("Explicit owned networks are required")
        self.denied = tuple(ipaddress.ip_network(n) for n in (*DEFAULT_DENIED, *own_networks))
        self.resolver = resolver
        self.browser = browser
        self.protected_hosts = {ipaddress.ip_address(host) for host in protected_hosts}
        self.protected_ports = {int(port) for port in protected_ports}
        if any(not 1 <= port <= 65535 for port in self.protected_ports):
            raise ValueError("Invalid protected port")

    def resolve(self, hostname, port):
        if (not hostname or len(hostname) > 253 or any(c in hostname for c in "\\%\r\n\t /@")
                or not 1 <= port <= 65535 or port in DENIED_PORTS
                or (self.browser and port in (11434, 11435))
                or (port < 1024 and port not in (80, 443))):
            raise Refused("Destination not allowed")
        try:
            if not DNS_SLOTS.acquire(blocking=False):
                raise Refused("Resolution capacity reached")
            future = DNS_EXECUTOR.submit(self.resolver, hostname, port, type=socket.SOCK_STREAM)
            future.add_done_callback(lambda _: DNS_SLOTS.release())
            answers = future.result(timeout=3)
        except (OSError, FutureTimeout) as exc:
            raise Refused("Destination resolution failed") from exc
        if len(answers) > 16:
            raise Refused("Too many destination addresses")
        approved = []
        for family, socktype, proto, _, address in answers:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            ip = ipaddress.ip_address(address[0])
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                    or (ip in self.protected_hosts and port in self.protected_ports)
                    or any(ip.version == n.version and ip in n for n in self.denied)):
                raise Refused("Destination not allowed")
            entry = family, socktype, proto, address
            if entry not in approved:
                approved.append(entry)
        if not approved:
            raise Refused("Destination resolution failed")
        return approved

    def connect(self, hostname, port):
        # Never resolve hostname again: connect only to the checked sockaddr.
        deadline = time.monotonic() + 10
        for family, socktype, proto, address in self.resolve(hostname, port)[:4]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            connection = socket.socket(family, socktype, proto)
            connection.settimeout(min(3, remaining))
            try:
                connection.connect(address)
                return connection
            except OSError:
                connection.close()
        raise Refused("Destination connection failed")


def stream_pair(client, upstream):
    started = active = time.monotonic()
    transferred = 0
    with selectors.DefaultSelector() as selector:
        selector.register(client, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, client)
        for peer in (client, upstream):
            peer.settimeout(10)
        while time.monotonic() - started < MAX_SECONDS:
            if time.monotonic() - active > IDLE_SECONDS:
                return
            for ready, _ in selector.select(timeout=1):
                block = ready.fileobj.recv(65536)
                if not block:
                    return
                transferred += len(block)
                if transferred > MAX_BYTES:
                    return
                ready.data.sendall(block)
                active = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    # No buffered request body may disappear when forwarding the raw stream.
    rbufsize = 0
    protocol_version = "HTTP/1.1"
    server_version = "PrivateEgress"
    sys_version = ""

    def setup(self):
        self.request.settimeout(15)
        self.expiry = Timer(MAX_SECONDS, self._expire)
        self.expiry.daemon = True
        self.expiry.start()
        super().setup()

    def _expire(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self):
        self.expiry.cancel()
        super().finish()

    def log_message(self, _format, *args):
        pass  # URLs, query strings, headers and credentials must not enter logs.

    def _reply(self, status):
        payload = ("Egress request refused\n" if status != 200 else "ready\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def _source_allowed(self):
        ip = ipaddress.ip_address(self.client_address[0])
        return any(ip.version == n.version and ip in n for n in self.server.allowed)

    def _forward(self, tunnel=False):
        self.close_connection = True
        upstream = None
        begun = False
        try:
            if not self._source_allowed():
                self._reply(403)
                return
            if self.path == "/health" and self.command == "GET":
                self._reply(200)
                return
            cdp = self.path.startswith("/") and urlsplit(self.path).path == "/browser-cdp"
            if cdp:
                if (self.command != "GET" or self.server.role != "app"
                        or self.client_address[0] != self.server.app_address
                        or self.headers.get("Upgrade", "").lower() != "websocket"
                        or self.headers.get("Content-Length", "0") != "0"
                        or self.headers.get("Transfer-Encoding")):
                    raise Refused("CDP relay not allowed")
                # No arbitrary Chrome flags, profile paths, logfile paths or URLs.
                query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                for key, value in query.items():
                    if key == "headful" and value in (["true"], ["false"]):
                        continue
                    if key == "timeout" and len(value) == 1 and value[0].isdigit() and 1 <= int(value[0]) <= 300000:
                        continue
                    raise Refused("CDP option not allowed")
                # Native live selector passes its UI lease. Consume it here; the
                # connection still has a fixed hard deadline, never a Chrome flag.
                query.pop("timeout", None)
                target = urlsplit("http://" + self.server.browser_address + ":3000/" +
                                  ("?" + urlencode({k: v[0] for k, v in query.items()}) if query else ""))
                port = 3000
            if sum(len(k) + len(v) + 4 for k, v in self.headers.items()) > 65536:
                raise Refused("Headers too large")
            if tunnel:
                target = urlsplit("//" + self.path)
                if target.path or target.query or target.fragment or target.username is not None:
                    raise Refused("Invalid CONNECT target")
                port = target.port
                if port is None:
                    raise Refused("CONNECT requires a port")
            elif not cdp:
                target = urlsplit(self.path)
                if target.scheme != "http" or target.fragment or target.username is not None:
                    raise Refused("HTTP absolute target required")
                port = target.port or 80
                lengths = self.headers.get_all("Content-Length", [])
                encodings = self.headers.get_all("Transfer-Encoding", [])
                if len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings):
                    raise Refused("Ambiguous message framing")
                if lengths and (not lengths[0].isdigit() or int(lengths[0]) > MAX_BYTES):
                    raise Refused("Invalid message length")
                if encodings and encodings[0].lower().strip() != "chunked":
                    raise Refused("Unsupported transfer encoding")
            if cdp:
                # Fixed literal IPv4 peer, never request-controlled or DNS-resolved.
                upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                upstream.settimeout(10)
                upstream.connect((self.server.browser_address, 3000))
            else:
                upstream = self.server.policy.connect(target.hostname, port)
            if tunnel:
                self.send_response(200, "Connection Established")
                self.end_headers()
            else:
                path = target.path or "/"
                if target.query:
                    path += "?" + target.query
                authority = target.hostname
                if ":" in authority:
                    authority = "[" + authority + "]"
                if port != 80:
                    authority += ":" + str(port)
                websocket = self.headers.get("Upgrade", "").lower() == "websocket"
                fields = [f"{self.command} {path} HTTP/1.1", f"Host: {authority}",
                          "Connection: Upgrade" if websocket else "Connection: close"]
                stripped = {"host", "connection", "proxy-connection", "proxy-authorization", "keep-alive"}
                for name, value in self.headers.items():
                    if name.lower() not in stripped:
                        fields.append(f"{name}: {value}")
                upstream.sendall(("\r\n".join(fields) + "\r\n\r\n").encode("latin-1"))
            begun = True
            self.wfile.flush()
            stream_pair(self.connection, upstream)
        except (Refused, ValueError, OSError, TimeoutError):
            if not begun:
                try:
                    self._reply(403)
                except OSError:
                    pass
        finally:
            if upstream:
                upstream.close()

    def do_CONNECT(self):
        self._forward(tunnel=True)

    def do_GET(self):
        self._forward()

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET


class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 32

    def __init__(self, address, policy, allowed, role="browser", app_address=None, browser_address=None):
        self.policy = policy
        self.allowed = tuple(ipaddress.ip_network(n) for n in allowed)
        self.role = role
        self.app_address = app_address
        self.browser_address = browser_address
        if not self.allowed:
            raise ValueError("Explicit source networks are required")
        self.slots = BoundedSemaphore(32)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass


def main():
    denied = json.loads(os.environ["EGRESS_DENY_NETWORKS"]) + json.loads(os.environ["EGRESS_DENY_HOSTS"])
    protected_hosts = json.loads(os.environ["EGRESS_PROTECTED_HOSTS"])
    protected_ports = json.loads(os.environ["EGRESS_PROTECTED_PORTS"])
    app = str(ipaddress.IPv4Address(os.environ["EGRESS_APP_ADDRESS"]))
    browser = str(ipaddress.IPv4Address(os.environ["EGRESS_BROWSER_ADDRESS"]))
    servers = []
    for role in ("app", "browser"):
        bind = str(ipaddress.IPv4Address(os.environ[f"EGRESS_{role.upper()}_BIND_ADDRESS"]))
        allowed = [app if role == "app" else browser, bind]
        policy = DestinationPolicy(denied, browser=role == "browser",
                                   protected_hosts=protected_hosts, protected_ports=protected_ports)
        servers.append(Server((bind, 3128), policy, allowed, role=role,
                              app_address=app, browser_address=browser))
    Thread(target=servers[1].serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever(poll_interval=0.5)
    finally:
        for server in servers:
            server.server_close()


if __name__ == "__main__":
    main()
