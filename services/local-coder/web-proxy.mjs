// Loopback-only bridge from Tailscale Serve identity to OpenCode Basic auth.
import http from 'node:http';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

export function allowed(req, config, websocket = false) {
  const h = req.headers;
  const origin = `https://${config.publicHost}`;
  return ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress)
    && h.host === config.publicHost
    && h['x-forwarded-proto'] === 'https'
    && typeof h['tailscale-user-login'] === 'string'
    && h['tailscale-user-login'].toLowerCase() === config.ownerLogin.toLowerCase()
    && (!h.origin || h.origin === origin)
    && (!(websocket || !['GET', 'HEAD'].includes(req.method)) || h.origin === origin)
    && req.url.startsWith('/') && !req.url.startsWith('//');
}

function headers(req, config, websocket = false) {
  const result = { ...req.headers };
  for (const key of Object.keys(result)) {
    if (key.startsWith('tailscale-') || key.startsWith('x-forwarded-')
      || ['authorization', 'proxy-authorization', 'connection', 'upgrade', 'host'].includes(key)) delete result[key];
  }
  result.host = `127.0.0.1:${config.upstreamPort}`;
  result.authorization = `Basic ${Buffer.from(`opencode:${config.backendPassword}`).toString('base64')}`;
  if (websocket) Object.assign(result, { connection: 'Upgrade', upgrade: 'websocket' });
  return result;
}

export function createProxy(config) {
  if (!config.ownerLogin || !config.publicHost || !config.backendPassword || !config.upstreamPort) {
    throw new Error('Complete private web configuration is required');
  }
  const server = http.createServer((req, res) => {
    if (!allowed(req, config)) {
      res.writeHead(403, { 'content-type': 'text/plain', 'cache-control': 'no-store' });
      return res.end('Verified owner access through Tailscale HTTPS is required.');
    }
    const upstream = http.request({ hostname: '127.0.0.1', port: config.upstreamPort,
      path: req.url, method: req.method, headers: headers(req, config) }, response => {
      res.writeHead(response.statusCode, { ...response.headers,
        'x-frame-options': 'DENY', 'referrer-policy': 'no-referrer' });
      response.pipe(res);
      response.on('error', () => res.destroy());
    });
    upstream.setTimeout(125 * 60 * 1000, () => upstream.destroy());
    upstream.on('error', () => {
      if (!res.headersSent) res.writeHead(502, { 'content-type': 'text/plain' });
      res.end('Local coding service is starting or unavailable.');
    });
    req.on('aborted', () => upstream.destroy());
    res.on('close', () => upstream.destroy());
    req.pipe(upstream);
  });
  server.on('upgrade', (req, socket, head) => {
    if (!allowed(req, config, true) || req.headers.upgrade?.toLowerCase() !== 'websocket') {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    const upstream = http.request({ hostname: '127.0.0.1', port: config.upstreamPort,
      path: req.url, method: req.method, headers: headers(req, config, true) });
    upstream.on('upgrade', (response, remote, upstreamHead) => {
      const lines = Object.entries(response.headers).flatMap(([key, value]) =>
        (Array.isArray(value) ? value : [value]).map(item => `${key}: ${item}`));
      socket.write(`HTTP/1.1 101 Switching Protocols\r\n${lines.join('\r\n')}\r\n\r\n`);
      if (upstreamHead.length) socket.write(upstreamHead);
      if (head.length) remote.write(head);
      socket.pipe(remote).pipe(socket);
      socket.on('end', () => remote.destroy());
      remote.on('end', () => socket.destroy());
      socket.on('close', () => remote.destroy());
      remote.on('close', () => socket.destroy());
      remote.on('error', () => socket.destroy());
    });
    upstream.on('response', response => { response.resume(); socket.destroy(); });
    upstream.on('error', () => socket.destroy());
    socket.on('error', () => upstream.destroy());
    socket.on('close', () => upstream.destroy());
    upstream.end();
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const config = JSON.parse(fs.readFileSync('/mnt/ai-store/local-coder/web/proxy.json', 'utf8'));
  createProxy(config).listen(4097, '127.0.0.1', () => console.log('Local coder identity bridge ready on loopback'));
}
