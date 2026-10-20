"""Fixed Frigate image reads and private, bounded event-clip artifacts.

Only the existing EdSys Frigate origin is reachable. Camera/event identifiers
are checked against fresh service inventory before any media read. Returned
images are untrusted observations; retrieval time does not prove capture time.
"""
from __future__ import annotations

import fcntl
import hashlib
import http.client
import math
import os
import re
import secrets
import stat
import struct
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import operations as ops

IMAGE_MAX_BYTES = 8 * 1024 * 1024
CLIP_MAX_BYTES = 64 * 1024 * 1024
TIMEOUT_SECONDS = 30
CHUNK_BYTES = 64 * 1024
ARTIFACT_ROOT = Path("/home/jeremy/.local/state/edsys-codex-operations/camera-artifacts")
ARTIFACT_MAX_AGE_SECONDS = 24 * 3600
ARTIFACT_MAX_BYTES = 256 * 1024 * 1024
ARTIFACT_MAX_FILES = 8
CAMERA = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
EVENT = re.compile(r"[0-9]{10}(?:\.[0-9]{1,8})?-[a-z0-9]{6}\Z")
ARTIFACT_NAME = re.compile(r"clip-[a-f0-9]{32}\.(?:mp4|part)\Z")
MEDIA_PATH = re.compile(
    r"/api/(?:[A-Za-z0-9_-]{1,80}/latest\.jpg|events/"
    r"[0-9]{10}(?:\.[0-9]{1,8})?-[a-z0-9]{6}/(?:snapshot\.jpg|clip\.mp4))\Z"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _camera_name(value: str) -> str:
    if not isinstance(value, str) or not CAMERA.fullmatch(value):
        raise ops.OperationsError("camera must be an exact name returned by the current Frigate inventory")
    return value


def _event_id(value: str) -> str:
    if not isinstance(value, str) or not EVENT.fullmatch(value):
        raise ops.OperationsError("event_id must be an exact Frigate event ID returned by event discovery")
    return value


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _inventory() -> dict:
    value = ops.request("frigate", "/api/stats")
    if not isinstance(value, dict) or not isinstance(value.get("cameras"), dict):
        raise ops.OperationsError("Frigate camera inventory is unavailable")
    return {name: stats for name, stats in value["cameras"].items()
            if isinstance(name, str) and CAMERA.fullmatch(name) and isinstance(stats, dict)}


def _fps(stats: dict) -> dict:
    return {key: stats[key] if _number(stats.get(key)) and stats[key] >= 0 else None
            for key in ("camera_fps", "process_fps")}


def _event(value: str, kind: str) -> tuple[dict, dict]:
    value = _event_id(value)
    inventory = _inventory()
    event = ops.request("frigate", "/api/events/" + value)
    if not isinstance(event, dict) or event.get("id") != value:
        raise ops.OperationsError("Frigate did not return the requested event")
    camera = _camera_name(event.get("camera"))
    if camera not in inventory:
        raise ops.OperationsError("The event camera is absent from the current Frigate inventory")
    start, end = event.get("start_time"), event.get("end_time")
    if (not _number(start) or not _number(end) or not 0 < start < end <= time.time() + 1
            or event.get("has_" + kind) is not True):
        raise ops.OperationsError("A completed Frigate event with the requested retained media is required")
    return {"event_id": value, "camera": camera, "event_start_time": start,
            "event_end_time": end, "event_duration_seconds": round(end - start, 3)}, _fps(inventory[camera])


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ops.OperationsError("Frigate media read exceeded the 30-second transfer deadline")
    return remaining


def _read(response, size: int, deadline: float) -> bytes:
    """Use the remaining whole-transfer deadline for each blocking socket read."""
    remaining = _remaining(deadline)
    # HTTPResponse closes its buffered socket immediately after the final
    # Content-Length byte. That is an already-complete read, not a transport
    # lacking a deadline-capable socket.
    if getattr(response, "isclosed", lambda: False)() is True:
        return b""
    # urllib's HTTPResponse owns this socket; updating it prevents a slow stream
    # from restarting a fresh 30-second timeout for every chunk. Fail closed on
    # an unexpected transport implementation rather than silently losing the cap.
    try:
        response.fp.raw._sock.settimeout(remaining)
    except (AttributeError, OSError):
        raise ops.OperationsError("Frigate media transport cannot enforce its transfer deadline") from None
    chunk = response.read1(size)
    _remaining(deadline)
    return chunk


@contextmanager
def _media(path: str, maximum: int, mime_types: set[str]):
    if not isinstance(path, str) or not MEDIA_PATH.fullmatch(path):
        raise ops.OperationsError("Unsupported fixed Frigate media route")
    # This addition is deliberately tied to the already approved origin. A
    # future origin change needs review rather than turning this into an SSRF
    # primitive through a mutable base mapping.
    if ops.BASES.get("frigate") != "http://192.168.50.50:5000":
        raise ops.OperationsError("The approved Frigate origin has changed")
    url = ops.BASES["frigate"] + path
    request = urllib.request.Request(url, method="GET", headers={
        "Accept": ", ".join(sorted(mime_types)), "Accept-Encoding": "identity", "Cache-Control": "no-cache"})
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), ops.NoRedirect())
        with opener.open(request, timeout=_remaining(deadline)) as response:
            if response.status != 200 or response.geturl() != url:
                raise ops.OperationsError("Frigate media returned an unsupported response or redirect")
            mime = response.headers.get_content_type()
            if mime not in mime_types or response.headers.get("Content-Encoding", "identity") != "identity":
                raise ops.OperationsError("Frigate returned an unsupported media type or encoding")
            raw_length = response.headers.get("Content-Length")
            length = None
            if raw_length is not None:
                if not re.fullmatch(r"[0-9]{1,12}", raw_length):
                    raise ops.OperationsError("Frigate returned an invalid media length")
                length = int(raw_length)
                if not 0 < length <= maximum:
                    raise ops.OperationsError("Frigate media exceeded the bounded read limit or was empty")
            yield response, mime, length, deadline
    except urllib.error.HTTPError as exc:
        raise ops.OperationsError(f"Frigate media returned HTTP {exc.code}; no redirects or credential fallback attempted") from None
    except ops.OperationsError:
        raise
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        raise ops.OperationsError("Frigate media read failed; no alternate route or credentials attempted") from None


