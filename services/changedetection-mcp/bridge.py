"""Pinned, private changedetection.io REST adapter. No listener or unrestricted proxy."""
from __future__ import annotations

import copy
import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import httpx2 as httpx
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

VERSION = "0.1.0"
UPSTREAM_VERSION = "0.60.10"
MAX_INPUT = 2 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
INLINE = 24 * 1024
MUTATING_QUERY = {"recheck", "recheck_all", "paused", "muted"}
WRITE_IDS = {"createWatch", "updateWatch", "deleteWatch", "deleteWatchHistory", "createTag", "updateTag", "deleteTag",
             "addNotifications", "replaceNotifications", "deleteNotifications", "importWatches"}
READ_IDS = {"listWatches", "getWatch", "getWatchHistory", "getWatchSnapshot", "getWatchHistoryDiff", "getWatchFavicon",
            "listTags", "getTag", "getNotifications", "searchWatches", "getSystemInfo", "getFullApiSpec"}
PRIVATE_FIELDS = {"headers", "body", "browser_steps", "webdriver_js_execute_code", "notification_urls",
                  "notification_body", "notification_title"}
SECRET_KEY = re.compile(r"password|passphrase|token|api.?key|authorization|cookies?|secret|credential|notification.?urls?|webhook", re.I)
SECRET_FIELDS = PRIVATE_FIELDS | {"optional_value", "last_error", "last_notification_error", "remote_server_reply", "proxy"}
URL_PATTERN = re.compile(r"(?:https?|ftp)://[^\s<>\"']+", re.I)
FORBIDDEN_HOSTS = {"localhost", "metadata", "metadata.google.internal", "instance-data", "host.docker.internal", "gateway.docker.internal"}
FORBIDDEN_IPS = {"100.100.100.200", "168.63.129.16", "fd00:ec2::254"}
# Keep the common policy aligned with docker/changedetection/egress_proxy.py.
DEFAULT_DENIED = (
    "0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "fe80::/10", "ff00::/8", "fd00:ec2::254/128", "100.100.100.200/32",
    "168.63.129.16/32", "::/96", "::ffff:0:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48",
    "2002::/16", "2001::/32", "fec0::/10", "100.100.100.100/32",
)
DENIED_PORTS = {2375, 2376, 6443, 10250, 8141, 8143, 8453}
DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="changedetection-mcp-dns")
DNS_SLOTS = threading.BoundedSemaphore(2)


