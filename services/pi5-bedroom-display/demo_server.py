#!/usr/bin/env python3
"""Loopback-only, GET-only synthetic preview. No HA credentials or device I/O."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from model import View

ROOT = Path(__file__).resolve().parent
ROUTES = {'/': ('preview/index.html', 'text/html; charset=utf-8'),
          '/preview.js': ('preview/preview.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('preview/style.css', 'text/css; charset=utf-8'),
          '/views.json': ('views.json', 'application/json')}
for pose in ['idle', 'listening', 'thinking', 'speaking-0', 'speaking-1', 'failed']:
    ROUTES[f'/butler-{pose}.png'] = (f'assets/butler-{pose}.png', 'image/png')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = ROUTES.get(self.path)
        if route is None:
            self.send_error(404)
            return
        path, mime = route
        content = (ROOT / path).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    for view in json.loads((ROOT / 'views.json').read_text()):
        View.from_dict(view)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Synthetic display preview: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
