"""Contract checks for destination pinning, origin scope and local-only AI."""

import asyncio
import os
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import app_guard
import egress_proxy


def resolver_for(*addresses):
    return lambda host, port, **kwargs: [
        (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
         (ip, port, 0, 0) if ":" in ip else (ip, port)) for ip in addresses
    ]


class DestinationTests(unittest.TestCase):
    def policy(self, *addresses, browser=False):
        return egress_proxy.DestinationPolicy(["10.254.230.16/28", "10.254.230.112/28", "10.254.230.96/28"],
                                              resolver=resolver_for(*addresses), browser=browser)

    def test_public_lan_tailnet_allowed(self):
        for address in ["93.184.216.34", "192.168.50.20", "100.75.12.40", "fd7a:115c:a1e0::1234"]:
            with self.subTest(address=address):
                self.assertEqual(len(self.policy(address).resolve("site.invalid", 443)), 1)

    def test_sensitive_destinations_refused(self):
        addresses = ["127.0.0.1", "0.0.0.0", "169.254.169.254", "::1", "fe80::1", "224.0.0.1",
                     "fd00:ec2::254", "100.100.100.200", "168.63.129.16", "10.254.230.18",
                     "10.254.230.114", "10.254.230.98", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
                     "2002:a9fe:a9fe::", "64:ff9b::a9fe:a9fe", "64:ff9b:1::a9fe:a9fe",
                     "2001::a9fe:a9fe", "::a9fe:a9fe", "::ffff:0:a9fe:a9fe"]
        for address in addresses:
            with self.subTest(address=address), self.assertRaises(egress_proxy.Refused):
                self.policy(address).resolve("site.invalid", 443)

    def test_mixed_answers_fail_closed(self):
        with self.assertRaises(egress_proxy.Refused):
            self.policy("93.184.216.34", "169.254.169.254").resolve("site.invalid", 443)

    def test_management_and_non_web_low_ports_refused(self):
        for port in (22, 25, 53, 2375, 2376, 6443, 10250, 8141, 8143, 8453, 0, 65536):
            with self.subTest(port=port), self.assertRaises(egress_proxy.Refused):
                self.policy("192.168.50.50").resolve("site.invalid", port)

    def test_browser_cannot_address_ollama(self):
        with self.assertRaises(egress_proxy.Refused):
            self.policy("192.168.50.50", browser=True).resolve("local.invalid", 11434)
        self.assertEqual(len(self.policy("192.168.50.50").resolve("local.invalid", 11434)), 1)

    def test_numeric_socket_is_pinned(self):
        policy = self.policy("93.184.216.34")
        with patch.object(egress_proxy.socket, "socket") as sock:
            policy.connect("site.invalid", 443)
            sock.return_value.connect.assert_called_once_with(("93.184.216.34", 443))

    def test_exact_host_management_ports(self):
        p = egress_proxy.DestinationPolicy(["100.75.12.1/32"], resolver=resolver_for("192.0.2.50"),
                                           protected_hosts=["192.0.2.50"], protected_ports=[443, 8444])
        for port in (443, 8444):
            with self.assertRaises(egress_proxy.Refused):
                p.resolve("own-host.invalid", port)
        self.assertEqual(len(p.resolve("own-host.invalid", 18146)), 1)
        p.resolver = resolver_for("192.0.2.51")
        self.assertEqual(len(p.resolve("other-host.invalid", 443)), 1)
        p.resolver = resolver_for("100.75.12.1")
        with self.assertRaises(egress_proxy.Refused):
            p.resolve("own-tailnet.invalid", 18146)

    def test_answer_count_bounded(self):
        with self.assertRaises(egress_proxy.Refused):
            self.policy(*(["93.184.216.34"] * 17)).resolve("site.invalid", 443)

    def test_parser_confusion_refused(self):
        for host in ("", "a\\b", "a%2fb", "a@b", "a b", "a\nb", "fe80::1%eth0"):
            with self.subTest(host=host), self.assertRaises(egress_proxy.Refused):
                self.policy("93.184.216.34").resolve(host, 443)


