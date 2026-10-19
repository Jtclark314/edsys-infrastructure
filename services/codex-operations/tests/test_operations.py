import io
import json
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operations as ops


class BoundsAndPrivacy(unittest.TestCase):
    def test_integer_bounds_and_booleans(self):
        for value in (0, 51, True, "1", 1.0):
            with self.subTest(value=value), self.assertRaises(ops.OperationsError):
                ops.bounded_int(value, 1, 50)

    def test_entity_path_injection_rejected_before_transport(self):
        for value in ("../secrets", "sensor.x?key=secret", "sensor.X", "sensor.x/y", "", "a." + "b" * 200):
            with self.subTest(value=value), patch.object(ops, "ha") as request, self.assertRaises(ops.OperationsError):
                ops.ha_state(value)
            request.assert_not_called()

    def test_bounds_reject_before_service_calls(self):
        for fn, args in ((ops.ha_entities, ("", "", 51)), (ops.ha_history, ("sun.sun", 25)),
                         (ops.frigate_events, ("", "", 169)), (ops.plex_search, ("Silo", 41)),
                         (ops.plex_metadata, ("1/refresh",)), (ops.rag_search, ("x", 7)),
                         (ops.rag_search, ("x", 2, float("nan")))):
            with self.subTest(fn=fn.__name__), patch.object(ops, "request") as request, self.assertRaises(ops.OperationsError):
                fn(*args)
            request.assert_not_called()

    def test_credential_shaped_queries_rejected(self):
        for value in ("password=hunter22", "Bearer xyzcredential", "api_key: abcdef", "sk-proj-" + "a" * 30):
            with self.subTest(value=value), self.assertRaises(ops.OperationsError):
                ops.text(value)

    def test_sanitize_nested_and_embedded_credentials(self):
        raw = {"password": "private", "auth_token": "private", "nested": [
            "http://user:password@host/x?access_token=abcd&key=efgh", "Bearer zzzz", "known-value"]}
        rendered = json.dumps(ops.sanitize(raw, ("known-value",)))
        for secret in ("private", "user:password", "abcd", "efgh", "zzzz", "known-value"):
            self.assertNotIn(secret, rendered)

    def test_long_text_bounded(self):
        self.assertEqual(len(ops.sanitize("x" * 10000)), ops.MAX_TEXT)

    def test_no_redirect(self):
        self.assertIsNone(ops.NoRedirect().redirect_request(None, None, 302, "", {}, "https://external.invalid"))

    def test_reject_non_retrieval_post(self):
        with patch.object(ops.urllib.request, "build_opener") as build, self.assertRaises(ops.OperationsError):
            ops.request("plex", "/library/sections", payload={"write": True})
        build.assert_not_called()

    def test_request_route_guards(self):
        for service, path in (("other", "/x"), ("plex", "//../x"), ("plex", "/x?token=abc")):
            with self.subTest(path=path), self.assertRaises(ops.OperationsError):
                ops.request(service, path)

    def test_http_error_body_and_url_are_not_exposed(self):
        error = urllib.error.HTTPError("http://host/?token=private", 401, "private", {}, io.BytesIO(b"private"))
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(ops.urllib.request, "build_opener", return_value=opener), self.assertRaises(ops.OperationsError) as raised:
            ops.request("plex", "/library/sections")
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_oversized_http_body_fails_closed(self):
        response = Mock()
        response.read.return_value = b"x" * (ops.MAX_BYTES + 1)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(ops.urllib.request, "build_opener", return_value=opener), self.assertRaises(ops.OperationsError):
            ops.request("frigate", "/api/stats")
        response.read.assert_called_once_with(ops.MAX_BYTES + 1)


