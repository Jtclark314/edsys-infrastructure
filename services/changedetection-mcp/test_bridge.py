"""Offline behavioral tests using synthetic non-secret credentials and HTTP fixtures."""
import json
import ast
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx2 as httpx

from bridge import Bridge, BridgeError, INLINE, private_file, scrub, collect_secrets, DEFAULT_DENIED, DENIED_PORTS

WATCH = "11111111-1111-4111-8111-111111111111"
TAG = "22222222-2222-4222-8222-222222222222"
FIXTURE_KEY = "synthetic-api-key-not-a-real-credential"


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.write_private("api-key", FIXTURE_KEY)
        self.requests = []
        self.watch = {"uuid": WATCH, "url": "https://fixture.test/page", "title": "Synthetic", "paused": True,
                      "notification_muted": True, "method": "GET", "tags": [], "browser_steps": [], "headers": {}}
        self.tag = {"uuid": TAG, "title": "Synthetic tag", "notification_muted": True}
        self.notifications = []
        self.custom_response = None
        self.bridge = Bridge("http://127.0.0.1:18141", str(self.key), str(self.root / "artifacts"),
                             transport=httpx.MockTransport(self.handler), resolver=lambda host, port: {"192.168.50.20"},
                             document=json.loads(Path(__file__).with_name("api-spec.json").read_text()))
        self.addCleanup(self.bridge.client.close)

    def write_private(self, name, value):
        path = self.root / name
        path.write_text(value)
        path.chmod(0o600)
        return path

    def handler(self, request):
        body = request.read()
        self.requests.append((request, body))
        if request.headers.get("x-api-key") != FIXTURE_KEY:
            return httpx.Response(403, text="sensitive upstream error")
        path = request.url.path
        if path == "/api/v1/systeminfo":
            return httpx.Response(200, json={"version": "0.60.10", "all_muted": False, "watch_count": 1})
        if self.custom_response:
            response = self.custom_response(request)
            if response is not None:
                return response
        if path == "/api/v1/watch" and request.method == "GET":
            return httpx.Response(200, json={WATCH: {"tags": self.watch["tags"], "url": self.watch["url"]}})
        if path == "/api/v1/watch/" + WATCH and request.method == "GET":
            return httpx.Response(200, json="OK" if request.url.params else self.watch)
        if path == "/api/v1/tag/" + TAG and request.method == "GET":
            return httpx.Response(200, json=self.tag)
        if path == "/api/v1/tags" and request.method == "GET":
            return httpx.Response(200, json={TAG: self.tag})
        if path == "/api/v1/notifications" and request.method == "GET":
            return httpx.Response(200, json={"notification_urls": self.notifications})
        if request.method == "POST" and path in {"/api/v1/watch", "/api/v1/tag"}:
            return httpx.Response(201, json={"uuid": WATCH if path.endswith("watch") else TAG})
        if path == "/api/v1/import":
            return httpx.Response(200, json=[WATCH])
        return httpx.Response(200, json="OK")

    def mutation_requests(self):
        return [request for request, _ in self.requests if request.method != "GET" or any(k in request.url.params for k in ("recheck", "muted", "paused"))]

    def test_catalog_and_exact_schema(self):
        self.assertEqual(self.bridge.catalog()["native_operations"], 23)
        self.assertEqual(self.bridge.catalog("create", "mutation")["matches"], 2)
        for identifier in self.bridge.operations:
            result = self.bridge.schema(identifier)
            self.assertEqual(result["operation_id"], identifier)
        self.assertFalse(self.requests)

    def test_read_rejects_every_mutating_get_flag(self):
        for flag in ("recheck", "recheck_all", "paused", "muted"):
            with self.assertRaises(BridgeError):
                self.bridge.read("getWatch", {"uuid": WATCH}, {flag: "false"})
        self.assertFalse(self.requests)

    def test_read_cannot_write_or_accept_unknown_route(self):
        for operation in ("deleteWatch", "resetAll", "http://example.test/delete"):
            with self.assertRaises(BridgeError):
                self.bridge.read(operation)
        self.assertFalse(self.requests)

    def test_unknown_and_read_operation_cannot_mutate(self):
        for operation in ("getWatch", "deleteAll", "settingsUpdate"):
            with self.assertRaises(BridgeError):
                self.bridge.mutate(operation)
        self.assertFalse(self.requests)

    def test_path_and_query_override_rejected(self):
        with self.assertRaises(BridgeError):
            self.bridge.read("getWatch", {"uuid": "../../api"})
        with self.assertRaises(BridgeError):
            self.bridge.read("getWatch", {"uuid": WATCH}, {"url": "http://example.test"})
        self.assertFalse(self.requests)

    def test_private_inputs_permissions_symlink_device_and_size(self):
        public = self.write_private("public", "data")
        public.chmod(0o644)
        linked = self.root / "linked"
        linked.symlink_to(self.key)
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        for path in (public, linked, fifo, Path("/dev/null")):
            with self.assertRaises(BridgeError):
                private_file(path)
        with self.assertRaises(BridgeError):
            private_file(self.key, 4)

    def test_missing_credential_does_not_contact_backend(self):
        self.key.unlink()
        with self.assertRaises(BridgeError):
            self.bridge.read("getSystemInfo")
        self.assertFalse(self.requests)

    def test_api_credential_destination_is_qualified_local_only(self):
        for origin in ("https://public.example", "http://192.168.50.99:5000", "https://8.8.8.8"):
            with self.assertRaises(BridgeError):
                Bridge(origin, str(self.key), str(self.root / "artifacts"))

    def test_create_defaults_are_paused_and_muted(self):
        result = self.bridge.mutate("createWatch", parameters={"url": "https://fixture.test/page"})
        body = json.loads(self.requests[-1][1])
        self.assertTrue(body["paused"])
        self.assertTrue(body["notification_muted"])
        self.assertEqual(result["applied_safety_defaults"], {"paused": True, "notification_muted": True})

    def test_activation_needs_task_authority(self):
        with self.assertRaises(BridgeError):
            self.bridge.mutate("createWatch", parameters={"url": "https://fixture.test/page", "paused": False})
        self.assertFalse(self.mutation_requests())
        self.bridge.mutate("createWatch", parameters={"url": "https://fixture.test/page", "paused": False}, allow_network_activity=True)
        self.assertEqual(len(self.mutation_requests()), 1)

    def test_secret_configuration_requires_private_file(self):
        with self.assertRaises(BridgeError):
            self.bridge.mutate("createWatch", parameters={"url": "https://fixture.test", "headers": {"Authorization": "Bearer synthetic-secret"}})
        self.assertFalse(self.mutation_requests())
        source = self.write_private("body.json", json.dumps({"url": "https://fixture.test", "headers": {"Authorization": "Bearer synthetic-secret"}}))
        self.bridge.mutate("createWatch", body_file=str(source))
        self.assertEqual(json.loads(self.requests[-1][1])["headers"]["Authorization"], "Bearer synthetic-secret")

    def test_body_file_cannot_supply_authority_flags(self):
        source = self.write_private("body.json", json.dumps({"url": "https://fixture.test", "allow_notifications": True}))
        with self.assertRaises(BridgeError):
            self.bridge.mutate("createWatch", body_file=str(source))
        self.assertFalse(self.mutation_requests())

    def test_redaction_precedes_artifact_creation(self):
        self.watch.update(headers={"Authorization": "Bearer header-secret"}, body="body-secret",
                          browser_steps=[{"operation": "Fill", "selector": "selector-secret", "optional_value": "step-secret"}],
                          notification_urls=["mailto://notification-secret"], api_access_token=FIXTURE_KEY,
                          rss_access_token="rss-secret", last_error="header-secret body-secret",
                          url="https://user-secret:password-secret@fixture.test/path?token=query-secret#fragment-secret")
        result = self.bridge.read("getWatch", {"uuid": WATCH})
        output = json.dumps(result) + Path(result["artifact"]["path"]).read_text()
        for secret in (FIXTURE_KEY, "header-secret", "body-secret", "step-secret", "selector-secret", "notification-secret",
                       "rss-secret", "user-secret", "password-secret", "query-secret", "fragment-secret"):
            self.assertNotIn(secret, output)
        self.assertTrue(result["data"]["headers"]["redacted"])

    def test_snapshot_redacts_known_watch_credentials_and_html_is_inert(self):
        self.watch["headers"] = {"X-Secret": "synthetic-private-value"}
        self.custom_response = lambda req: httpx.Response(200, text="<script>ignored</script> synthetic-private-value", headers={"Content-Type": "text/html"}) if "/history/" in req.url.path else None
        result = self.bridge.read("getWatchSnapshot", {"uuid": WATCH, "timestamp": "latest"})
        self.assertNotIn("synthetic-private-value", json.dumps(result))
        self.assertTrue(result["artifact"]["path"].endswith(".txt"))

    def test_recheck_notifications_require_authority(self):
        self.watch["notification_muted"] = False
        self.notifications = ["mailto://synthetic@example.test"]
        with self.assertRaises(BridgeError):
            self.bridge.control("watch", WATCH, "recheck", allow_network_activity=True)
        self.assertFalse(self.mutation_requests())
        self.bridge.control("watch", WATCH, "recheck", allow_network_activity=True, allow_notifications=True)
        self.assertEqual(len(self.mutation_requests()), 1)

    def test_browser_actions_inherited_from_tag_require_authority(self):
        self.watch["tags"] = [TAG]
        self.tag["browser_steps"] = [{"operation": "Click", "selector": "#submit", "optional_value": ""}]
        with self.assertRaises(BridgeError):
            self.bridge.control("watch", WATCH, "recheck", allow_network_activity=True)
        self.assertFalse(self.mutation_requests())

    def test_target_method_needs_action_authority(self):
        self.watch["method"] = "DELETE"
        with self.assertRaises(BridgeError):
            self.bridge.control("watch", WATCH, "resume", allow_network_activity=True)
        self.assertFalse(self.mutation_requests())

    def test_create_and_import_tag_aliases_preserve_action_authority(self):
        self.tag["method"] = "POST"
        for field, value in (("tag", "Synthetic tag"), ("tag", TAG), ("tag_uuids", TAG)):
            with self.assertRaises(BridgeError):
                self.bridge.mutate("importWatches", urls=["https://fixture.test/page"],
                                   query_parameters={"paused": False, field: value}, allow_network_activity=True)
        with self.assertRaises(BridgeError):
            self.bridge.mutate("createWatch", parameters={"url": "https://fixture.test/page", "paused": False,
                               "tag": "Synthetic tag"}, allow_network_activity=True)
        self.assertFalse(self.mutation_requests())

    def test_delete_tag_requires_authority_for_active_member_effects(self):
        self.watch.update(tags=[TAG], paused=False)
        with self.assertRaises(BridgeError):
            self.bridge.mutate("deleteTag", {"uuid": TAG})
        self.assertFalse(self.mutation_requests())
        self.bridge.mutate("deleteTag", {"uuid": TAG}, allow_network_activity=True)
        self.assertEqual(len(self.mutation_requests()), 1)

    def test_create_tag_cannot_modify_existing_title_alias(self):
        with self.assertRaises(BridgeError):
            self.bridge.mutate("createTag", parameters={"title": "  sYnThetic TAG  ", "method": "POST"})
        self.assertFalse(self.mutation_requests())

    def test_tag_title_update_accounts_for_member_reprocessing(self):
        self.watch["tags"] = [TAG]
        self.watch["paused"] = False
        with self.assertRaises(BridgeError):
            self.bridge.mutate("updateTag", {"uuid": TAG}, {"title": "Renamed"})
        self.assertFalse(self.mutation_requests())

    def test_import_defaults_and_credential_query_rejection(self):
        self.bridge.mutate("importWatches", urls=["https://fixture.test/one", "https://fixture.test/two"])
        req, body = self.requests[-1]
        self.assertEqual(req.url.params["paused"], "true")
        self.assertEqual(req.url.params["notification_muted"], "true")
        self.assertEqual(body.decode(), "https://fixture.test/one\nhttps://fixture.test/two")
        for query in ({"headers": {"Cookie": "synthetic"}}, {"notification_muted": False}, {"allow_network_activity": True}):
            before = len(self.mutation_requests())
            with self.assertRaises(BridgeError):
                self.bridge.mutate("importWatches", urls=["https://fixture.test"], query_parameters=query)
            self.assertEqual(len(self.mutation_requests()), before)

    def test_import_rejects_nonstring_and_unsafe_urls(self):
        for urls in ([1], ["file:///etc/passwd"], ["https://fixture.test\nhttp://localhost"]):
            with self.assertRaises(BridgeError):
                self.bridge.mutate("importWatches", urls=urls)
        self.assertFalse(self.mutation_requests())

    def test_target_guards_allow_lan_tailnet_and_block_metadata(self):
        for address in ("192.168.1.2", "10.1.2.3", "100.80.2.3", "fd7a:115c:a1e0::1"):
            self.bridge.resolver = lambda host, port, address=address: {address}
            self.bridge.validate_target("https://fixture.test")
        for address in ("127.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "0.2.3.4", "240.0.0.3", "224.0.0.2", "fd00:ec2::254", "64:ff9b::7f00:1", "2002:7f00:1::1", "2001::1", "fec0::1"):
            self.bridge.resolver = lambda host, port, address=address: {"1.1.1.1", address}
            with self.assertRaises(BridgeError):
                self.bridge.validate_target("https://fixture.test")
        for url in ("http://localhost", "file:///tmp/a", "ftp://fixture.test", "http://127.0.0.1:18141", "https://fixture.test\\@localhost", "https://fixture.test/\npath", "http://fixture.test:2375", "http://fixture.test:22"):
            with self.assertRaises(BridgeError):
                self.bridge.validate_target(url)

    def test_common_target_policy_matches_shared_egress(self):
        source = Path(__file__).resolve().parents[2] / "docker/changedetection/egress_proxy.py"
        if not source.exists():
            self.skipTest("Shared source policy is not present in this runtime-only copy")
        definitions = {}
        for node in ast.parse(source.read_text()).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"DEFAULT_DENIED", "DENIED_PORTS"}:
                        definitions[target.id] = ast.literal_eval(node.value)
        self.assertEqual(set(DEFAULT_DENIED), set(definitions["DEFAULT_DENIED"]))
        self.assertEqual(DENIED_PORTS, definitions["DENIED_PORTS"])

    def test_no_redirect_or_upstream_error_echo(self):
        self.custom_response = lambda request: httpx.Response(302, headers={"Location": "http://example.test/leak"}, text=FIXTURE_KEY)
        with self.assertRaises(BridgeError) as caught:
            self.bridge.read("getWatch", {"uuid": WATCH})
        self.assertNotIn(FIXTURE_KEY, str(caught.exception))
        self.assertEqual(len(self.requests), 2)  # Version check, then one target request.

    def test_response_bound_prevents_artifact(self):
        self.bridge.max_response = 512
        self.custom_response = lambda request: httpx.Response(200, json={"large": "x" * 1024})
        with self.assertRaises(BridgeError):
            self.bridge.read("getWatch", {"uuid": WATCH})
        self.assertFalse((self.root / "artifacts").exists())

    def test_metadata_pagination(self):
        self.custom_response = lambda request: httpx.Response(200, json={str(index): {"url": "https://fixture.test"} for index in range(10)})
        result = self.bridge.read("listWatches", limit=3, offset=2)
        self.assertEqual(len(result["data"]), 3)
        self.assertEqual(result["pagination"]["total"], 10)
        self.assertEqual(result["pagination"]["next_offset"], 5)

    def test_large_text_stays_in_private_artifact(self):
        self.custom_response = lambda request: httpx.Response(200, text="x" * (INLINE + 1)) if "/history/" in request.url.path else None
        result = self.bridge.read("getWatchSnapshot", {"uuid": WATCH, "timestamp": "latest"})
        self.assertNotIn("text", result)
        path = Path(result["artifact"]["path"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_screenshot_is_fixed_authenticated_png_route(self):
        png = b"\x89PNG\r\n\x1a\nsynthetic"
        self.custom_response = lambda request: httpx.Response(200, content=png, headers={"Content-Type": "image/png"}) if request.url.path.startswith("/static/") else None
        result = self.bridge.screenshot(WATCH)
        self.assertEqual(self.requests[-2][0].url.path, "/api/v1/watch/" + WATCH)
        self.assertEqual(self.requests[-1][0].url.path, "/static/screenshot/" + WATCH)
        self.assertNotIn("data", result)
        self.assertEqual(Path(result["artifact"]["path"]).read_bytes(), png)

    def test_bad_screenshot_rejected_and_no_user_file_overwrite(self):
        self.custom_response = lambda request: httpx.Response(200, text="<html>login</html>") if request.url.path.startswith("/static/") else None
        with self.assertRaises(BridgeError):
            self.bridge.screenshot(WATCH)
        first = self.bridge.read("getWatch", {"uuid": WATCH})["artifact"]["path"]
        second = self.bridge.read("getWatch", {"uuid": WATCH})["artifact"]["path"]
        self.assertNotEqual(first, second)

    def test_native_jpeg_with_png_content_type_is_saved_correctly(self):
        jpeg = b"\xff\xd8\xffsynthetic\xff\xd9"
        self.custom_response = lambda request: httpx.Response(200, content=jpeg, headers={"Content-Type": "image/png"}) if request.url.path.startswith("/static/") else None
        result = self.bridge.screenshot(WATCH)
        self.assertEqual(result["artifact"]["content_type"], "image/jpeg")
        self.assertTrue(result["artifact"]["path"].endswith(".jpg"))

    def test_visual_snapshot_text_content_type_preserves_binary(self):
        self.watch["processor"] = "image_ssim_diff"
        png = b"\x89PNG\r\n\x1a\nsynthetic"
        self.custom_response = lambda request: httpx.Response(200, content=png, headers={"Content-Type": "text/plain"}) if "/history/" in request.url.path else None
        result = self.bridge.read("getWatchSnapshot", {"uuid": WATCH, "timestamp": "latest"})
        self.assertNotIn("text", result)
        self.assertEqual(Path(result["artifact"]["path"]).read_bytes(), png)
        with self.assertRaises(BridgeError):
            self.bridge.read("getWatchHistoryDiff", {"uuid": WATCH, "from_timestamp": "previous", "to_timestamp": "latest"})

    def test_runtime_schema_mismatch_prevents_mutation(self):
        self.bridge.native_spec_hash = "0" * 64
        with self.assertRaises(BridgeError):
            self.bridge.mutate("createWatch", parameters={"url": "https://fixture.test/page"})
        self.assertFalse(self.mutation_requests())

    def test_reflected_bare_authorization_and_cookie_values_are_redacted(self):
        secret_values = collect_secrets({"headers": {"Authorization": "Bearer synthetic-bearer-value",
                                                   "Cookie": "session=synthetic-cookie-value; theme=dark"}})
        value = scrub({"echo": "synthetic-bearer-value synthetic-cookie-value"}, secret_values)
        self.assertNotIn("synthetic-bearer-value", value["echo"])
        self.assertNotIn("synthetic-cookie-value", value["echo"])

    def test_artifact_symlink_rejected(self):
        (self.root / "artifacts").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(BridgeError):
            self.bridge.read("getWatch", {"uuid": WATCH})

    def test_calls_are_serialized(self):
        active = maximum = 0
        lock = threading.Lock()
        def handler(request):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.02)
            with lock:
                active -= 1
            return None
        self.custom_response = handler
        errors = []
        def read():
            try:
                self.bridge.read("getWatch", {"uuid": WATCH})
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=read) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(errors)
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
