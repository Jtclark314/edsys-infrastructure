import base64
import hashlib
import http.client
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cameras
import operations as ops

EVENT_ID = "1792390000.123456-abc123"
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
    "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/"
    "wAARCAADAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/"
    "8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDy+iiitjI//9k="
)
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2" + b"fixture" * 20
INVENTORY = {"cameras": {"outdoor": {"camera_fps": 5.1, "process_fps": 5.0}}}


def event(**changes):
    return {"id": EVENT_ID, "camera": "outdoor", "start_time": time.time() - 20,
            "end_time": time.time() - 10, "has_clip": True, "has_snapshot": True, **changes}


class Response:
    def __init__(self, data=JPEG, mime="image/jpeg", *, length="auto", status=200, url=None):
        self.body = io.BytesIO(data)
        self.headers = Message()
        self.headers["Content-Type"] = mime
        if length != "none":
            self.headers["Content-Length"] = str(len(data)) if length == "auto" else length
        self.status = status
        self.url = url
        self.fp = Mock()
        self.read1 = Mock(side_effect=self.body.read)

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def transport(response):
    opener = Mock()
    def open_request(request, timeout):
        if response.url is None:
            response.url = request.full_url
        return response
    opener.open.side_effect = open_request
    return patch.object(cameras.urllib.request, "build_opener", return_value=opener), opener


