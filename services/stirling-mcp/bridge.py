"""Private, schema-driven Stirling core REST adapter. No MCP or web listener here."""
from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import ExitStack
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx2 as httpx
from jsonschema import Draft4Validator
from referencing import Registry

VERSION = "0.1.0"
MAX_SCHEMA_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_INPUT_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_INPUT_BYTES = 2 * MAX_INPUT_BYTES
MAX_OUTPUT_BYTES = 2 * MAX_INPUT_BYTES
INLINE_BYTES = 48 * 1024
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


class BridgeError(Exception):
    """Safe, deliberately value-free error for the MCP caller."""


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def local_file(stack: ExitStack, name: str, *, limit: int, secret: bool = False):
    path = Path(name).expanduser()
    if not path.is_absolute():
        raise BridgeError("Local file paths must be absolute.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        handle = stack.enter_context(os.fdopen(fd, "rb"))
        info = os.fstat(handle.fileno())
    except (OSError, ValueError):
        raise BridgeError("An explicitly selected local input could not be opened safely.") from None
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise BridgeError("Inputs must be regular files within the documented size limit.")
    if secret and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise BridgeError("Secret files must be owned by this user with no group or other permissions.")
    return handle, info.st_size, path.name


class BoundedUpload:
    """Keep a file that changes during upload within its originally checked size."""
    def __init__(self, handle, size: int):
        self.handle = handle
        self.size = size

    def read(self, size: int = -1) -> bytes:
        remaining = self.size - self.handle.tell()
        return self.handle.read(max(0, remaining if size < 0 else min(remaining, size)))

    def seek(self, offset: int, whence: int = 0):
        return self.handle.seek(offset, whence)

    def tell(self):
        return self.handle.tell()

    def fileno(self):
        return self.handle.fileno()


def adapted_schema(value: Any) -> Any:
    """OpenAPI 3.0 is based on Draft 4; normalize its nullable extension only."""
    if isinstance(value, list):
        return [adapted_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: adapted_schema(item) for key, item in value.items()}
    if result.pop("nullable", False):
        return {"anyOf": [result, {"type": "null"}]}
    return result


def form_parts(name: str, value: Any, schema: dict | None = None, resolve=lambda item: item):
    """Spring ModelAttribute binding: scalar arrays repeat; objects use indexed fields."""
    schema = resolve(schema or {})
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or any(ch in key for ch in "[]\r\n\x00"):
                raise BridgeError("A form object key cannot be encoded safely.")
            properties = schema.get("properties", {})
            suffix = f".{key}" if properties else f"[{key}]"
            child_schema = properties.get(key, schema.get("additionalProperties", {}))
            yield from form_parts(name + suffix, item, child_schema if isinstance(child_schema, dict) else {}, resolve)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (dict, list)):
                yield from form_parts(f"{name}[{index}]", item, schema.get("items", {}), resolve)
            else:
                yield from form_parts(name, item, schema.get("items", {}), resolve)
    elif value is not None:
        yield name, "true" if value is True else "false" if value is False else str(value)