def _transfer(response, maximum: int, length: int | None, deadline: float,
              output: BinaryIO) -> tuple[int, str, bytes]:
    size = 0
    digest = hashlib.sha256()
    prefix = b""
    while True:
        chunk = _read(response, min(CHUNK_BYTES, maximum - size + 1), deadline)
        if not chunk:
            break
        if not isinstance(chunk, bytes) or len(chunk) > maximum - size:
            raise ops.OperationsError("Frigate media exceeded the bounded read limit")
        if len(prefix) < 32:
            prefix += chunk[:32 - len(prefix)]
        output.write(chunk)
        size += len(chunk)
        digest.update(chunk)
    if size == 0 or (length is not None and size != length):
        raise ops.OperationsError("Frigate media was empty or incomplete")
    return size, digest.hexdigest(), prefix


def _dimensions(body: bytes, mime: str) -> tuple[int, int]:
    """Validate bounded JPEG/PNG framing and dimensions without pixel expansion."""
    width = height = 0
    if mime == "image/png":
        if (len(body) < 45 or body[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                or body[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            raise ops.OperationsError("Frigate returned an invalid or incomplete PNG image")
        width, height = struct.unpack(">II", body[16:24])
    elif mime == "image/jpeg":
        if not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9"):
            raise ops.OperationsError("Frigate returned an invalid or incomplete JPEG image")
        offset = 2
        saw_scan = False
        while offset + 4 <= len(body):
            if body[offset] != 255:
                break
            while offset < len(body) and body[offset] == 255:
                offset += 1
            if offset >= len(body):
                break
            marker = body[offset]
            offset += 1
            if marker == 0xDA:
                saw_scan = True
                break
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(body):
                break
            length = int.from_bytes(body[offset:offset + 2], "big")
            if length < 2 or offset + length > len(body):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if length < 8:
                    break
                height, width = struct.unpack(">HH", body[offset + 3:offset + 7])
            offset += length
        if not saw_scan:
            raise ops.OperationsError("Frigate returned an unsupported JPEG image")
    if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > 32_000_000:
        raise ops.OperationsError("Frigate image dimensions are invalid or exceed the pixel limit")
    return width, height


def _image(path: str, metadata: dict) -> tuple[bytes, dict]:
    import io
    with _media(path, IMAGE_MAX_BYTES, {"image/jpeg", "image/png"}) as (response, mime, length, deadline):
        output = io.BytesIO()
        size, digest, _ = _transfer(response, IMAGE_MAX_BYTES, length, deadline, output)
        body = output.getvalue()
    width, height = _dimensions(body, mime)
    return body, {**metadata, "mime_type": mime, "size_bytes": size, "sha256": digest,
                  "width": width, "height": height, "obtained_at": _now(),
                  "decode_verified": False, "capture_timestamp_verified": False, "live_feed_verified": False}


def camera_snapshot(camera: str) -> tuple[bytes, dict]:
    """Latest image in memory; no file or recording settings are changed."""
    camera = _camera_name(camera)
    inventory = _inventory()
    if camera not in inventory:
        raise ops.OperationsError("camera is absent from the current Frigate inventory")
    fps = _fps(inventory[camera])
    return _image("/api/" + camera + "/latest.jpg", {"camera": camera, "source": "latest_frame",
                  **fps, "preview_possible": fps["camera_fps"] is None or fps["camera_fps"] <= 0,
                  "freshness_note": "Latest-frame reads can fall back to stored previews; retrieval time and FPS do not prove frame capture time or motion."})


def camera_event_snapshot(event_id: str) -> tuple[bytes, dict]:
    """Snapshot from one completed, currently inventoried camera's event."""
    metadata, fps = _event(event_id, "snapshot")
    return _image("/api/events/" + metadata["event_id"] + "/snapshot.jpg",
                  {**metadata, **fps, "source": "retained_event_snapshot"})


@contextmanager
def _artifact_store():
    """Serialize private artifact creation and prune only our expired files."""
    try:
        ARTIFACT_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
        if ARTIFACT_ROOT.is_symlink() or ARTIFACT_ROOT.resolve() != ARTIFACT_ROOT:
            raise ops.OperationsError("Private camera artifact storage must not use symlinks")
        root_stat = ARTIFACT_ROOT.stat()
        if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != os.getuid():
            raise ops.OperationsError("Private camera artifact storage has an unexpected owner or type")
        os.chmod(ARTIFACT_ROOT, 0o700)
        descriptor = os.open(ARTIFACT_ROOT / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+b") as lock:
            lock_stat = os.fstat(lock.fileno())
            if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.getuid() or lock_stat.st_nlink != 1:
                raise ops.OperationsError("Private camera artifact lock is unsafe")
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            now = time.time()
            size = count = 0
            for path in ARTIFACT_ROOT.iterdir():
                if not ARTIFACT_NAME.fullmatch(path.name):
                    continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise ops.OperationsError("Private camera artifact storage contains an unsafe entry")
                if now - info.st_mtime > ARTIFACT_MAX_AGE_SECONDS:
                    path.unlink()
                else:
                    size += info.st_size
                    count += 1
            if count >= ARTIFACT_MAX_FILES or size + CLIP_MAX_BYTES > ARTIFACT_MAX_BYTES:
                raise ops.OperationsError("Private camera artifact quota is full; review existing clips before retrying")
            yield
    except ops.OperationsError:
        raise
    except OSError:
        raise ops.OperationsError("Private camera artifact storage is unavailable or busy") from None


def camera_clip(event_id: str) -> dict:
    """Download one completed event MP4 into bounded private local storage."""
    metadata, fps = _event(event_id, "clip")
    with _artifact_store():
        stem = "clip-" + secrets.token_hex(16)
        partial = ARTIFACT_ROOT / (stem + ".part")
        destination = ARTIFACT_ROOT / (stem + ".mp4")
        try:
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                with _media("/api/events/" + metadata["event_id"] + "/clip.mp4", CLIP_MAX_BYTES,
                            {"video/mp4"}) as (response, mime, length, deadline):
                    size, digest, prefix = _transfer(response, CLIP_MAX_BYTES, length, deadline, output)
                if len(prefix) < 12 or prefix[4:8] != b"ftyp":
                    raise ops.OperationsError("Frigate returned an unsupported MP4 container")
                output.flush()
                os.fsync(output.fileno())
            os.replace(partial, destination)
            return {**metadata, **fps, "source": "retained_event_clip", "mime_type": mime,
                    "path": str(destination), "size_bytes": size, "sha256": digest, "obtained_at": _now(),
                    "retention_hours": ARTIFACT_MAX_AGE_SECONDS // 3600,
                    "retention_note": "Expired adapter clips are removed on the next clip retrieval; no background cleanup or public upload occurs.",
                    "decode_verified": False, "live_feed_verified": False}
        except ops.OperationsError:
            raise
        except OSError:
            raise ops.OperationsError("Private camera clip could not be written") from None
        finally:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                raise ops.OperationsError("Private partial camera clip cleanup failed") from None
