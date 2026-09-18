import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { allowed, createProxy } from '../web-proxy.mjs';

const config = { ownerLogin: 'owner@example.invalid', publicHost: 'coder.example.invalid:8444',
  upstreamPort: 1, backendPassword: 'synthetic-test-only' };
const base = { host: config.publicHost, 'x-forwarded-proto': 'https',
  'tailscale-user-login': config.ownerLogin };
function request(headers = base, method = 'GET', remoteAddress = '127.0.0.1') {
  return { headers, method, url: '/', socket: { remoteAddress } };
}

test('identity, proxy route, and browser-origin boundaries fail closed', () => {
  assert.equal(allowed(request(), config), true);
  for (const key of ['host', 'x-forwarded-proto', 'tailscale-user-login']) {
    const headers = { ...base }; delete headers[key];
    assert.equal(allowed(request(headers), config), false);
  }
  assert.equal(allowed(request({ ...base, 'tailscale-user-login': 'other@example.invalid' }), config), false);
  assert.equal(allowed(request(base, 'GET', '192.0.2.1'), config), false);
  assert.equal(allowed(request({ ...base, origin: 'https://untrusted.example' }), config), false);
  assert.equal(allowed(request(base, 'POST'), config), false);
  assert.equal(allowed(request({ ...base, origin: `https://${config.publicHost}` }, 'POST'), config), true);
  assert.equal(allowed(request(), config, true), false);
  assert.equal(allowed({ ...request(), url: '//untrusted.example' }, config), false);
  assert.throws(() => createProxy({}), /configuration/);
});

test('authorized streaming HTTP and upgrades work; rejected requests never reach backend', async () => {
  let calls = 0;
  const upstream = http.createServer((req, res) => {
    calls++;
    assert.equal(req.headers.authorization,
      `Basic ${Buffer.from(`opencode:${config.backendPassword}`).toString('base64')}`);
    assert.equal(req.headers['tailscale-user-login'], undefined);
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: first\n\n');
    setTimeout(() => res.end('data: second\n\n'), 40);
  });
  upstream.on('upgrade', (req, socket) => {
    calls++;
    assert.ok(req.headers.authorization.startsWith('Basic '));
    socket.write('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');
    socket.on('data', bytes => socket.write(bytes));
  });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  const proxy = createProxy({ ...config, upstreamPort: upstream.address().port });
  proxy.listen(0, '127.0.0.1'); await once(proxy, 'listening');
  const port = proxy.address().port;
  try {
    const denied = await fetch(`http://127.0.0.1:${port}/`);
    assert.equal(denied.status, 403); assert.equal(calls, 0);
    const chunks = [];
    await new Promise((resolve, reject) => {
      http.get({ host: '127.0.0.1', port, headers: { ...base, authorization: 'discard-this' } }, res => {
        assert.equal(res.statusCode, 200);
        res.on('data', bytes => chunks.push(bytes.toString()));
        res.on('end', resolve);
      }).on('error', reject);
    });
    assert.ok(chunks.length >= 2, 'SSE was buffered');
    assert.match(chunks.join(''), /first[\s\S]*second/);
    await new Promise((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port,
        headers: { ...base, origin: `https://${config.publicHost}`, connection: 'Upgrade', upgrade: 'websocket' } });
      req.on('upgrade', (_res, socket) => {
        socket.on('data', bytes => { assert.equal(bytes.toString(), 'fixture-echo'); socket.destroy(); resolve(); });
        socket.write('fixture-echo');
      });
      req.on('response', () => reject(new Error('Upgrade rejected')));
      req.on('error', reject); req.end();
    });
    assert.equal(calls, 2);
  } finally {
    proxy.closeAllConnections(); proxy.close();
    upstream.closeAllConnections(); upstream.close();
  }
});
