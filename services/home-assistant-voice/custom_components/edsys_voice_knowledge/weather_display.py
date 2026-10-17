"""Bounded NOAA radar frames for the existing Pi camera-frame transport.

No Home Assistant imports, arbitrary URLs, location lookup or device operations.
Last-Modified describes the source file, not the radar scan time printed on it.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import io
import math
import time
import warnings

from PIL import Image

RADAR_URL = "https://radar.weather.gov/ridge/standard/KGSP_loop.gif"
RADAR_LABEL = "NOAA KGSP radar · timestamps on image"
ERROR = "Weather radar is unavailable."
MAX_DOWNLOAD = 4 * 1024 * 1024
MAX_DIMENSION = 1024
MAX_FRAMES = 20
# Retain at most 8M RGB pixels, plus Pillow's bounded frame/work buffers.
MAX_TOTAL_PIXELS = 8_000_000
MAX_JPEG = 32_000
MAX_BASE64 = (MAX_JPEG + 2) // 3 * 4
MAX_TOTAL_JPEG = MAX_JPEG * MAX_FRAMES
MAX_TOTAL_BASE64 = MAX_BASE64 * MAX_FRAMES
MAX_AGE_SECONDS = 20 * 60
MAX_FUTURE_SECONDS = 5 * 60
TIMEOUT_SECONDS = 10
QUALITIES = (85, 75, 65, 55, 45, 35, 25)
SCALES = (1.0, 0.96, 0.9, 0.8, 0.7, 0.6, 0.5)


def _timestamp(value):
    value = time.time() if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(ERROR)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(ERROR)
    # Also bound conversions used in returned UTC metadata.
    datetime.fromtimestamp(value, timezone.utc)
    return value


def _fresh_modified(modified, now):
    if not isinstance(modified, datetime) or modified.utcoffset() is None:
        raise ValueError(ERROR)
    modified = modified.astimezone(timezone.utc)
    age = now - modified.timestamp()
    if not math.isfinite(age) or not -MAX_FUTURE_SECONDS <= age <= MAX_AGE_SECONDS:
        raise ValueError(ERROR)
    return modified


async def download_radar(session, now_timestamp=None):
    """Return (GIF bytes, aware UTC Last-Modified), with no image decoding.

    The caller provides its existing aiohttp session. Redirects and automatic
    content decompression are disabled; the response is read in bounded chunks.
    Cancellation propagates. Other failures disclose no provider response text.
    """
    try:
        # Reject invalid supplied clocks before making a request.
        if now_timestamp is not None:
            _timestamp(now_timestamp)
        async with asyncio.timeout(TIMEOUT_SECONDS):
            async with session.get(
                RADAR_URL, allow_redirects=False, auto_decompress=False,
                timeout=TIMEOUT_SECONDS,
                headers={"Accept": "image/gif", "Accept-Encoding": "identity"},
            ) as response:
                if response.status != 200:
                    raise ValueError(ERROR)
                content_type = response.headers.get("Content-Type", "")
                encoding = response.headers.get("Content-Encoding", "identity")
                if (content_type.split(";", 1)[0].strip().lower() != "image/gif"
                        or encoding.strip().lower() not in ("", "identity")):
                    raise ValueError(ERROR)
                length = response.headers.get("Content-Length")
                if length is not None and not 0 < int(length) <= MAX_DOWNLOAD:
                    raise ValueError(ERROR)
                header = response.headers.get("Last-Modified", "")
                if not isinstance(header, str) or not 1 <= len(header) <= 128:
                    raise ValueError(ERROR)
                modified = _fresh_modified(parsedate_to_datetime(header), _timestamp(now_timestamp))
                chunks, total = [], 0
                while True:
                    chunk = await response.content.read(min(65_536, MAX_DOWNLOAD - total + 1))
                    if not isinstance(chunk, bytes):
                        raise ValueError(ERROR)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise ValueError(ERROR)
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data.startswith((b"GIF87a", b"GIF89a")):
                    raise ValueError(ERROR)
                modified = _fresh_modified(modified, _timestamp(now_timestamp))
                return data, modified
    except Exception:
        pass
    # Raise outside the handler so even the exception context contains no URL.
    raise ValueError(ERROR) from None


def _gif_layout(data):
    """Preflight the complete GIF container before Pillow allocates any frame.

    Pillow can expand a logical canvas to fit a later image descriptor. Check
    those descriptors too, without changing process-global Pillow limits.
    """
    if (not isinstance(data, bytes) or not 14 <= len(data) <= MAX_DOWNLOAD
            or data[:6] not in (b"GIF87a", b"GIF89a")):
        raise ValueError(ERROR)
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
        raise ValueError(ERROR)
    position = 13

    def skip(count):
        nonlocal position
        if position + count > len(data):
            raise ValueError(ERROR)
        position += count

    def blocks():
        nonlocal position
        while True:
            if position >= len(data):
                raise ValueError(ERROR)
            size = data[position]
            position += 1
            if not size:
                return
            skip(size)

    if data[10] & 0x80:
        skip(3 * (1 << ((data[10] & 7) + 1)))
    frames = 0
    while position < len(data):
        marker = data[position]
        position += 1
        if marker == 0x3B:
            if not frames or position != len(data):
                raise ValueError(ERROR)
            return width, height, frames
        if marker == 0x21:
            skip(1)  # extension label, followed by bounded data subblocks
            blocks()
        elif marker == 0x2C:
            start = position
            skip(9)
            left, top, frame_width, frame_height = (
                int.from_bytes(data[start + offset:start + offset + 2], "little")
                for offset in (0, 2, 4, 6)
            )
            if (not frame_width or not frame_height or left + frame_width > width
                    or top + frame_height > height):
                raise ValueError(ERROR)
            frames += 1
            if frames > MAX_FRAMES or width * height * frames > MAX_TOTAL_PIXELS:
                raise ValueError(ERROR)
            packed = data[start + 8]
            if packed & 0x80:
                skip(3 * (1 << ((packed & 7) + 1)))
            if position >= len(data) or not 2 <= data[position] <= 8:
                raise ValueError(ERROR)
            skip(1)  # LZW code size; Pillow validates the compressed pixels
            blocks()
        else:
            raise ValueError(ERROR)
    raise ValueError(ERROR)


def _encode_frames(images):
    """Use one whole-image size for the loop, retaining full resolution first."""
    original = images[0].size
    for scale in SCALES:
        size = tuple(max(1, round(dimension * scale)) for dimension in original)
        frames, total_bytes, total_base64 = [], 0, 0
        for image in images:
            bounded = image if size == original else image.resize(size, Image.Resampling.LANCZOS)
            try:
                raw = None
                for quality in QUALITIES:
                    output = io.BytesIO()
                    # Fresh RGB images have no EXIF, ICC, comments or source tags.
                    bounded.save(output, format="JPEG", quality=quality, optimize=True)
                    if 100 <= output.tell() <= MAX_JPEG:
                        raw = output.getvalue()
                        break
                if raw is None:
                    break
                encoded = base64.b64encode(raw).decode("ascii")
                total_bytes += len(raw)
                total_base64 += len(encoded)
                if (len(encoded) > MAX_BASE64 or total_bytes > MAX_TOTAL_JPEG
                        or total_base64 > MAX_TOTAL_BASE64):
                    raise ValueError(ERROR)
                frames.append(encoded)
            finally:
                if bounded is not image:
                    bounded.close()
        if len(frames) == len(images):
            return frames, size
    raise ValueError(ERROR)


def decode_radar(data, modified, now_timestamp=None):
    """Return bounded JPEG frames and file-freshness metadata; no I/O calls.

    Run this CPU work in an executor. Images retain their full geographic canvas,
    legend, attribution and printed scan timestamps; resizing never crops them.
    """
    images = []
    try:
        now = _timestamp(now_timestamp)
        modified = _fresh_modified(modified, now)
        width, height, count = _gif_layout(data)
        durations = []
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format != "GIF" or source.size != (width, height):
                    raise ValueError(ERROR)
                for index in range(count):
                    source.seek(index)
                    if source.size != (width, height):
                        raise ValueError(ERROR)
                    source.load()
                    rgb = source.convert("RGB")
                    try:
                        clean = Image.new("RGB", source.size)
                        clean.paste(rgb)
                    finally:
                        rgb.close()
                    images.append(clean)
                    durations.append(min(2_000, max(100, int(source.info.get("duration", 200)))))
        frames, size = _encode_frames(images)
        return {
            "label": RADAR_LABEL, "frames": frames, "durations_ms": durations,
            "source_modified": modified.isoformat(),
            "fetched_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "width": size[0], "height": size[1],
            "source_width": width, "source_height": height,
        }
    except Exception:
        pass
    finally:
        for image in images:
            image.close()
    raise ValueError(ERROR) from None


async def fetch_radar(session, now_timestamp=None):
    """Convenience API; perform Pillow work off the event loop.

    A Home Assistant caller can instead compose download_radar with its own
    async_add_executor_job(decode_radar, data, modified, now_timestamp).
    """
    try:
        data, modified = await download_radar(session, now_timestamp)
        return await asyncio.to_thread(decode_radar, data, modified, now_timestamp)
    except Exception:
        pass
    raise ValueError(ERROR) from None