class CameraBoundaries(unittest.TestCase):
    def test_camera_traversal_urls_and_controls_rejected_before_network(self):
        for value in ("../secrets", "https://external.invalid", "//external", "a%2Fb", "a?x=1", "a\nb", "", "a" * 81, None):
            with self.subTest(value=value), patch.object(ops, "request") as request:
                with self.assertRaises(ops.OperationsError):
                    cameras.camera_snapshot(value)
                request.assert_not_called()

    def test_event_ids_reject_traversal_and_arbitrary_values_before_network(self):
        for value in ("../secrets", "evt1", "https://external.invalid", EVENT_ID + "/../../x", EVENT_ID + "?download=1", "", None):
            for function in (cameras.camera_event_snapshot, cameras.camera_clip):
                with self.subTest(value=value, function=function.__name__), patch.object(ops, "request") as request:
                    with self.assertRaises(ops.OperationsError):
                        function(value)
                    request.assert_not_called()

    def test_unknown_camera_never_fetches_media(self):
        with patch.object(ops, "request", return_value=INVENTORY), patch.object(cameras, "_media") as media:
            with self.assertRaises(ops.OperationsError):
                cameras.camera_snapshot("unlisted")
            media.assert_not_called()

    def test_event_record_identity_inventory_and_retention_are_required(self):
        for change in ({"id": "different"}, {"camera": "unlisted"}, {"camera": "../secrets"},
                       {"has_clip": False}, {"has_clip": 1}, {"end_time": None},
                       {"end_time": float("nan")}, {"end_time": time.time() + 100},
                       {"start_time": True}, {"start_time": time.time() + 100}):
            with self.subTest(change=change), patch.object(ops, "request", side_effect=[INVENTORY, event(**change)]), patch.object(cameras, "_media") as media:
                with self.assertRaises(ops.OperationsError):
                    cameras.camera_clip(EVENT_ID)
                media.assert_not_called()

    def test_snapshot_requires_retained_snapshot_flag(self):
        with patch.object(ops, "request", side_effect=[INVENTORY, event(has_snapshot=False)]), patch.object(cameras, "_media") as media:
            with self.assertRaises(ops.OperationsError):
                cameras.camera_event_snapshot(EVENT_ID)
            media.assert_not_called()

    def test_internal_media_route_and_origin_fail_closed(self):
        for value in ("http://external.invalid/x", "/api/../secrets", "/api/outdoor/latest.jpg?store=1", "/api//external/latest.jpg"):
            with self.subTest(value=value), patch.object(cameras.urllib.request, "build_opener") as build:
                with self.assertRaises(ops.OperationsError), cameras._media(value, 100, {"image/jpeg"}):
                    pass
                build.assert_not_called()
        with patch.dict(ops.BASES, {"frigate": "http://external.invalid"}), patch.object(cameras.urllib.request, "build_opener") as build:
            with self.assertRaises(ops.OperationsError), cameras._media("/api/outdoor/latest.jpg", 100, {"image/jpeg"}):
                pass
            build.assert_not_called()

    def test_proxy_and_redirect_handlers_are_explicit(self):
        response = Response()
        context, opener = transport(response)
        with context as build, patch.object(ops, "request", return_value=INVENTORY):
            cameras.camera_snapshot("outdoor")
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], ops.NoRedirect)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.full_url, ops.BASES["frigate"] + "/api/outdoor/latest.jpg")
        self.assertNotIn("Authorization", request.headers)

    def test_http_error_body_and_redirect_target_never_exposed(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://private.invalid?token=private", 302, "private", {}, io.BytesIO(b"private"))
        with patch.object(ops, "request", return_value=INVENTORY), patch.object(cameras.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ops.OperationsError) as raised:
                cameras.camera_snapshot("outdoor")
        self.assertIn("302", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_status_redirect_and_bad_mime_are_rejected_before_read(self):
        for changes in ({"status": 206}, {"status": 302}, {"url": "https://external.invalid"}, {"mime": "text/html"}):
            response = Response(**changes)
            context, _ = transport(response)
            with self.subTest(changes=changes), context, patch.object(ops, "request", return_value=INVENTORY):
                with self.assertRaises(ops.OperationsError):
                    cameras.camera_snapshot("outdoor")
                response.read1.assert_not_called()

    def test_invalid_or_oversized_content_length_rejected_before_read(self):
        for length in ("-1", "secret", "0", str(cameras.IMAGE_MAX_BYTES + 1), "999999999999999"):
            response = Response(length=length)
            context, _ = transport(response)
            with self.subTest(length=length), context, patch.object(ops, "request", return_value=INVENTORY):
                with self.assertRaises(ops.OperationsError):
                    cameras.camera_snapshot("outdoor")
                response.read1.assert_not_called()

    def test_stream_limit_is_enforced_without_content_length(self):
        response = Response(b"x" * 70, length="none")
        context, _ = transport(response)
        with context, patch.object(ops, "request", return_value=INVENTORY), patch.object(cameras, "IMAGE_MAX_BYTES", 64):
            with self.assertRaises(ops.OperationsError):
                cameras.camera_snapshot("outdoor")

    def test_truncated_content_and_invalid_image_fail_closed(self):
        for response in (Response(length=str(len(JPEG) + 1)), Response(JPEG[:-2]), Response(b"not an image")):
            context, _ = transport(response)
            with self.subTest(response=response), context, patch.object(ops, "request", return_value=INVENTORY):
                with self.assertRaises(ops.OperationsError):
                    cameras.camera_snapshot("outdoor")

    def test_socket_timeout_suppresses_private_error_text(self):
        response = Response()
        response.read1.side_effect = TimeoutError("token=private")
        context, _ = transport(response)
        with context, patch.object(ops, "request", return_value=INVENTORY):
            with self.assertRaises(ops.OperationsError) as raised:
                cameras.camera_snapshot("outdoor")
        self.assertNotIn("private", str(raised.exception))

    def test_whole_transfer_deadline_and_socket_timeout(self):
        response = Response()
        with patch.object(cameras.time, "monotonic", return_value=11):
            with self.assertRaises(ops.OperationsError):
                cameras._read(response, 10, 10)
            response.read1.assert_not_called()
        with patch.object(cameras.time, "monotonic", return_value=5):
            cameras._read(response, 10, 10)
        response.fp.raw._sock.settimeout.assert_called_once_with(5)

    def test_http_response_closed_at_exact_length_is_normal_eof(self):
        response = Response()
        response.isclosed = lambda: True
        response.fp = None
        self.assertEqual(cameras._read(response, 10, time.monotonic() + 30), b"")
        response.read1.assert_not_called()

    def test_broken_chunked_transfer_does_not_expose_upstream_text(self):
        response = Response(length="none")
        response.read1.side_effect = http.client.IncompleteRead(b"private")
        context, _ = transport(response)
        with context, patch.object(ops, "request", return_value=INVENTORY):
            with self.assertRaises(ops.OperationsError) as raised:
                cameras.camera_snapshot("outdoor")
        self.assertNotIn("private", str(raised.exception))

    def test_unexpected_transport_cannot_silently_lose_deadline(self):
        response = Response()
        response.fp = object()
        with self.assertRaises(ops.OperationsError):
            cameras._read(response, 10, time.monotonic() + 30)
        response.read1.assert_not_called()

    def test_snapshot_metadata_is_bounded_and_never_claims_live_acceptance(self):
        response = Response()
        context, _ = transport(response)
        with context, patch.object(ops, "request", return_value=INVENTORY):
            body, metadata = cameras.camera_snapshot("outdoor")
        self.assertEqual(body, JPEG)
        self.assertEqual((metadata["width"], metadata["height"]), (2, 3))
        self.assertEqual(metadata["sha256"], hashlib.sha256(JPEG).hexdigest())
        self.assertEqual(metadata["camera_fps"], 5.1)
        self.assertFalse(metadata["capture_timestamp_verified"])
        self.assertFalse(metadata["decode_verified"])
        self.assertFalse(metadata["live_feed_verified"])
        self.assertNotIn("path", metadata)

    def test_zero_or_unknown_fps_warns_about_preview(self):
        for fps in (0, None, float("nan"), -1):
            response = Response()
            context, _ = transport(response)
            inventory = {"cameras": {"outdoor": {"camera_fps": fps}}}
            with self.subTest(fps=fps), context, patch.object(ops, "request", return_value=inventory):
                _, metadata = cameras.camera_snapshot("outdoor")
            self.assertTrue(metadata["preview_possible"])
            self.assertFalse(metadata["live_feed_verified"])

    def test_event_snapshot_uses_exact_validated_event_route(self):
        response = Response()
        context, opener = transport(response)
        with context, patch.object(ops, "request", side_effect=[INVENTORY, event()]):
            _, metadata = cameras.camera_event_snapshot(EVENT_ID)
        self.assertEqual(opener.open.call_args.args[0].full_url, ops.BASES["frigate"] + "/api/events/" + EVENT_ID + "/snapshot.jpg")
        self.assertEqual(metadata["source"], "retained_event_snapshot")
        self.assertNotIn("label", metadata)


class PrivateClipArtifacts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "clips"
        self.root_patch = patch.object(cameras, "ARTIFACT_ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temporary.cleanup()

    def fetch(self, response):
        context, _ = transport(response)
        with context, patch.object(ops, "request", side_effect=[INVENTORY, event()]):
            return cameras.camera_clip(EVENT_ID)

    def test_clip_private_random_name_hash_and_metadata(self):
        metadata = self.fetch(Response(MP4, "video/mp4"))
        path = Path(metadata["path"])
        self.assertEqual(path.parent, self.root)
        self.assertRegex(path.name, r"^clip-[a-f0-9]{32}\.mp4$")
        self.assertNotIn(EVENT_ID, path.name)
        self.assertEqual(path.read_bytes(), MP4)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(metadata["sha256"], hashlib.sha256(MP4).hexdigest())
        self.assertFalse(metadata["decode_verified"])

    def test_clip_failure_removes_partial_artifact(self):
        for response in (Response(b"html instead of video", "video/mp4"),
                         Response(MP4, "video/mp4", length=str(len(MP4) + 1)),
                         Response(MP4, "video/mp4", length=str(cameras.CLIP_MAX_BYTES + 1))):
            with self.subTest(response=response), self.assertRaises(ops.OperationsError):
                self.fetch(response)
            self.assertEqual(list(self.root.glob("clip-*")), [])

    def test_stream_failure_removes_written_partial(self):
        response = Response(MP4, "video/mp4", length="none")
        response.read1.side_effect = [MP4[:20], TimeoutError("private")]
        with self.assertRaises(ops.OperationsError):
            self.fetch(response)
        self.assertEqual(list(self.root.glob("clip-*")), [])

    def test_symlink_artifact_root_rejected(self):
        destination = Path(self.temporary.name) / "other"
        destination.mkdir()
        self.root.symlink_to(destination, target_is_directory=True)
        with patch.object(ops, "request", side_effect=[INVENTORY, event()]), self.assertRaises(ops.OperationsError):
            cameras.camera_clip(EVENT_ID)
        self.assertEqual(list(destination.iterdir()), [])

    def test_artifact_symlink_rejected_without_following_it(self):
        self.root.mkdir()
        outside = Path(self.temporary.name) / "outside"
        outside.write_bytes(b"unrelated")
        (self.root / ("clip-" + "a" * 32 + ".mp4")).symlink_to(outside)
        with self.assertRaises(ops.OperationsError):
            self.fetch(Response(MP4, "video/mp4"))
        self.assertEqual(outside.read_bytes(), b"unrelated")

    def test_cleanup_only_removes_owned_expired_adapter_files(self):
        self.root.mkdir()
        expired = self.root / ("clip-" + "a" * 32 + ".mp4")
        unrelated = self.root / "unrelated.txt"
        expired.write_bytes(b"expired")
        unrelated.write_bytes(b"retain")
        old = time.time() - cameras.ARTIFACT_MAX_AGE_SECONDS - 1
        os.utime(expired, (old, old))
        os.utime(unrelated, (old, old))
        self.fetch(Response(MP4, "video/mp4"))
        self.assertFalse(expired.exists())
        self.assertEqual(unrelated.read_bytes(), b"retain")

    def test_quota_stops_before_media_fetch(self):
        self.root.mkdir()
        for i in range(cameras.ARTIFACT_MAX_FILES):
            (self.root / ("clip-" + format(i, "032x") + ".mp4")).write_bytes(b"keep")
        with patch.object(ops, "request", side_effect=[INVENTORY, event()]), patch.object(cameras, "_media") as media:
            with self.assertRaises(ops.OperationsError):
                cameras.camera_clip(EVENT_ID)
            media.assert_not_called()

    def test_lock_symlink_rejected_without_touching_target(self):
        self.root.mkdir()
        outside = Path(self.temporary.name) / "outside"
        outside.write_bytes(b"retain")
        (self.root / ".lock").symlink_to(outside)
        with self.assertRaises(ops.OperationsError):
            self.fetch(Response(MP4, "video/mp4"))
        self.assertEqual(outside.read_bytes(), b"retain")


if __name__ == "__main__":
    unittest.main()
