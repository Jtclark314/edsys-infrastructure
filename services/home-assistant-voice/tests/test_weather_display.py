"""Offline radar download/decode bounds; no HA, network or device calls."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import importlib.util
import io
from pathlib import Path
import traceback
import unittest
from unittest.mock import patch

from PIL import Image

PATH = Path(__file__).parents[1] / "custom_components/edsys_voice_knowledge/weather_display.py"
SPEC = importlib.util.spec_from_file_location("weather_display_fixture", PATH)
weather = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(weather)
NOW = datetime(2026, 10, 17, 5, 0, tzinfo=timezone.utc)


def gif(count=2, size=(40, 30), comment=b"SOURCE COMMENT MUST NOT BE FORWARDED"):
    images = [Image.new("RGB", size, (index * 11 % 256, index * 7 % 256, index * 17 % 256))
              for index in range(count)]
    try:
        output = io.BytesIO()
        images[0].save(output, format="GIF", save_all=True, append_images=images[1:],
                       duration=[200] * count, loop=0, comment=comment, optimize=False)
        return output.getvalue()
    finally:
        for image in images:
            image.close()


def assert_fixed_failure(test, operation):
    try:
        operation()
    except ValueError as error:
        test.assertEqual(str(error), weather.ERROR)
        test.assertIsNone(error.__cause__)
        test.assertIsNone(error.__context__)
        test.assertNotIn("example.invalid", "".join(traceback.format_exception(error)))
    else:
        test.fail("Expected fixed radar failure")


class DecodeChecks(unittest.TestCase):
    def test_full_canvas_frames_and_metadata_without_source_tags(self):
        result = weather.decode_radar(gif(), NOW - timedelta(minutes=1), NOW.timestamp())
        self.assertEqual(result["label"], "NOAA KGSP radar · timestamps on image")
        self.assertEqual((result["width"], result["height"]), (40, 30))
        self.assertEqual((result["source_width"], result["source_height"]), (40, 30))
        self.assertEqual(result["durations_ms"], [200, 200])
        self.assertEqual(result["source_modified"], (NOW - timedelta(minutes=1)).isoformat())
        self.assertEqual(result["fetched_at"], NOW.isoformat())
        self.assertEqual(len(result["frames"]), 2)
        total = 0
        for encoded in result["frames"]:
            raw = base64.b64decode(encoded, validate=True)
            total += len(raw)
            self.assertLessEqual(len(raw), weather.MAX_JPEG)
            self.assertLessEqual(len(encoded), weather.MAX_BASE64)
            with Image.open(io.BytesIO(raw)) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (40, 30))
                self.assertEqual(dict(image.getexif()), {})
                for key in ("exif", "icc_profile", "comment", "duration", "extension"):
                    self.assertNotIn(key, image.info)
        self.assertLessEqual(total, weather.MAX_TOTAL_JPEG)

    def test_malformed_non_gif_truncated_and_compressed_byte_bounds(self):
        source = gif(1)
        for raw in [b"", b"not GIF", b"GIF89a", source[:-1], source + b"unexpected trailer",
                    b"GIF89a" + bytes(weather.MAX_DOWNLOAD)]:
            with self.subTest(length=len(raw)):
                assert_fixed_failure(self, lambda: weather.decode_radar(raw, NOW, NOW.timestamp()))

    def test_canvas_later_frame_count_and_total_pixel_bounds_precede_pillow(self):
        large_canvas = bytearray(gif(1))
        large_canvas[6:8] = (weather.MAX_DIMENSION + 1).to_bytes(2, "little")
        later_extent = bytearray(gif(2, size=(2, 2)))
        # This tiny fixed fixture has no comma bytes in palette or LZW data.
        descriptors = [index for index, byte in enumerate(later_extent) if byte == 0x2C]
        self.assertEqual(len(descriptors), 2)
        descriptor = descriptors[1]
        later_extent[descriptor + 5:descriptor + 7] = (1025).to_bytes(2, "little")
        too_many_pixels = bytearray(gif(8, size=(2, 2)))
        too_many_pixels[6:10] = (1024).to_bytes(2, "little") * 2
        for raw in [bytes(large_canvas), bytes(later_extent), gif(weather.MAX_FRAMES + 1), bytes(too_many_pixels)]:
            with self.subTest(length=len(raw)), patch.object(weather.Image, "open") as opened:
                assert_fixed_failure(self, lambda: weather.decode_radar(raw, NOW, NOW.timestamp()))
                opened.assert_not_called()

    def test_invalid_clocks_modified_dates_and_freshness_bounds(self):
        raw = gif(1)
        for now in [float("nan"), float("inf"), -float("inf"), True, "now", 10**100]:
            with self.subTest(now=now):
                assert_fixed_failure(self, lambda: weather.decode_radar(raw, NOW, now))
        for modified in [None, "yesterday", NOW.replace(tzinfo=None),
                         NOW - timedelta(seconds=1201), NOW + timedelta(seconds=301)]:
            with self.subTest(modified=modified):
                assert_fixed_failure(self, lambda: weather.decode_radar(raw, modified, NOW.timestamp()))
        for modified in [NOW - timedelta(seconds=1200), NOW + timedelta(seconds=300)]:
            self.assertEqual(len(weather.decode_radar(raw, modified, NOW.timestamp())["frames"]), 1)

    def test_pixel_decode_failure_is_sanitized(self):
        with patch.object(weather.Image, "open", side_effect=OSError("https://example.invalid/provider-body")):
            assert_fixed_failure(self, lambda: weather.decode_radar(gif(1), NOW, NOW.timestamp()))

    def test_unencodable_frame_fails_instead_of_exceeding_pi_budget(self):
        with patch.object(weather, "MAX_JPEG", 100):
            assert_fixed_failure(self, lambda: weather.decode_radar(gif(1), NOW, NOW.timestamp()))


class Content:
    def __init__(self, body, error=None, delay=0):
        self.body, self.error, self.delay = body, error, delay
        self.position = 0
        self.read_sizes = []

    async def read(self, size):
        self.read_sizes.append(size)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        chunk = self.body[self.position:self.position + size]
        self.position += len(chunk)
        return chunk


class Response:
    def __init__(self, body, status=200, headers=None, error=None, delay=0):
        self.status = status
        self.headers = {"Content-Type": "image/gif", "Last-Modified": format_datetime(NOW, usegmt=True)}
        if headers:
            self.headers.update(headers)
        self.content = Content(body, error, delay)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class DownloadChecks(unittest.IsolatedAsyncioTestCase):
    async def failure(self, session, now=None):
        try:
            await weather.download_radar(session, NOW.timestamp() if now is None else now)
        except ValueError as error:
            self.assertEqual(str(error), weather.ERROR)
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            self.assertNotIn("example.invalid", "".join(traceback.format_exception(error)))
        else:
            self.fail("Expected fixed radar failure")

    async def test_only_fixed_source_no_redirects_decompression_and_bounded_reads(self):
        source = gif()
        response = Response(source)
        session = Session(response)
        data, modified = await weather.download_radar(session, NOW.timestamp())
        self.assertEqual(data, source)
        self.assertEqual(modified, NOW)
        self.assertTrue(response.closed)
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://radar.weather.gov/ridge/standard/KGSP_loop.gif")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertFalse(kwargs["auto_decompress"])
        self.assertEqual(kwargs["timeout"], 10)
        self.assertTrue(all(size <= 65_536 for size in response.content.read_sizes))

    async def test_http_type_encoding_date_and_declared_size_fail_before_read(self):
        cases = [(302, {}), (500, {}), (200, {"Content-Type": "text/html"}),
                 (200, {"Content-Encoding": "gzip"}), (200, {"Last-Modified": "invalid"}),
                 (200, {"Content-Length": str(weather.MAX_DOWNLOAD + 1)})]
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                response = Response(gif(), status, headers)
                await self.failure(Session(response))
                self.assertTrue(response.closed)
                self.assertEqual(response.content.read_sizes, [])

    async def test_missing_stale_future_last_modified_and_actual_body_bounds(self):
        for header in ["", "Fri, 16 Oct 2026 05:00:00 GMT", "Sun, 18 Oct 2026 05:00:00 GMT",
                       "Sat, 17 Oct 2026 05:00:00"]:
            with self.subTest(header=header):
                await self.failure(Session(Response(gif(), headers={"Last-Modified": header})))
        response = Response(b"GIF89a" + bytes(weather.MAX_DOWNLOAD))
        await self.failure(Session(response))
        self.assertLessEqual(response.content.position, weather.MAX_DOWNLOAD + 1)
        await self.failure(Session(Response(b"HTML instead of a GIF")))

    async def test_transport_timeout_and_invalid_clock_disclose_no_cause(self):
        await self.failure(Session(OSError("https://example.invalid/provider-body")))
        await self.failure(Session(Response(gif(), error=OSError("https://example.invalid/read"))))
        with patch.object(weather, "TIMEOUT_SECONDS", 0.005):
            await self.failure(Session(Response(gif(), delay=0.05)))
        session = Session(Response(gif()))
        await self.failure(session, float("nan"))
        self.assertEqual(session.calls, [])

    async def test_cancellation_propagates(self):
        with self.assertRaises(asyncio.CancelledError):
            await weather.download_radar(Session(Response(gif(), error=asyncio.CancelledError())), NOW.timestamp())

    async def test_convenience_api_decodes_off_event_loop(self):
        loop_thread = __import__("threading").get_ident()
        decode = weather.decode_radar
        threads = []
        def checked(*args):
            threads.append(__import__("threading").get_ident())
            return decode(*args)
        with patch.object(weather, "decode_radar", side_effect=checked):
            result = await weather.fetch_radar(Session(Response(gif())), NOW.timestamp())
        self.assertEqual(len(result["frames"]), 2)
        self.assertNotEqual(threads, [loop_thread])


if __name__ == "__main__":
    unittest.main()