class OriginTests(unittest.IsolatedAsyncioTestCase):
    def test_per_watch_transport_cannot_bypass_gateway(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://192.0.2.2:3128", "playwright_proxy_server": "http://192.0.2.3:3128",
                                     "PLAYWRIGHT_DRIVER_URL": "ws://192.0.2.2:3128/browser-cdp"}):
            app_guard.check_transport_override(None)
            app_guard.check_transport_override("http://192.0.2.2:3128")
            with self.assertRaises(ValueError):
                app_guard.check_transport_override("http://127.0.0.1:5000")
            with self.assertRaises(ValueError):
                app_guard.check_transport_override(None, "ws://127.0.0.1:5000", browser=True)
            self.assertEqual(app_guard.browser_proxy(None), {"server": "http://192.0.2.3:3128", "bypass": "<-loopback>"})

    def test_monitor_cannot_invoke_model_api(self):
        app_guard.check_watch_url("http://192.0.2.50:8123/")
        for port in (11434, 11435):
            with self.assertRaises(ValueError):
                app_guard.check_watch_url(f"http://192.0.2.50:{port}/api/generate")

    def test_cross_origin_secret_headers_refused(self):
        for header in ("Authorization", "Cookie", "X-API-Key", "X-Custom"):
            with self.subTest(header=header), self.assertRaises(ValueError):
                app_guard.check_redirect("https://private.invalid/a", "https://cdn.invalid/b", {header: "fixture"})

    def test_same_origin_and_uncredentialed_redirect(self):
        app_guard.check_redirect("https://private.invalid/a", "https://private.invalid/b", {"Authorization": "fixture"})
        app_guard.check_redirect("https://private.invalid/a", "https://cdn.invalid/b", {"User-Agent": "fixture"})

    async def test_browser_header_scope(self):
        class Session:
            handler = None
            calls = []

            def on(self, name, handler):
                self.handler = handler

            async def send(self, method, params):
                self.calls.append((method, params))

        class Context:
            session = Session()

            async def new_cdp_session(self, page):
                return self.session

        context = Context()
        await app_guard.scope_browser_headers(context, {"Authorization": "fixture", "Cookie": "test=session"},
                                              "https://private.invalid/start", object())
        event = {"requestId": "fixture", "request": {"url": "https://private.invalid/api",
                                                    "headers": {"Accept": "text/plain"}}}
        await context.session.handler(event)
        method, params = context.session.calls[-1]
        self.assertEqual(method, "Fetch.continueRequest")
        self.assertIn({"name": "Authorization", "value": "fixture"}, params["headers"])
        self.assertIn({"name": "Cookie", "value": "test=session"}, params["headers"])
        for foreign in ("https://cdn.invalid/image", "https://private.invalid:444/api"):
            event["request"]["url"] = foreign
            event["request"]["headers"].update(Authorization="fixture", Cookie="test=session")
            await context.session.handler(event)
            self.assertEqual(context.session.calls[-1][1]["headers"], [{"name": "Accept", "value": "text/plain"}])
        event["request"]["url"] = "https://private.invalid/api"
        event.update(responseStatusCode=307, responseHeaders=[{"name": "Location", "value": "https://cdn.invalid/received"}])
        await context.session.handler(event)
        self.assertEqual(context.session.calls[-1][0], "Fetch.failRequest")


class LocalModelTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"EDSYS_LLM_MODEL": "ollama_chat/llama3.2:3b",
                                             "EDSYS_LLM_BASE": "http://192.168.50.50:11434"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def kwargs(self):
        return {"model": "ollama_chat/llama3.2:3b", "api_base": "http://192.168.50.50:11434", "messages": [{"role": "user", "content": "fixture"}]}

    def test_cpu_output_context_and_timeout_bounds(self):
        values = self.kwargs() | {"max_tokens": 100000, "timeout": 9000, "extra_body": {"options": {"num_gpu": 99}}}
        result = app_guard.local_llm_kwargs(values)
        self.assertEqual([result[k] for k in ("num_gpu", "num_thread", "num_ctx", "keep_alive", "max_tokens", "timeout")],
                         [0, 4, 8192, 0, 512, 120])
        self.assertNotIn("extra_body", result)

    def test_alternate_model_endpoint_and_api_key_refused(self):
        for changed in ({"model": "gpt-4.1"}, {"api_base": "https://external.invalid"}, {"api_key": "fixture"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                app_guard.local_llm_kwargs(self.kwargs() | changed)

    def test_input_bound(self):
        with self.assertRaises(ValueError):
            app_guard.local_llm_kwargs(self.kwargs() | {"messages": [{"role": "user", "content": "x" * 32768}]})

    def test_model_discovery_guard(self):
        app_guard.local_model_provider("ollama", "http://192.168.50.50:11434")
        with self.assertRaises(ValueError):
            app_guard.local_model_provider("openai", "https://external.invalid")
        with self.assertRaises(ValueError):
            app_guard.local_model_provider("ollama", "http://192.168.50.50:11434", "fixture-key")

    def test_shared_inference_lock_rejects_concurrent_call_and_releases(self):
        original_open = os.open
        entered = threading.Event()
        release = threading.Event()
        def held(**kwargs):
            entered.set()
            release.wait(timeout=2)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app_guard.os, "open", side_effect=lambda path, flags, mode: original_open(directory + "/lock", flags, mode)):
                worker = threading.Thread(target=lambda: app_guard.local_completion(held, self.kwargs()))
                worker.start()
                self.assertTrue(entered.wait(timeout=1))
                try:
                    with self.assertRaises(RuntimeError):
                        app_guard.local_completion(lambda **kwargs: None, self.kwargs())
                finally:
                    release.set()
                    worker.join(timeout=2)
                self.assertEqual(app_guard.local_completion(lambda **kwargs: "released", self.kwargs()), "released")


if __name__ == "__main__":
    unittest.main()