class Bridge:
    def __init__(self, base_url: str, artifact_dir: str, *, transport=None, document=None,
                 max_output_bytes: int = MAX_OUTPUT_BYTES):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise BridgeError("The operator-configured backend must be an HTTP(S) origin without credentials.")
        self.base_url = base_url.rstrip("/")
        self.artifact_dir = Path(artifact_dir).expanduser()
        if not self.artifact_dir.is_absolute():
            raise BridgeError("The artifact directory must be absolute.")
        self.max_output_bytes = max_output_bytes
        self.client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                   timeout=httpx.Timeout(120, connect=10, pool=10))
        self._document = document
        self._loaded_at = time.monotonic() if document else 0.0
        self._lock = threading.Lock()
        self._operations: dict[str, tuple[str, str, dict]] = {}
        if document:
            self._index()

    def _index(self):
        result = {}
        for path, item in self._document.get("paths", {}).items():
            if (not (path.startswith("/api/v1/") or path == "/robots.txt")
                    or ".." in path or "//" in path or "\\" in path or "?" in path or "#" in path):
                raise BridgeError("The backend catalog contains an unsupported operation path.")
            for method, operation in item.items():
                if method not in METHODS:
                    continue
                operation_id = operation.get("operationId")
                if not operation_id or operation_id in result:
                    raise BridgeError("The backend catalog does not have unique operation identifiers.")
                merged = copy.deepcopy(operation)
                merged["parameters"] = item.get("parameters", []) + merged.get("parameters", [])
                result[operation_id] = (path, method.upper(), merged)
        if not result:
            raise BridgeError("The backend returned an empty operation catalog.")
        self._operations = result

    def _load(self):
        with self._lock:
            if self._document is not None and time.monotonic() - self._loaded_at < 300:
                return
            try:
                with self.client.stream("GET", self.base_url + "/v1/api-docs") as response:
                    if response.status_code != 200:
                        raise BridgeError("The configured backend OpenAPI catalog is unavailable.")
                    chunks, length = [], 0
                    for chunk in response.iter_bytes():
                        length += len(chunk)
                        if length > MAX_SCHEMA_BYTES:
                            raise BridgeError("The backend OpenAPI catalog exceeds its size limit.")
                        chunks.append(chunk)
                    document = json.loads(b"".join(chunks))
                if not str(document.get("openapi", "")).startswith("3.0."):
                    raise BridgeError("Only the qualified OpenAPI 3.0 catalog format is supported.")
                self._document = document
                self._index()
                self._loaded_at = time.monotonic()
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                raise BridgeError("The configured backend catalog could not be read safely.") from None

    def _operation(self, operation_id: str):
        self._load()
        if operation_id not in self._operations:
            raise BridgeError("Unknown operation_id; search the current catalog first.")
        return self._operations[operation_id]

    def _resolve(self, schema: dict) -> dict:
        seen = set()
        while "$ref" in schema:
            reference = schema["$ref"]
            if not reference.startswith("#/components/") or reference in seen:
                raise BridgeError("Unsupported catalog reference; external schemas are never fetched.")
            seen.add(reference)
            value = self._document
            try:
                for token in reference[2:].split("/"):
                    value = value[token.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError):
                raise BridgeError("The backend catalog contains an unresolved reference.") from None
            schema = value
        return schema

    def _validate(self, instance: Any, schema: dict, section: str):
        document = adapted_schema(self._document)
        # These two upstream annotations are date display masks, not valid date regexes.
        metadata = document.get("components", {}).get("schemas", {}).get("MetadataRequest", {})
        for field in ("creationDate", "modificationDate"):
            prop = metadata.get("properties", {}).get(field, {})
            if prop.get("pattern") == "yyyy/MM/dd HH:mm:ss":
                prop["pattern"] = r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$"
        check = {**adapted_schema(schema), "components": document.get("components", {})}
        # Only local refs; never allow jsonschema to retrieve an external URI.
        def refs(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "$ref" and not str(child).startswith("#/components/"):
                        raise BridgeError("External schema references are unsupported.")
                    refs(child)
            elif isinstance(value, list):
                for child in value:
                    refs(child)
        refs(check)
        try:
            def no_retrieval(uri):
                raise BridgeError("External schema retrieval is disabled.")
            for error in Draft4Validator(check, registry=Registry(retrieve=no_retrieval)).iter_errors(instance):
                location = ".".join(str(item) for item in error.absolute_path)[:200] or "body"
                # error.message can contain passwords/document content; never expose it.
                raise BridgeError(f"Invalid {section} at {location}: {error.validator} constraint failed. Consult the exact schema.")
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("The selected backend schema could not be validated safely.") from None

    def _properties(self, schema: dict, depth: int = 0):
        if depth > 16:
            raise BridgeError("Request schema composition is too deeply nested.")
        schema = self._resolve(schema)
        result = dict(schema.get("properties", {}))
        for keyword in ("oneOf", "anyOf", "allOf"):
            for branch in schema.get(keyword, []):
                result.update(self._properties(branch, depth + 1))
        return result

    def operations(self, query: str = "", limit: int = 20, offset: int = 0):
        self._load()
        if len(query) > 256 or not 1 <= limit <= 50 or offset < 0:
            raise BridgeError("Use a query up to 256 characters, limit 1–50, and a nonnegative offset.")
        words = query.casefold().split()
        matches = []
        for operation_id, (path, method, operation) in sorted(self._operations.items()):
            searchable = " ".join([operation_id, path, operation.get("summary", ""),
                                   operation.get("description", ""), *operation.get("tags", [])]).casefold()
            if all(word in searchable for word in words):
                matches.append({"operation_id": operation_id, "method": method, "path": path,
                                "summary": operation.get("summary", "")[:240], "tags": operation.get("tags", [])})
        return {"bridge_version": VERSION, "catalog_operations": len(self._operations),
                "matching_operations": len(matches), "operations": matches[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(matches) else None,
                "note": "Catalog presence does not establish licensing, hardware, or runtime availability. Read the exact schema before execution."}

    def operation_schema(self, operation_id: str):
        path, method, operation = self._operation(operation_id)
        components, pending = {}, [operation]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                if "$ref" in value:
                    reference = value["$ref"]
                    tokens = reference.split("/")
                    if len(tokens) != 4 or tokens[:2] != ["#", "components"]:
                        raise BridgeError("Only local component references are supported.")
                    group, name = tokens[2:]
                    if name not in components.setdefault(group, {}):
                        resolved = self._document["components"][group][name]
                        components[group][name] = resolved
                        pending.append(resolved)
                pending.extend(child for key, child in value.items() if key != "$ref")
        payload = {"operation_id": operation_id, "path": path, "method": method,
                   "operation": operation, "components": components,
                   "bridge_notes": ["Defaults in this schema are descriptive; supply required and operationally important fields explicitly.",
                                    "Metadata creationDate/modificationDate masks are validated as yyyy/MM/dd HH:mm:ss.",
                                    "Use files for binary fields; json_body_file for large JSON requests; secret_files for confidential string fields.",
                                    "asynchronous=true sets Stirling's async query flag on POST. Poll getJobStatus, then getJobResult/getJobFiles/downloadFile; never fetch returned URLs blindly."]}
        encoded = json_bytes(payload)
        return payload if len(encoded) <= INLINE_BYTES else {"operation_id": operation_id, "schema_artifact": self._save_bytes(encoded, "application/json")}

    def _artifact(self, content_type: str, disposition: str = ""):
        root = self.artifact_dir
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise BridgeError("The artifact root must be an owned, nonsymlink private directory (0700).")
        # Never trust Content-Disposition filenames or accept output destinations from the backend.
        media = content_type.split(";", 1)[0].strip().lower()
        extension = {"application/pdf": ".pdf", "application/zip": ".zip", "application/json": ".json",
                     "text/plain": ".txt", "text/html": ".html", "image/jpeg": ".jpg"}.get(media)
        extension = extension or mimetypes.guess_extension(media) or ".bin"
        if extension == ".bin" and disposition:
            header = Message()
            header["Content-Disposition"] = disposition[:4096]
            # Accept only a known extension as a type hint, never a server-supplied path/name.
            hint = Path(header.get_filename() or "").suffix.lower()
            if hint in {".pdf", ".zip", ".json", ".txt", ".csv", ".html", ".md", ".png", ".jpg",
                        ".jpeg", ".tif", ".tiff", ".svg", ".webp", ".doc", ".docx", ".xls", ".xlsx",
                        ".ppt", ".pptx", ".odt", ".rtf", ".epub", ".mobi", ".cbz", ".xps", ".eps", ".ps", ".pcl"}:
                extension = hint
        if not re.fullmatch(r"\.[a-zA-Z0-9]{1,10}", extension):
            extension = ".bin"
        folder = Path(tempfile.mkdtemp(prefix="stirling-", dir=root)).resolve()
        return folder / ("result" + extension)

    def _save_bytes(self, data: bytes, content_type: str):
        path = self._artifact(content_type)
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(data)
        return {"path": str(path), "bytes": len(data), "content_type": content_type,
                "sha256": hashlib.sha256(data).hexdigest()}

    def execute(self, operation_id: str, parameters: dict | None = None, files: dict | None = None,
                path_parameters: dict | None = None, query_parameters: dict | None = None,
                secret_files: dict | None = None, json_body_file: str | None = None,
                asynchronous: bool = False, timeout_seconds: int = 120):
        path, method, operation = self._operation(operation_id)
        if not 1 <= timeout_seconds <= 7200:
            raise BridgeError("timeout_seconds must be between 1 and 7200; prefer async for long operations.")
        parameters = copy.deepcopy(parameters or {})
        files, secret_files = files or {}, secret_files or {}
        path_parameters, query_parameters = path_parameters or {}, query_parameters or {}
        if not all(isinstance(item, dict) for item in (parameters, files, secret_files, path_parameters, query_parameters)):
            raise BridgeError("Parameter groups must be JSON objects.")
        request = self._resolve(operation.get("requestBody", {}))
        content = request.get("content", {})
        media = next((item for item in ("multipart/form-data", "application/json", "application/x-www-form-urlencoded") if item in content), None)
        if content and media is None:
            raise BridgeError("This request content type is not supported by the bridge.")
        body_schema = content.get(media, {}).get("schema", {})
        props = self._properties(body_schema)
        path_defs, query_defs = {}, {}
        for definition in operation.get("parameters", []):
            definition = self._resolve(definition)
            target = {"path": path_defs, "query": query_defs}.get(definition["in"])
            if target is None:
                raise BridgeError("This operation requires an unsupported parameter location.")
            target[definition["name"]] = definition
        if set(path_parameters) != set(path_defs) or set(query_parameters) - set(query_defs):
            raise BridgeError("Path parameters must match the schema exactly; query parameters must be documented.")
        query = []
        for key, definition in path_defs.items():
            value = path_parameters[key]
            self._validate(value, definition.get("schema", {}), "path parameter")
            value = str(value)
            if not value or value in {".", ".."} or any(ch in value for ch in "/\\\r\n\x00"):
                raise BridgeError("Unsafe path parameter.")
            path = path.replace("{" + key + "}", quote(value, safe=""))
        if "{" in path or "}" in path:
            raise BridgeError("Unresolved operation path parameter.")
        for key, definition in query_defs.items():
            if definition.get("required") and key not in query_parameters:
                raise BridgeError("A required query parameter is missing.")
            if key in query_parameters:
                self._validate(query_parameters[key], definition.get("schema", {}), "query parameter")
                query.extend(form_parts(key, query_parameters[key]))
        if asynchronous:
            if method != "POST":
                raise BridgeError("The asynchronous option applies only to POST operations.")
            query.append(("async", "true"))
        with ExitStack() as stack:
            if json_body_file:
                if media != "application/json" or parameters or files or secret_files:
                    raise BridgeError("json_body_file requires a JSON operation and cannot be combined with other body inputs.")
                handle, _, _ = local_file(stack, json_body_file, limit=MAX_JSON_BYTES)
                try:
                    parameters = json.loads(handle.read(MAX_JSON_BYTES + 1))
                except (ValueError, UnicodeError):
                    raise BridgeError("json_body_file is not valid JSON.") from None
            if files and media != "multipart/form-data":
                raise BridgeError("Binary file inputs require a multipart operation.")
            if secret_files and not isinstance(parameters, dict):
                raise BridgeError("Secret fields require an object body.")
            for key, file_path in secret_files.items():
                if key in parameters or key not in props or self._resolve(props[key]).get("type") != "string":
                    raise BridgeError("Secret files must name distinct documented string body fields.")
                handle, _, _ = local_file(stack, file_path, limit=8192, secret=True)
                try:
                    parameters[key] = handle.read(8193).decode("utf-8").removesuffix("\n").removesuffix("\r")
                except UnicodeError:
                    raise BridgeError("Secret field files must contain UTF-8 text.") from None
            if len(json_bytes(parameters)) > MAX_JSON_BYTES:
                raise BridgeError("The JSON request exceeds the documented size limit.")
            combined = copy.deepcopy(parameters)
            if isinstance(parameters, dict):
                for key in parameters:
                    prop = self._resolve(props.get(key, {}))
                    item = self._resolve(prop.get("items", {}))
                    if prop.get("format") == "binary" or item.get("format") == "binary":
                        raise BridgeError("Binary body fields must be provided through files, not parameters.")
            multipart = []
            total_size, file_count = 0, 0
            for key, paths in files.items():
                if key not in props or key in parameters or not isinstance(paths, list) or not paths:
                    raise BridgeError("File fields must name distinct documented binary properties with a nonempty path list.")
                schema = self._resolve(props[key])
                multiple = schema.get("type") == "array"
                binary = self._resolve(schema.get("items", {})) if multiple else schema
                if binary.get("format") != "binary" or (not multiple and len(paths) != 1):
                    raise BridgeError("File counts must match the binary field schema.")
                names = []
                for file_path in paths:
                    file_count += 1
                    if file_count > 256:
                        raise BridgeError("At most 256 input files may be submitted per operation.")
                    handle, size, name = local_file(stack, file_path, limit=MAX_INPUT_BYTES)
                    total_size += size
                    if total_size > MAX_TOTAL_INPUT_BYTES:
                        raise BridgeError("The combined input size exceeds 2 GiB.")
                    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:180] or "input.bin"
                    names.append(safe_name)
                    multipart.append((key, (safe_name, BoundedUpload(handle, size), mimetypes.guess_type(name)[0] or "application/octet-stream")))
                combined[key] = names if multiple else names[0]
            if media:
                if isinstance(combined, dict) and props and set(combined) - set(props):
                    raise BridgeError("Unknown body fields; consult the current operation schema.")
                self._validate(combined, body_schema, "request body")
            elif parameters or files or secret_files or json_body_file:
                raise BridgeError("This operation does not accept a request body.")
            kwargs = {"params": query, "timeout": httpx.Timeout(timeout_seconds, connect=10, pool=10)}
            if media == "multipart/form-data":
                multipart.extend((key, (None, value)) for field, input_value in parameters.items()
                                 for key, value in form_parts(field, input_value, props.get(field, {}), self._resolve))
                # A multipart endpoint with no parameters is still encoded as multipart.
                kwargs["files"] = multipart or [("_bridge_empty", (None, ""))]
            elif media == "application/json":
                kwargs["json"] = parameters
            elif media == "application/x-www-form-urlencoded":
                from urllib.parse import urlencode
                kwargs["content"] = urlencode(list(part for field, value in parameters.items() for part in form_parts(field, value)))
                kwargs["headers"] = {"Content-Type": media}
            try:
                with self.client.stream(method, self.base_url + path, **kwargs) as response:
                    if not 200 <= response.status_code < 300:
                        # Bodies/Location/exception strings may echo passwords or contain hostile URLs.
                        raise BridgeError(f"Backend HTTP {response.status_code}; no redirect was followed and no automatic retry was made. Availability may depend on core features or hardware.")
                    return {"operation_id": operation_id, "http_status": response.status_code,
                            **self._response(response)}
            except httpx.HTTPError:
                raise BridgeError("Backend request failed or timed out. It may already have been accepted; check its job state before retrying. No automatic retry was made.") from None

    def _response(self, response):
        content_type = response.headers.get("content-type", "application/octet-stream")[:200]
        content_length = response.headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > self.max_output_bytes:
            raise BridgeError("The response exceeds the 2 GiB artifact limit; no output was retained.")
        target = self._artifact(content_type, response.headers.get("content-disposition", ""))
        partial = target.with_suffix(target.suffix + ".partial")
        length, digest, small = 0, hashlib.sha256(), bytearray()
        try:
            with partial.open("xb") as handle:
                os.chmod(partial, 0o600)
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if length > self.max_output_bytes:
                        raise BridgeError("The response exceeded the artifact limit; the partial output was removed.")
                    handle.write(chunk)
                    digest.update(chunk)
                    if len(small) <= INLINE_BYTES:
                        small.extend(chunk[:INLINE_BYTES + 1 - len(small)])
                handle.flush()
                os.fsync(handle.fileno())
            # Unique private directory and exclusive creation guarantee no user-file overwrite.
            partial.rename(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            target.parent.rmdir()
            raise
        result = {"artifact": {"path": str(target), "bytes": length, "content_type": content_type,
                               "sha256": digest.hexdigest()}, "data_is_untrusted": True}
        if length <= INLINE_BYTES and ("application/json" in content_type or "+json" in content_type):
            try:
                result["json"] = json.loads(small)
            except (ValueError, UnicodeError):
                pass
        if 300 <= response.status_code < 400:
            raise BridgeError("Redirects are never followed.")
        return result