class BridgeError(Exception):
    """A deliberately value-free error suitable for the MCP caller."""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def image_media(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    raise BridgeError("The native image route did not return supported PNG/JPEG bytes; no content was saved.")


def private_file(path, limit=MAX_INPUT):
    """Read only an explicitly selected, owned private regular file, without following a final symlink."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise BridgeError("Private input paths must be absolute.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise BridgeError("Input files must be owned private regular files with no group/other permissions.")
            if info.st_size > limit:
                raise BridgeError("The private input file exceeds its size limit.")
            result = handle.read(limit + 1)
            if len(result) > limit:
                raise BridgeError("The private input file grew beyond its size limit.")
            return result
    except (OSError, TypeError, ValueError):
        raise BridgeError("The selected private file could not be read safely.") from None


def url_redacted(value):
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if ":" in host:
            host = "[" + host + "]"
        if parts.port:
            host += ":" + str(parts.port)
        path = re.sub(r"(?i)(/(?:token|secret|password|api[-_]?key)/)[^/]+", r"\1[REDACTED]", parts.path)
        return urlunsplit((parts.scheme, host, path, "[REDACTED]" if parts.query else "", "[REDACTED]" if parts.fragment else ""))
    except (ValueError, TypeError):
        return "[REDACTED URL]"


def collect_secrets(value, active=False):
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str) and "authorization" in key.lower():
                scheme, _, token = child.partition(" ")
                if len(token) >= 4:
                    found.add(token)
                if scheme.lower() == "basic":
                    try:
                        decoded = base64.b64decode(token, validate=True).decode()
                        found.update(part for part in decoded.split(":", 1) if len(part) >= 4)
                    except (ValueError, UnicodeError):
                        pass
            if isinstance(child, str) and "cookie" in key.lower():
                found.update(part.partition("=")[2].strip().strip('"') for part in child.split(";")
                             if len(part.partition("=")[2].strip().strip('"')) >= 4)
            found.update(collect_secrets(child, active or key in PRIVATE_FIELDS or bool(SECRET_KEY.search(key))))
    elif isinstance(value, list):
        for item in value:
            found.update(collect_secrets(item, active))
    elif isinstance(value, str):
        if active and len(value) >= 4:
            found.add(value)
        for url in URL_PATTERN.findall(value):
            try:
                parsed = urlsplit(url)
                if parsed.password:
                    found.add(parsed.password)
                if parsed.username:
                    found.add(parsed.username)
            except ValueError:
                pass
    return found


def scrub_text(value, secrets=()):
    for secret in sorted(secrets, key=len, reverse=True):
        if len(secret) >= 4:
            value = value.replace(secret, "[REDACTED]")
    value = URL_PATTERN.sub(lambda match: url_redacted(match.group()), value)
    value = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/=._~-]+", r"\1 [REDACTED]", value)
    value = re.sub(r"(?i)(\b(?:password|api[-_]?key|access[-_]?token|rss[-_]?token|secret)\s*[:=]\s*)[^\s,;<>]+", r"\1[REDACTED]", value)
    return value


def scrub(value, secrets=(), depth=0):
    if depth > 40:
        return "[omitted: nesting limit]"
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            clean_key = scrub_text(str(key), secrets)[:256]
            if key in SECRET_FIELDS or SECRET_KEY.search(str(key)):
                result[clean_key] = {"redacted": True, "present": bool(child),
                                     **({"count": len(child)} if isinstance(child, (list, dict)) else {})}
            else:
                result[clean_key] = scrub(child, secrets, depth + 1)
        return result
    if isinstance(value, list):
        return [scrub(item, secrets, depth + 1) for item in value]
    if isinstance(value, str):
        return scrub_text(value, secrets)
    return value


class Bridge:
    def __init__(self, base_url, key_file, artifact_dir, *, transport=None, resolver=None,
                 deny_origins=(), deny_networks=(), document=None, max_response=MAX_RESPONSE):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise BridgeError("The configured backend must be an HTTP(S) origin without credentials or a path.")
        try:
            backend_ip = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            raise BridgeError("The credential destination must be a qualified numeric local backend address.") from None
        if not backend_ip.is_loopback and str(backend_ip) not in {"10.254.230.2", "10.254.230.18"}:
            raise BridgeError("The credential destination is outside the qualified local candidate/production backend addresses.")
        self.base_url = base_url.rstrip("/")
        self.key_file = key_file
        self.artifact_dir = Path(artifact_dir).expanduser()
        if not self.artifact_dir.is_absolute():
            raise BridgeError("The artifact directory must be absolute.")
        self.max_response = max_response
        self.client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                   timeout=httpx.Timeout(45, connect=10, pool=10))
        self.resolver = resolver or self._resolve_host
        self.deny_origins = {self._origin(self.base_url), *(self._origin(item) for item in deny_origins)}
        self.deny_networks = [ipaddress.ip_network(item) for item in (*DEFAULT_DENIED, *deny_networks)]
        self.native_spec_hash = None
        if document is None:
            spec_bytes = Path(__file__).with_name("api-spec.json").read_bytes()
            binding = json.loads(Path(__file__).with_name("catalog-source.json").read_text())
            if binding.get("upstream_version") != UPSTREAM_VERSION or hashlib.sha256(spec_bytes).hexdigest() != binding.get("api_spec_sha256"):
                raise BridgeError("The pinned API catalog integrity binding failed.")
            document = json.loads(spec_bytes)
            self.native_spec_hash = binding.get("native_full_spec_sha256")
            if not isinstance(self.native_spec_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.native_spec_hash):
                raise BridgeError("The qualified native API schema binding is missing or invalid.")
        self.document = document
        self.operations = {}
        for path, item in self.document["paths"].items():
            for method, operation in item.items():
                if isinstance(operation, dict) and "operationId" in operation:
                    self.operations[operation["operationId"]] = ("/api/v1" + path, method.upper(), operation)
        if set(self.operations) != READ_IDS | WRITE_IDS:
            raise BridgeError("The pinned API catalog does not match the reviewed operation allowlist.")
        self._lock = threading.RLock()
        self._verified_at = 0.0

    @staticmethod
    def _origin(url):
        parts = urlsplit(url)
        return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.port or (443 if parts.scheme == "https" else 80))

    @staticmethod
    def _resolve_host(host, port):
        try:
            if not DNS_SLOTS.acquire(blocking=False):
                raise BridgeError("Target name-resolution capacity is temporarily exhausted.")
            future = DNS_POOL.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
            future.add_done_callback(lambda _: DNS_SLOTS.release())
            answers = future.result(timeout=3)
            if len(answers) > 16:
                raise BridgeError("The target resolves to too many addresses.")
            return {item[4][0] for item in answers}
        except (OSError, UnicodeError, FutureTimeout):
            raise BridgeError("A requested target hostname could not be resolved safely.") from None

    def validate_target(self, url):
        try:
            if not isinstance(url, str) or any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in url):
                raise BridgeError("Control characters and backslashes are not allowed in target URLs.")
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or len(url) > 8192:
                raise BridgeError("Watch targets must be bounded HTTP(S) URLs.")
            hostname = parts.hostname.lower().rstrip(".")
            port = parts.port or (443 if parts.scheme == "https" else 80)
            if (len(hostname) > 253 or any(char in hostname for char in "\\%\r\n\t /@") or not 1 <= port <= 65535
                    or port in DENIED_PORTS or (port < 1024 and port not in {80, 443})):
                raise BridgeError("The target hostname or management port is not allowed.")
            if hostname in FORBIDDEN_HOSTS or hostname.endswith(".localhost") or self._origin(url) in self.deny_origins:
                raise BridgeError("The watch target is a protected local/management endpoint.")
            addresses = self.resolver(hostname, port)
            if not addresses:
                raise BridgeError("The target did not resolve to an address.")
            for address in addresses:
                ip = ipaddress.ip_address(address.split("%", 1)[0])
                ip = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
                if (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                        or str(ip) in FORBIDDEN_IPS or any(ip.version == network.version and ip in network for network in self.deny_networks)):
                    raise BridgeError("The target resolves to a protected local/metadata/management address.")
            return url
        except (ValueError, TypeError):
            raise BridgeError("The target URL is malformed.") from None

    def _key(self):
        try:
            key = private_file(self.key_file, 1024).decode("ascii").strip()
            if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{16,512}", key):
                raise BridgeError("The configured native API credential file has an unsupported format.")
            return key
        except UnicodeError:
            raise BridgeError("The configured native API credential file must contain ASCII text.") from None

    def _request(self, method, path, *, query=(), body=None, text_body=None):
        key = self._key()
        kwargs = {"headers": {"x-api-key": key}, "params": query}
        if body is not None:
            kwargs["json"] = body
        if text_body is not None:
            kwargs["content"] = text_body.encode()
            kwargs["headers"]["Content-Type"] = "text/plain; charset=utf-8"
        try:
            with self.client.stream(method, self.base_url + path, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    raise BridgeError(f"Backend HTTP {response.status_code}; error contents and redirects are suppressed. No automatic retry was made.")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > self.max_response:
                    raise BridgeError("The backend response exceeds the 16 MiB limit.")
                data, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_response:
                        raise BridgeError("The backend response exceeded the 16 MiB limit; no artifact was saved.")
                    data.append(chunk)
                return response.status_code, response.headers.get("content-type", "application/octet-stream")[:200], b"".join(data), key
        except httpx.HTTPError:
            raise BridgeError("The backend request failed or timed out. A mutation may already have been accepted; inspect state before retrying.") from None

    def _json(self, operation_id, uuid=None):
        path, method, _ = self.operations[operation_id]
        if uuid:
            self._uuid(uuid)
            path = path.replace("{uuid}", uuid)
        _, _, body, _ = self._request(method, path)
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise BridgeError("The backend returned unexpected JSON.") from None

    def _verify(self):
        if time.monotonic() - self._verified_at < 60:
            return
        status = self._json("getSystemInfo")
        if status.get("version") != UPSTREAM_VERSION:
            raise BridgeError("Backend version differs from the qualified 0.60.10 API; requalify before execution.")
        if self.native_spec_hash:
            native_spec = self._request("GET", "/api/v1/full-spec")[2]
            if hashlib.sha256(native_spec).hexdigest() != self.native_spec_hash:
                raise BridgeError("Backend API schema differs from the qualified local catalog; requalify before execution.")
        self._verified_at = time.monotonic()

    @staticmethod
    def _uuid(value):
        import uuid
        try:
            if str(uuid.UUID(value)) != value.lower():
                raise ValueError()
        except (ValueError, AttributeError, TypeError):
            raise BridgeError("A canonical watch/tag UUID is required.") from None

    def _resolve(self, value):
        seen = set()
        while "$ref" in value:
            ref = value["$ref"]
            if not ref.startswith("#/components/schemas/") or ref in seen:
                raise BridgeError("Only pinned local schema references are supported.")
            seen.add(ref)
            value = self.document["components"]["schemas"][ref.rsplit("/", 1)[1]]
        return value

    def _properties(self, schema, depth=0):
        if depth > 16:
            raise BridgeError("Schema composition is too deep.")
        schema = self._resolve(schema)
        result = dict(schema.get("properties", {}))
        for keyword in ("allOf", "oneOf", "anyOf"):
            for child in schema.get(keyword, []):
                result.update(self._properties(child, depth + 1))
        return result

    def _validate(self, value, schema):
        def no_retrieve(uri):
            raise BridgeError("External schema retrieval is disabled.")
        check = {**schema, "components": self.document["components"]}
        try:
            for error in Draft202012Validator(check, registry=Registry(retrieve=no_retrieve), format_checker=FormatChecker()).iter_errors(value):
                raise BridgeError(f"Request schema {error.validator} constraint failed; inspect the exact schema. Values are not echoed.")
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("The request could not be validated against the pinned schema.") from None

    def _prepare(self, operation_id, path_parameters, query_parameters, *, writing=False):
        if operation_id not in (WRITE_IDS if writing else READ_IDS):
            raise BridgeError("Operation is not allowed through this tool; use discovery and the correct read/mutation tool.")
        path, method, operation = self.operations[operation_id]
        definitions = operation.get("parameters", [])
        paths = {item["name"]: item for item in definitions if item["in"] == "path"}
        queries = {item["name"]: item for item in definitions if item["in"] == "query"}
        if set(path_parameters) != set(paths):
            raise BridgeError("Path parameters must match the pinned operation exactly.")
        for name, item in paths.items():
            value = path_parameters[name]
            self._validate(value, item.get("schema", {}))
            if name == "uuid":
                self._uuid(value)
            text = str(value)
            if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
                raise BridgeError("Unsafe path parameter.")
            path = path.replace("{" + name + "}", quote(text, safe=""))
        if not writing and MUTATING_QUERY & set(query_parameters):
            raise BridgeError("Mutating GET flags are forbidden in read tools; use changedetection_control.")
        if operation_id == "importWatches":
            for name, schema in self._properties({"$ref": "#/components/schemas/WatchBase"}).items():
                if not schema.get("readOnly"):
                    queries.setdefault(name, {"schema": schema})
        if set(query_parameters) - set(queries):
            raise BridgeError("Unknown query parameter; arbitrary query passthrough is disabled.")
        query = []
        for name, value in query_parameters.items():
            self._validate(value, queries[name].get("schema", {}))
            if isinstance(value, (dict, list)):
                value = encoded(value).decode()
            elif isinstance(value, bool):
                value = "true" if value else "false"
            query.append((name, str(value)))
        return path, method, operation, query

    def catalog(self, query="", kind="all", limit=30, offset=0):
        if len(query) > 256 or kind not in {"all", "read", "mutation"} or not 1 <= limit <= 50 or offset < 0:
            raise BridgeError("Invalid catalog query/pagination bounds.")
        matches = []
        for identifier, (path, method, operation) in sorted(self.operations.items()):
            category = "mutation" if identifier in WRITE_IDS else "read"
            searchable = " ".join((identifier, path, operation.get("summary", ""), operation.get("description", ""))).lower()
            if (kind == "all" or category == kind) and all(word in searchable for word in query.lower().split()):
                matches.append({"operation_id": identifier, "kind": category, "method": method, "path": path,
                                "summary": operation.get("summary", "")[:220]})
        return {"bridge_version": VERSION, "qualified_backend": UPSTREAM_VERSION, "native_operations": len(self.operations),
                "matches": len(matches), "operations": matches[offset:offset + limit],
                "next_offset": offset + limit if len(matches) > offset + limit else None,
                "additional_tools": {"changedetection_control": "Explicit watch/tag pause, resume, mute, unmute and recheck; no recheck_all.",
                                     "changedetection_screenshot": "Fixed local screenshot route after authenticated UUID lookup; private image artifact."},
                "limitations": "Global application settings and interactive browser-step sessions are UI-only. Saved watch/tag browser steps and processor settings use the API."}

    def schema(self, operation_id):
        if operation_id not in self.operations:
            raise BridgeError("Unknown pinned operation identifier.")
        path, method, operation = self.operations[operation_id]
        components, pending = {}, [operation]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                ref = item.get("$ref")
                if ref:
                    if not ref.startswith("#/components/schemas/"):
                        raise BridgeError("Unsupported schema reference.")
                    name = ref.rsplit("/", 1)[1]
                    if name not in components:
                        components[name] = self.document["components"]["schemas"][name]
                        pending.append(components[name])
                pending.extend(child for key, child in item.items() if key != "$ref")
            elif isinstance(item, list):
                pending.extend(item)
        result = {"operation_id": operation_id, "path": path, "method": method, "operation": operation,
                  "components": {"schemas": components}, "bridge_rules": [
                      "Read tools reject every mutating GET flag. Use changedetection_control for recheck/pause/mute.",
                      "New watches/imports default paused and muted. Authority flags refer to authorization already in the user's task.",
                      "Credential-bearing configuration requires a selected private body_file; files cannot supply authority flags.",
                      "Imports forbid credential-bearing query configuration. Import safely, then update individual watches with a private JSON body.",
                      "Read metadata is redacted and may be paginated; never blindly PUT a redacted response back."]}
        raw = encoded(result)
        return result if len(raw) <= INLINE else {"operation_id": operation_id, "schema_artifact": self._save(raw, "application/json")}

    def _save(self, data, media):
        root = self.artifact_dir
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise BridgeError("Artifact root must be an owned nonsymlink private directory (0700).")
        extension = ".json" if "json" in media else ".png" if media.startswith("image/png") else ".jpg" if media.startswith("image/jpeg") else ".ico" if "icon" in media else ".txt" if media.startswith("text/") or "yaml" in media else ".bin"
        folder = Path(tempfile.mkdtemp(prefix="changedetection-", dir=root)).resolve()
        target = folder / ("result" + extension)
        try:
            with target.open("xb") as handle:
                os.chmod(target, 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            folder.rmdir()
            raise
        return {"path": str(target), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "content_type": media}

    def _result(self, operation_id, response, *, secrets=(), limit=50, offset=0):
        status, media, raw, key = response
        known = {*secrets, key}
        result = {"operation_id": operation_id, "http_status": status, "data_is_untrusted": True}
        if not raw:
            return {**result, "data": None}
        if "json" in media:
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                raise BridgeError("Unexpected malformed JSON response; no raw content was retained.") from None
            known.update(collect_secrets(value))
            value = scrub(value, known)
            if operation_id in {"listWatches", "listTags", "getWatchHistory", "searchWatches"} and isinstance(value, (dict, list)):
                total = len(value)
                if isinstance(value, dict):
                    items = sorted(value.items(), reverse=operation_id == "getWatchHistory")
                    value = dict(items[offset:offset + limit])
                else:
                    value = value[offset:offset + limit]
                result["pagination"] = {"total": total, "offset": offset, "limit": limit, "next_offset": offset + limit if total > offset + limit else None}
            raw = encoded(value)
            if len(raw) <= INLINE:
                result["data"] = value
        elif media.startswith("text/") or "yaml" in media:
            value = scrub_text(raw.decode("utf-8", errors="replace"), known)
            raw = value.encode()
            # HTML remains inert text in both context and the .txt artifact.
            if len(raw) <= INLINE:
                result["text"] = value
        result["artifact"] = self._save(raw, media)
        return result

    def read(self, operation_id, path_parameters=None, query_parameters=None, limit=50, offset=0):
        if not 1 <= limit <= 200 or offset < 0:
            raise BridgeError("Read pagination is limited to 1–200 items and a nonnegative offset.")
        with self._lock:
            path, method, _, query = self._prepare(operation_id, path_parameters or {}, query_parameters or {})
            self._verify()
            secrets = set()
            watch = None
            if operation_id in {"getWatchSnapshot", "getWatchHistoryDiff"}:
                watch = self._json("getWatch", path_parameters["uuid"])
                secrets = collect_secrets(watch)
                if operation_id == "getWatchHistoryDiff" and watch.get("processor") == "image_ssim_diff":
                    raise BridgeError("Native history diff is text-only. Read selected image snapshots or use the private web UI for the visual comparison.")
            response = self._request(method, path, query=query)
            if operation_id == "getWatchSnapshot" and watch.get("processor") == "image_ssim_diff" and not (query_parameters or {}).get("html"):
                # The native API labels visual history bytes text/plain; preserve the binary artifact.
                response = (response[0], image_media(response[2]), response[2], response[3])
            return self._result(operation_id, response, secrets=secrets, limit=limit, offset=offset)

    def _notifications_possible(self, watch):
        system = self._json("getSystemInfo")
        if system.get("all_muted") or watch.get("notification_muted"):
            return False
        if watch.get("notification_urls"):
            return True
        for tag_id in watch.get("tags") or []:
            tag = self._json("getTag", tag_id)
            if tag.get("notification_urls") and not tag.get("notification_muted"):
                return True
        return bool(self._json("getNotifications").get("notification_urls"))

    def _watch_tag_ids(self, watch):
        """Resolve native create/import aliases before evaluating inherited effects."""
        ids = set(watch.get("tags") or [])
        if watch.get("tag_uuids"):
            ids.update(item.strip() for item in watch["tag_uuids"].split(",") if item.strip())
        if watch.get("tag"):
            listing = self._json("listTags")
            for name in watch["tag"].split(","):
                name = name.strip()
                if name in listing:
                    ids.add(name)
                else:
                    ids.update(key for key, tag in listing.items() if tag.get("title", "").strip().lower() == name.lower())
        if len(ids) > 100:
            raise BridgeError("A watch can inherit from at most 100 tags through this bridge.")
        for tag_id in ids:
            self._uuid(tag_id)
        return sorted(ids)

    def _authorize_watch(self, watch, *, allow_network_activity, allow_notifications, allow_target_actions):
        if not allow_network_activity:
            raise BridgeError("This action can fetch a target or start recurring checks. Set allow_network_activity only when the current user task authorizes it.")
        self.validate_target(watch.get("url", ""))
        watch = {**watch, "tags": self._watch_tag_ids(watch)}
        inherited = [self._json("getTag", item) for item in watch.get("tags") or []]
        if any(item.get("method", "GET") != "GET" or item.get("browser_steps") or item.get("webdriver_js_execute_code")
               for item in [watch, *inherited]) and not allow_target_actions:
            raise BridgeError("Non-GET fetches, JavaScript and browser steps require allow_target_actions from the current task's authority.")
        if not allow_notifications and self._notifications_possible(watch):
            raise BridgeError("This check could deliver configured notifications. Mute the target first or use allow_notifications only when authorized by the user's task.")

    @staticmethod
    def _private_required(body):
        if not isinstance(body, dict):
            return False
        for key, value in body.items():
            if value and (key in PRIVATE_FIELDS or SECRET_KEY.search(key)):
                return True
            if isinstance(value, str):
                for url in URL_PATTERN.findall(value):
                    try:
                        parts = urlsplit(url)
                        if parts.username or parts.password or parts.fragment or SECRET_KEY.search(parts.query):
                            return True
                    except ValueError:
                        return True
        return False

    def mutate(self, operation_id, path_parameters=None, parameters=None, query_parameters=None,
               body_file=None, text_body_file=None, urls=None, allow_network_activity=False,
               allow_notifications=False, allow_target_actions=False):
        with self._lock:
            if operation_id not in WRITE_IDS:
                raise BridgeError("Unknown or read-only operation; no arbitrary admin/API passthrough is available.")
            body = copy.deepcopy(parameters or {})
            query = copy.deepcopy(query_parameters or {})
            if body_file:
                if parameters or text_body_file or urls:
                    raise BridgeError("body_file cannot be combined with other body inputs.")
                try:
                    body = json.loads(private_file(body_file))
                except (ValueError, UnicodeError):
                    raise BridgeError("The private body file is not valid JSON.") from None
            if not isinstance(body, dict) or not isinstance(query, dict):
                raise BridgeError("Body and query parameters must be JSON objects.")
            if len(encoded(body)) > MAX_INPUT or len(encoded(query)) > MAX_INPUT:
                raise BridgeError("Request parameters exceed the 2 MiB input limit.")
            if not body_file and self._private_required(body):
                raise BridgeError("Credential-bearing headers, body, browser steps, JavaScript and notification settings require a private body_file.")
            self._verify()
            defaults, text_body = {}, None
            if operation_id == "importWatches":
                if body or body_file or (urls and text_body_file):
                    raise BridgeError("Import takes urls or a private text_body_file; configuration belongs in query_parameters.")
                if text_body_file:
                    try:
                        urls = private_file(text_body_file).decode("utf-8").splitlines()
                    except UnicodeError:
                        raise BridgeError("The URL import file must contain UTF-8 text.") from None
                if not isinstance(urls, list) or not urls or len(urls) > 100:
                    raise BridgeError("Import requires an explicit list of 1–100 URLs.")
                if any(not isinstance(url, str) for url in urls):
                    raise BridgeError("Every imported URL must be a string; invalid rows are not silently discarded.")
                urls = [url.strip() for url in urls if isinstance(url, str) and url.strip()]
                if not urls:
                    raise BridgeError("The import contains no URLs.")
                for url in urls:
                    if "\n" in url or "\r" in url:
                        raise BridgeError("Each imported URL must be a single line.")
                    self.validate_target(url)
                    if not text_body_file and self._private_required({"url": url}):
                        raise BridgeError("Credential-bearing URLs require a private import file.")
                if self._private_required(query):
                    raise BridgeError("Sensitive configuration cannot be placed in import query strings. Import paused, then update each watch using a private body_file.")
                for field in ("paused", "notification_muted"):
                    if field not in query:
                        query[field] = defaults[field] = True
                if query.get("notification_muted") is False and not allow_notifications:
                    raise BridgeError("Importing unmuted watches requires allow_notifications from explicit task authority.")
                text_body = "\n".join(urls)
                if not query["paused"]:
                    for url in urls:
                        self._authorize_watch({**query, "url": url}, allow_network_activity=allow_network_activity,
                                              allow_notifications=allow_notifications, allow_target_actions=allow_target_actions)
            elif text_body_file or urls:
                raise BridgeError("URL text inputs are supported only by importWatches.")
            if operation_id == "createWatch":
                for field in ("paused", "notification_muted"):
                    if field not in body:
                        body[field] = defaults[field] = True
                self.validate_target(body.get("url", ""))
                if not body["paused"]:
                    self._authorize_watch(body, allow_network_activity=allow_network_activity,
                                          allow_notifications=allow_notifications, allow_target_actions=allow_target_actions)
            if operation_id in {"addNotifications", "replaceNotifications", "deleteNotifications"} and not allow_notifications:
                raise BridgeError("Notification destination changes require allow_notifications from explicit task authority.")
            if operation_id == "createTag":
                title = str(body.get("title", "")).strip().lower()
                if any(tag.get("title", "").strip().lower() == title for tag in self._json("listTags").values()):
                    raise BridgeError("That tag title already exists; use updateTag with its explicit UUID. Native creation would modify the existing tag.")
            if (body.get("notification_urls") or body.get("notification_muted") is False) and not allow_notifications:
                raise BridgeError("Enabling/configuring notifications requires allow_notifications from explicit task authority.")
            if operation_id in {"updateWatch", "updateTag", "deleteTag"}:
                uuid = (path_parameters or {}).get("uuid")
                self._uuid(uuid)
                current = self._json("getWatch" if operation_id == "updateWatch" else "getTag", uuid)
                merged = {**current, **body}
                if "url" in body:
                    self.validate_target(body["url"])
                if operation_id == "updateWatch" and not merged.get("paused", False):
                    active_fields = set(body) - {"title", "last_viewed", "notification_muted", "notification_urls", "notification_title", "notification_body", "notification_format"}
                    if active_fields:
                        self._authorize_watch(merged, allow_network_activity=allow_network_activity,
                                              allow_notifications=allow_notifications, allow_target_actions=allow_target_actions)
                if operation_id in {"updateTag", "deleteTag"}:
                    # Every tag update clears member checksums. Deletion changes inherited settings.
                    listing = self._json("listWatches")
                    members = [key for key, item in listing.items() if uuid in item.get("tags", [])]
                    if len(members) > 100:
                        raise BridgeError("A tag update is limited to 100 members; use an operator-reviewed larger change.")
                    for member in members:
                        watch = self._json("getWatch", member)
                        if watch.get("paused"):
                            continue
                        if operation_id == "deleteTag":
                            effective = {**watch, "tags": [tag for tag in watch.get("tags", []) if tag != uuid]}
                        else:
                            effective = {**watch, **{key: value for key, value in body.items() if key not in {"url", "title", "tags", "paused"}}}
                        self._authorize_watch(effective, allow_network_activity=allow_network_activity,
                                              allow_notifications=allow_notifications, allow_target_actions=allow_target_actions)
            path, method, operation, query_values = self._prepare(operation_id, path_parameters or {}, query, writing=True)
            request_content = operation.get("requestBody", {}).get("content", {})
            if "application/json" in request_content:
                schema = request_content["application/json"]["schema"]
                properties = self._properties(schema)
                if set(body) - set(properties) or any(properties[key].get("readOnly") for key in body):
                    raise BridgeError("Unknown/read-only body fields are not writable; authority flags cannot be supplied through a body file.")
                self._validate(body, schema)
            elif body:
                raise BridgeError("This operation does not accept a JSON body.")
            response = self._request(method, path, query=query_values, body=body if "application/json" in request_content else None, text_body=text_body)
            result = self._result(operation_id, response, secrets=collect_secrets(body))
            if defaults:
                result["applied_safety_defaults"] = defaults
            return result

    def control(self, target_type, uuid, action, allow_network_activity=False, allow_notifications=False, allow_target_actions=False):
        if target_type not in {"watch", "tag"} or action not in {"pause", "resume", "mute", "unmute", "recheck"}:
            raise BridgeError("Unsupported target or control action.")
        if target_type == "tag" and action in {"pause", "resume"}:
            raise BridgeError("The native API has no tag pause/resume control. Select individual watch UUIDs.")
        self._uuid(uuid)
        with self._lock:
            self._verify()
            current = self._json("getWatch" if target_type == "watch" else "getTag", uuid)
            if action in {"resume", "recheck"}:
                watches = [current]
                if target_type == "tag":
                    listing = self._json("listWatches")
                    members = [key for key, value in listing.items() if uuid in value.get("tags", [])]
                    if len(members) > 100:
                        raise BridgeError("A group control is limited to 100 explicitly selected tag members.")
                    watches = [self._json("getWatch", item) for item in members]
                    watches = [watch for watch in watches if not watch.get("paused")]
                    if not allow_network_activity:
                        raise BridgeError("Group recheck requires authority for its background network effects.")
                for watch in watches:
                    self._authorize_watch(watch, allow_network_activity=allow_network_activity,
                                          allow_notifications=allow_notifications, allow_target_actions=allow_target_actions)
            if action == "unmute" and not allow_notifications:
                raise BridgeError("Unmuting requires allow_notifications from the current user task.")
            query = {"pause": ("paused", "paused"), "resume": ("paused", "unpaused"), "mute": ("muted", "muted"),
                     "unmute": ("muted", "unmuted"), "recheck": ("recheck", "true")}[action]
            response = self._request("GET", "/api/v1/" + target_type + "/" + uuid, query=[query])
            return self._result(target_type + ":" + action, response, secrets=collect_secrets(current))

    def screenshot(self, uuid, error=False):
        self._uuid(uuid)
        with self._lock:
            self._verify()
            self._json("getWatch", uuid)  # The static UI route itself does not enforce the native API key.
            response = self._request("GET", "/static/screenshot/" + uuid, query=[("error_screenshot", "1")] if error else [])
            # Native Playwright may store JPEG bytes in last-screenshot.png and label them image/png.
            return self._result("getWatchScreenshot", (response[0], image_media(response[2]), response[2], response[3]))
