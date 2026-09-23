"""Exercise the PowerShell-to-C# HTTP boundary used during account bootstrap."""
import http.server
import pathlib
import shutil
import subprocess
import threading
import unittest


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
class BootstrapHttpTests(unittest.TestCase):
    def test_null_body_is_get_and_nonempty_body_is_post(self):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requests.append((self.command, self.headers.get("Authorization"), body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":true}')

            do_GET = handle_request
            do_POST = handle_request

            def log_message(self, *args):
                pass

        source = (pathlib.Path(__file__).parents[1] / "install-work-laptop-sentinel-host.ps1").read_text()
        helper = source[source.index("Add-Type -TypeDefinition"):source.index("[int]$health")]
        # The installer targets Windows PowerShell 5.1 / .NET Framework.
        # Modern .NET marks HttpWebRequest obsolete but retains its behavior.
        helper = helper.replace("Add-Type -TypeDefinition", "Add-Type -IgnoreWarnings -TypeDefinition")
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/config"
                script = helper + f"\n[EdSysSunshineSetup]::Request('{url}', $null, $null)\n"
                script += f"[EdSysSunshineSetup]::Request('{url}', '{{\"probe\":true}}', 'test-only')\n"
                result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(requests, [("GET", None, b""),
                                    ("POST", "Basic test-only", b'{"probe":true}')])


if __name__ == "__main__":
    unittest.main()