class SafeServiceProjection(unittest.TestCase):
    def test_ha_state_omits_raw_attributes(self):
        upstream = {"entity_id": "sun.sun", "state": "above_horizon", "attributes": {"friendly_name": "Sun", "access_token": "private"}}
        with patch.object(ops, "ha", return_value=upstream):
            value = ops.ha_state("sun.sun")
        self.assertEqual(value["name"], "Sun")
        self.assertNotIn("private", json.dumps(value))
        self.assertNotIn("attributes", value)

    def test_ha_helper_errors_suppressed(self):
        with patch.object(ops, "module", side_effect=RuntimeError("token=private")), self.assertRaises(ops.OperationsError) as raised:
            ops.ha_inventory()
        self.assertNotIn("private", str(raised.exception))

    def test_history_is_single_entity_fixed_get_and_bounded(self):
        upstream = [[{"state": str(i), "attributes": {"password": "private"}, "last_changed": "today"} for i in range(8)]]
        with patch.object(ops, "ha", return_value=upstream) as call:
            value = ops.ha_history("sun.sun", 2, 3)
        payload = call.call_args.args[0]
        self.assertEqual(payload["method"], "GET")
        self.assertTrue(payload["path"].startswith("api/history/period/"))
        self.assertIn("filter_entity_id=sun.sun", payload["path"])
        self.assertIn("no_attributes=", payload["path"])
        self.assertEqual(len(value["states"]), 3)
        self.assertTrue(value["limited"])
        self.assertNotIn("private", json.dumps(value))

    def test_frigate_omits_recognition_and_thumbnail_content(self):
        raw = [{"id": "evt1", "camera": "driveway", "label": "person", "sub_label": "Private Person", "thumbnail": "pixels", "data": {"api_key": "private"}}]
        with patch.object(ops, "request", return_value=raw) as call:
            value = ops.frigate_events(limit=1)
        self.assertEqual(value["events"][0]["id"], "evt1")
        self.assertNotIn("Private", json.dumps(value))
        self.assertNotIn("pixels", json.dumps(value))
        self.assertEqual(call.call_args.kwargs["query"]["include_thumbnails"], 0)

    def test_plex_token_only_in_header_and_metadata_projection(self):
        raw = ET.fromstring('<MediaContainer><Video ratingKey="12" title="known-secret" key="/x?token=known-secret"><Media><Part file="/private/media"/></Media></Video></MediaContainer>')
        with patch.object(ops, "plex_key", return_value="known-secret"), patch.object(ops, "request", return_value=raw) as call:
            value = ops.plex_search("show", 2)
        self.assertEqual(call.call_args.kwargs["headers"], {"X-Plex-Token": "known-secret"})
        rendered = json.dumps(value)
        self.assertNotIn("known-secret", rendered)
        self.assertNotIn("/private/media", rendered)
        self.assertNotIn("key", value["results"][0])

    def test_plex_missing_auth_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops, "PLEX_PREFERENCES", Path(tmp) / "absent"):
            with self.assertRaises(ops.OperationsError):
                ops.plex_key()

    def test_rag_guard_prevents_query_on_changed_route(self):
        result = Mock(stdout=json.dumps(["EMBEDDING_ENGINE=openai", "EMBEDDING_MODEL_PREF=cloud"]))
        with patch.object(ops.subprocess, "run", return_value=result), patch.object(ops, "request") as call:
            with self.assertRaises(ops.OperationsError):
                ops.rag_search("EdSys architecture")
        call.assert_not_called()

    def test_rag_vector_search_never_calls_chat_and_redacts_known_key(self):
        helper = Mock()
        helper.read_api_key.return_value = "known-secret"
        raw = {"results": [{"text": "source known-secret", "score": 0.9, "metadata": {"title": "EdSys", "api_key": "private", "chunkSource": "record.md"}}]}
        with patch.object(ops, "rag_local_embeddings"), patch.object(ops, "module", return_value=helper), patch.object(ops, "request", return_value=raw) as call:
            value = ops.rag_search("EdSys architecture", 2)
        self.assertEqual(call.call_args.args[:2], ("rag", "/api/v1/workspace/edsys-rag-current/vector-search"))
        self.assertEqual(call.call_args.kwargs["payload"]["topN"], 2)
        self.assertTrue(value["retrieval_only"])
        self.assertNotIn("known-secret", json.dumps(value))
        self.assertNotIn("private", json.dumps(value))


if __name__ == "__main__":
    unittest.main()
