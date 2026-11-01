import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { once } from 'node:events';
import { allowed, createProxy, createTailnetAuthorizer, isTailnetMember,
  readPrivateConfig, start, validateConfig } from '../web-proxy.mjs';

const example = JSON.parse(fs.readFileSync(new URL('../proxy.example.json', import.meta.url)));
const stirling = validateConfig(example)[0];
const base = validateConfig(example)[1];
const fixtureIP = '100.64.1.2';
function headers(config = base) {
  return { host: config.publicHost, 'x-forwarded-proto': 'https',
    'x-forwarded-host': config.publicHost, 'x-forwarded-for': fixtureIP,
    'tailscale-user-login': config.ownerLogin };
}
function memberStatus() {
  return { BackendState: 'Running', CurrentTailnet: { StableID: example.tailnet.stableID,
    MagicDNSSuffix: example.tailnet.dnsSuffix }, Peer: {
    fixture: { DNSName: `fixture.${example.tailnet.dnsSuffix}.`, TailscaleIPs: [fixtureIP],
      InNetworkMap: true, UserID: 0, Tags: ['tag:server'] },
  } };
}
function request(changes = {}, config = base) {
  return { headers: headers(config), method: 'GET', url: '/',
    socket: { remoteAddress: '127.0.0.1' }, ...changes };
}
async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return server.address().port;
}
async function fixture(handler, overrides = {}, upgrade, readStatus = async () => memberStatus()) {
  const backend = http.createServer(handler);
  const sockets = new Set();
  backend.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  if (upgrade) backend.on('upgrade', upgrade);
  const upstreamPort = await listen(backend);
  const config = { ...base, maxBodyBytes: 1024, requestTimeoutMs: 2000, ...overrides };
  // Only tests replace the transport; the production route still names its exact approved endpoint.
  const requestUpstream = (options, callback) => {
    assert.equal(options.hostname, config.upstreamHost);
    assert.equal(options.port, config.upstreamPort);
    return http.request({ ...options, hostname: '127.0.0.1', port: upstreamPort }, callback);
  };
  const proxy = createProxy(config, { requestUpstream,
    authorizeTailnet: createTailnetAuthorizer({ readStatus, cacheMs: 0 }) });
  const port = await listen(proxy);
  return { port, config, backend, proxy, async close() {
    await proxy.shutdown();
    for (const socket of sockets) socket.destroy();
    backend.closeAllConnections();
    await new Promise(resolve => backend.close(resolve));
  } };
}
function call(port, options = {}, chunks = []) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port,
      headers: headers(), ...options }, res => {
      const bytes = [];
      res.on('data', chunk => bytes.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers,
        body: Buffer.concat(bytes).toString() }));
      res.on('error', reject);
    });
    req.on('error', reject);
    for (const chunk of chunks) req.write(chunk);
    req.end();
  });
}
function rawCall(port, raw) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1', () => socket.write(raw));
    const chunks = [];
    socket.on('data', data => chunks.push(data));
    socket.on('end', () => resolve(Buffer.concat(chunks).toString()));
    socket.on('error', reject);
  });
}

test('configuration restricts identity, exact port pairs, hosts, keys and limits', () => {
  assert.equal(validateConfig(example).length, 2);
  const bad = [
    value => { value.ownerLogin = ''; },
    value => { value.ownerLogin = 'owner@example.invalid,other'; },
    value => { delete value.tailnet; },
    value => { value.tailnet.stableID = ''; },
    value => { value.tailnet.dnsSuffix = 'other.ts.net'; },
    value => { value.routes.pop(); },
    value => { value.routes[1] = structuredClone(value.routes[0]); },
    value => { value.routes[0].listenPort = 80; },
    value => { value.routes[0].upstreamPort = 8141; },
    value => { value.routes[0].upstreamPort = 8140; },
    value => { value.routes[0].upstreamHost = '127.0.0.1'; },
    value => { value.routes[0].upstreamHost = '10.254.230.51'; },
    value => { value.routes[0].upstreamHost = 'stirling-pdf'; },
    value => { value.routes[1].upstreamHost = '10.254.230.50'; },
    value => { value.routes[1].upstreamHost = 'localhost'; },
    value => { delete value.routes[0].upstreamHost; },
    value => { value.routes[0].publicHost = '9950x.taile832fe.ts.net:8453'; },
    value => { value.routes[0].publicHost = 'https://9950x.taile832fe.ts.net:8452'; },
    value => { value.routes[0].publicHost = 'owner@9950x.taile832fe.ts.net:8452'; },
    value => { value.routes[0].publicHost = 'attacker.example:8452'; },
    value => { value.routes[0].publicHost = null; },
    value => { value.routes[0].maxBodyBytes = 0; },
    value => { value.routes[0].maxBodyBytes = 2 ** 40; },
    value => { value.routes[0].requestTimeoutMs = 0; },
    value => { value.routes[0].requestTimeoutMs = 7200001; },
    value => { value.routes[1].requestTimeoutMs = 1800001; },
    value => { value.routes[1].maxBodyBytes = 33554433; },
    value => { value.routes[0].upstreamUrl = 'http://untrusted.invalid'; },
    value => { value.listenAddress = '0.0.0.0'; },
  ];
  for (const mutate of bad) {
    const value = structuredClone(example);
    mutate(value);
    assert.throws(() => validateConfig(value), /Invalid private proxy/);
  }
});

test('large Stirling limits validate without allocating a large upload', () => {
  const config = validateConfig(example)[0];
  assert.equal(config.maxBodyBytes, 2164260864);
  assert.equal(config.requestTimeoutMs, 7200000);
  const upper = structuredClone(example);
  upper.routes[0].maxBodyBytes = 3 * 1024 ** 3;
  assert.equal(validateConfig(upper)[0].maxBodyBytes, 3 * 1024 ** 3);
  upper.routes[0].maxBodyBytes++;
  assert.throws(() => validateConfig(upper), /Invalid private proxy/);
});

test('membership includes tagged peers, different member users and Self; excludes shares, stale nodes and wrong tailnets', () => {
  const valid = memberStatus();
  assert.equal(isTailnetMember(valid, fixtureIP, stirling), true, 'tagged server');
  valid.Peer.fixture.TailscaleIPs.push('fd7a:115c:a1e0::1234');
  assert.equal(isTailnetMember(valid, 'fd7a:115c:a1e0::1234', stirling), true, 'assigned IPv6');
  const differentUser = structuredClone(valid);
  differentUser.Peer.fixture.UserID = 123;
  delete differentUser.Peer.fixture.Tags;
  differentUser.Peer.fixture.Online = false; // This cached flag is not connection authentication.
  assert.equal(isTailnetMember(differentUser, fixtureIP, stirling), true, 'other member device');
  const self = structuredClone(valid);
  self.Self = self.Peer.fixture; self.Peer = {};
  assert.equal(isTailnetMember(self, fixtureIP, stirling), true, 'self');
  const bad = [
    value => { value.BackendState = 'Stopped'; },
    value => { value.CurrentTailnet.StableID = 'other-tailnet'; },
    value => { value.CurrentTailnet.MagicDNSSuffix = 'other.ts.net'; },
    value => { value.Peer.fixture.InNetworkMap = false; },
    value => { value.Peer.fixture.ShareeNode = true; },
    value => { value.Peer.fixture.AltSharerUserID = 456; },
    value => { value.Peer.fixture.Expired = true; },
    value => { value.Peer.fixture.KeyExpiry = '2000-01-01T00:00:00Z'; },
    value => { value.Peer.fixture.KeyExpiry = 'invalid'; },
    value => { value.Peer.fixture.DNSName = 'fixture.other.ts.net.'; },
    value => { value.Peer.fixture.DNSName += 'evil.invalid'; },
    value => { value.Peer.fixture.TailscaleIPs = ['100.64.9.9']; value.Peer.fixture.AllowedIPs = ['100.64.0.0/10']; },
    value => { value.Peer = {}; },
  ];
  for (const mutate of bad) {
    const status = structuredClone(valid); mutate(status);
    assert.equal(isTailnetMember(status, fixtureIP, stirling), false);
  }
  for (const ip of [undefined, '203.0.113.1', `${fixtureIP}, 203.0.113.1`, `${fixtureIP}:1234`]) {
    assert.equal(isTailnetMember(valid, ip, stirling), false);
  }
});

test('membership lookup shares concurrent reads, refreshes after two seconds and fails closed after daemon failure', async () => {
  let now = 1000;
  let reads = 0;
  let status = memberStatus();
  let unavailable = false;
  const authorize = createTailnetAuthorizer({ clock: () => now, readStatus: async () => {
    reads++;
    if (unavailable) throw new Error('fixture daemon unavailable');
    return structuredClone(status);
  } });
  const req = request({}, stirling);
  assert.deepEqual(await Promise.all(Array.from({ length: 12 }, () => authorize(req, stirling))), Array(12).fill(true));
  assert.equal(reads, 1);
  status.Peer = {};
  now = 2999;
  assert.equal(await authorize(req, stirling), true);
  now = 3000;
  assert.equal(await authorize(req, stirling), false);
  assert.equal(reads, 2);
  status = memberStatus(); now = 5000;
  assert.equal(await authorize(req, stirling), true);
  unavailable = true; now = 7000;
  assert.equal(await authorize(req, stirling), false, 'must not reuse stale grant');
  assert.equal(await authorize(req, stirling), false);
  const previous = reads;
  for (const h of [{ 'x-forwarded-for': `${fixtureIP}, 203.0.113.1` },
    { 'x-forwarded-for': '100.64.1.2:1234' }, { 'x-forwarded-host': 'evil.invalid' }]) {
    assert.equal(await authorize(request({ headers: { ...headers(stirling), ...h } }), stirling), false);
  }
  assert.equal(reads, previous, 'malformed forwarded values should not reach the daemon');
});

test('tagged Stirling ingress succeeds without identity; foreign/shared/Funnel ingress never reaches the app', async () => {
  let calls = 0;
  let status = memberStatus();
  const f = await fixture((_req, res) => { calls++; res.end('member'); }, stirling, undefined, async () => status);
  const h = headers(stirling); delete h['tailscale-user-login'];
  try {
    assert.equal((await call(f.port, { headers: h })).status, 200);
    assert.equal(calls, 1);
    for (const change of [{ 'x-forwarded-for': '203.0.113.77' },
      { 'x-forwarded-for': `${fixtureIP}, 203.0.113.77` },
      { 'x-forwarded-for': '' }, { 'x-forwarded-host': 'forged.invalid' },
      { 'tailscale-funnel-request': '?1' }, { 'tailscale-funnel-request': '' },
      { origin: 'https://evil.invalid' }]) {
      assert.equal((await call(f.port, { headers: { ...h, ...change } })).status, 403);
    }
    for (const [name, value] of [['X-Forwarded-For', fixtureIP], ['X-Forwarded-Host', stirling.publicHost]]) {
      const rows = Object.entries(h).map(([key, item]) => `${key}: ${item}`);
      const raw = await rawCall(f.port, `GET / HTTP/1.1\r\n${rows.join('\r\n')}\r\n${name}: ${value}\r\nConnection: close\r\n\r\n`);
      assert.match(raw, /^HTTP\/1.1 403 /);
    }
    status = memberStatus(); status.Peer.fixture.ShareeNode = true;
    assert.equal((await call(f.port, { headers: h })).status, 403);
    status = memberStatus(); status.Peer.fixture.DNSName = 'fixture.external.ts.net.';
    assert.equal((await call(f.port, { headers: h })).status, 403);
    assert.equal(calls, 1);
  } finally { await f.close(); }
  const cd = await fixture((_req, res) => res.end('owner'));
  try {
    const noOwner = headers(); delete noOwner['tailscale-user-login'];
    assert.equal((await call(cd.port, { headers: noOwner })).status, 403);
    assert.equal((await call(cd.port, { headers: { ...headers(), 'tailscale-user-login': 'other@example.invalid' } })).status, 403);
  } finally { await cd.close(); }
});

test('native Stirling API writes work without browser headers; browser and changedetection CSRF rules remain', async () => {
  let calls = 0;
  const f = await fixture((req, res) => {
    calls++; let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => res.end(body));
  }, stirling);
  const h = headers(stirling); delete h['tailscale-user-login'];
  try {
    assert.equal((await call(f.port, { method: 'POST', path: '/api/v1/general/merge-pdfs', headers: h }, ['fixture'])).status, 200);
    assert.equal((await call(f.port, { method: 'POST', path: '/api/v1/general/merge-pdfs',
      headers: { ...h, origin: `https://${stirling.publicHost}`, 'sec-fetch-site': 'same-origin' } }, ['browser'])).status, 200);
    for (const change of [{ origin: 'https://evil.invalid' }, { origin: 'null' },
      { 'sec-fetch-site': 'same-origin' }, { 'sec-fetch-mode': 'cors' },
      { 'sec-fetch-site': 'cross-site' }]) {
      assert.equal((await call(f.port, { method: 'POST', path: '/api/v1/general/merge-pdfs',
        headers: { ...h, ...change } }, ['blocked'])).status, 403);
    }
    assert.equal((await call(f.port, { method: 'POST', path: '/settings', headers: h }, ['blocked'])).status, 403);
    assert.equal(calls, 2);
  } finally { await f.close(); }
  const cd = await fixture((_req, res) => res.end('unexpected'));
  try { assert.equal((await call(cd.port, { method: 'POST', path: '/api/v1/watch' })).status, 403); }
  finally { await cd.close(); }
});

test('unavailable tailnet daemon and clients disconnected during authorization never reach Stirling', async () => {
  let calls = 0;
  const failed = await fixture((_req, res) => { calls++; res.end('unexpected'); }, stirling,
    undefined, async () => { throw new Error('fixture failure'); });
  try { assert.equal((await call(failed.port, { headers: headers(stirling) })).status, 403); }
  finally { await failed.close(); }
  let release;
  let lookupStarted;
  const started = new Promise(resolve => { lookupStarted = resolve; });
  const blocked = new Promise(resolve => { release = resolve; });
  const f = await fixture((_req, res) => { calls++; res.end('unexpected'); }, stirling,
    undefined, async () => { lookupStarted(); return blocked; });
  try {
    const connectionClosed = new Promise(resolve => f.proxy.once('connection', socket => socket.once('close', resolve)));
    const req = http.get({ hostname: '127.0.0.1', port: f.port, headers: headers(stirling) });
    req.on('error', () => {});
    await started;
    req.destroy();
    await connectionClosed;
    release(memberStatus());
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 0);
  } finally { release(memberStatus()); await f.close(); }
});

test('private config requires a small regular owner-readable 0600 file and rejects symlinks', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'private-proxy-config-'));
  const filename = path.join(root, 'proxy.json');
  try {
    fs.writeFileSync(filename, JSON.stringify(example), { mode: 0o600 });
    assert.equal(readPrivateConfig(filename).length, 2);
    fs.chmodSync(filename, 0o644);
    assert.throws(() => readPrivateConfig(filename), /configuration file/);
    fs.chmodSync(filename, 0o600);
    fs.symlinkSync(filename, path.join(root, 'link'));
    assert.throws(() => readPrivateConfig(path.join(root, 'link')));
    fs.writeFileSync(filename, ' '.repeat(65537));
    assert.throws(() => readPrivateConfig(filename), /configuration file/);
    fs.writeFileSync(filename, '{');
    assert.throws(() => readPrivateConfig(filename));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('owner, route, origin and browser-context boundaries fail closed', () => {
  assert.equal(allowed(request(), base), true);
  assert.equal(allowed(request({ headers: { ...headers(), 'tailscale-user-login': base.ownerLogin.toUpperCase() } }), base), true);
  for (const key of ['host', 'x-forwarded-proto', 'tailscale-user-login']) {
    const h = headers(); delete h[key];
    assert.equal(allowed(request({ headers: h }), base), false, key);
  }
  for (const [name, value] of [['host', 'other.taile832fe.ts.net:8452'],
    ['host', stirling.publicHost], ['x-forwarded-proto', 'http'],
    ['tailscale-user-login', 'other@example.invalid'], ['origin', 'https://evil.invalid'],
    ['origin', 'null'], ['origin', ''], ['sec-fetch-site', 'cross-site']]) {
    assert.equal(allowed(request({ headers: { ...headers(), [name]: value } }), base), false, name);
  }
  assert.equal(allowed(request({ socket: { remoteAddress: '100.64.0.2' } }), base), false);
  for (const url of ['https://evil.invalid/', '//evil.invalid/']) assert.equal(allowed(request({ url }), base), false);
  assert.equal(allowed(request({ method: 'TRACE' }), base), false);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
    assert.equal(allowed(request({ method }), base), false);
    assert.equal(allowed(request({ method, headers: { ...headers(), origin: `https://${base.publicHost}` } }), base), true);
  }
  assert.equal(allowed(request(), base, true), false);
  assert.equal(allowed(request({ headers: { ...headers(), origin: `https://${base.publicHost}` } }), base, true), true);
  const navigation = { ...headers(), 'sec-fetch-site': 'cross-site', 'sec-fetch-mode': 'navigate',
    'sec-fetch-dest': 'document', 'sec-fetch-user': '?1' };
  assert.equal(allowed(request({ headers: navigation }), base), true, 'dashboard link');
  assert.equal(allowed(request({ headers: { ...navigation, 'sec-fetch-dest': 'iframe' } }), base), false);
  assert.equal(allowed(request({ headers: { ...navigation, 'sec-fetch-user': undefined } }), base), false);
  assert.equal(allowed(request({ method: 'POST', headers: { ...navigation, origin: `https://${base.publicHost}` } }), base), false);
});

test('rejected live requests and duplicate gate headers never contact the backend', async () => {
  let calls = 0;
  const f = await fixture((_req, res) => { calls++; res.end('backend'); });
  try {
    for (const options of [
      { headers: {} },
      { headers: { ...headers(), 'tailscale-user-login': 'other@example.invalid' } },
      { headers: { ...headers(), origin: 'https://evil.invalid' } },
      { method: 'POST' },
      { headers: { ...headers(), host: stirling.publicHost } },
    ]) assert.equal((await call(f.port, options)).status, 403);
    for (const name of ['Host', 'X-Forwarded-Proto', 'Tailscale-User-Login', 'Origin']) {
      const value = name === 'Host' ? base.publicHost : name === 'X-Forwarded-Proto' ? 'https'
        : name === 'Origin' ? `https://${base.publicHost}` : base.ownerLogin;
      const rows = Object.entries({ ...headers(), origin: `https://${base.publicHost}` })
        .map(([key, item]) => `${key}: ${item}`);
      const raw = await rawCall(f.port, `GET / HTTP/1.1\r\n${rows.join('\r\n')}\r\n${name}: ${value}\r\nConnection: close\r\n\r\n`);
      assert.match(raw, /^HTTP\/1.1 403 /, name);
    }
    assert.equal(calls, 0);
  } finally { await f.close(); }
});

test('forwarding rebuilds trusted fields while preserving app auth, cookies and responses', async () => {
  let received;
  const f = await fixture((req, res) => {
    received = req.headers;
    res.writeHead(201, { 'set-cookie': ['a=one; Secure; SameSite=Strict', 'b=two; HttpOnly'],
      'content-type': 'text/plain', 'x-frame-options': 'SAMEORIGIN' });
    res.end('application response');
  });
  try {
    const result = await call(f.port, { headers: { ...headers(), authorization: 'Bearer synthetic-fixture',
      cookie: 'existing=fixture', 'proxy-authorization': 'discard', 'tailscale-user-name': 'discard',
      'tailscale-app-capabilities': 'discard', 'x-forwarded-for': '203.0.113.1',
      'x-forwarded-host': 'evil.invalid', forwarded: 'host=evil.invalid;proto=http',
      connection: 'close, x-remove-me', 'x-remove-me': 'discard' } });
    assert.equal(result.status, 201);
    assert.equal(result.body, 'application response');
    assert.equal(received.authorization, 'Bearer synthetic-fixture');
    assert.equal(received.cookie, 'existing=fixture');
    assert.equal(received.host, base.publicHost);
    assert.equal(received['x-forwarded-host'], base.publicHost);
    assert.equal(received['x-forwarded-proto'], 'https');
    for (const key of ['tailscale-user-login', 'tailscale-user-name', 'tailscale-app-capabilities',
      'x-forwarded-for', 'forwarded', 'proxy-authorization', 'x-remove-me']) assert.equal(received[key], undefined, key);
    assert.equal(result.headers['set-cookie'].length, 2);
    assert.equal(result.headers['x-frame-options'], 'DENY');
    assert.equal(result.headers['referrer-policy'], 'no-referrer');
    assert.equal(result.headers['cache-control'], 'no-store');
  } finally { await f.close(); }
});

test('both named routes retain their own exact public identity and approved backend destination', async () => {
  for (const config of validateConfig(example)) {
    const f = await fixture((req, res) => {
      assert.equal(req.headers.host, config.publicHost);
      assert.equal(req.headers['x-forwarded-host'], config.publicHost);
      res.end(config.name);
    }, config);
    try {
      const result = await call(f.port, { headers: headers(config) });
      assert.equal(result.status, 200);
      assert.equal(result.body, config.name);
      if (config.name === 'stirling-pdf') {
        assert.match(result.headers['content-security-policy'], /connect-src 'self' blob:/);
        assert.match(result.headers['content-security-policy'], /img-src 'self' data: blob:/);
        assert.match(result.headers['content-security-policy'], /font-src 'self' data:/);
        assert.match(result.headers['content-security-policy'], /worker-src 'self' blob:/);
        assert.doesNotMatch(result.headers['content-security-policy'], /script-src|default-src|https:/);
      } else assert.equal(result.headers['content-security-policy'], undefined);
      const other = validateConfig(example).find(item => item.name !== config.name);
      assert.equal((await call(f.port, { headers: headers(other) })).status, 403);
    } finally { await f.close(); }
  }
});

test('Stirling CSP is additional to any upstream policy; changedetection policy is preserved verbatim', async () => {
  for (const config of validateConfig(example)) {
    const f = await fixture((_req, res) => {
      res.setHeader('content-security-policy', "frame-ancestors 'none'");
      res.end('policy');
    }, config);
    try {
      const result = await call(f.port, { headers: headers(config) });
      const policy = result.headers['content-security-policy'];
      assert.match(policy, /frame-ancestors 'none'/);
      if (config.name === 'stirling-pdf') assert.match(policy, /connect-src 'self' blob:/);
      else assert.equal(policy, "frame-ancestors 'none'");
    } finally { await f.close(); }
  }
});

test('SSE first event reaches the client before the upstream finishes', async () => {
  let continueStream;
  const clientReadFirst = new Promise(resolve => { continueStream = resolve; });
  const f = await fixture(async (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: first\n\n');
    await clientReadFirst;
    res.end('data: second\n\n');
  });
  try {
    const result = await new Promise((resolve, reject) => {
      http.get({ hostname: '127.0.0.1', port: f.port, headers: headers() }, res => {
        let body = '';
        res.on('data', bytes => { body += bytes; if (body.includes('first')) continueStream(); });
        res.on('end', () => resolve(body));
        res.on('error', reject);
      }).on('error', reject);
    });
    assert.match(result, /first[\s\S]*second/);
  } finally { continueStream(); await f.close(); }
});

test('upload streams before completion, accepts its exact boundary and preflights oversized Content-Length', async () => {
  let calls = 0;
  let firstBytes;
  const backendRead = new Promise(resolve => { firstBytes = resolve; });
  const f = await fixture((req, res) => {
    calls++;
    let body = '';
    req.on('data', chunk => { body += chunk; firstBytes(); });
    req.on('end', () => res.end(body));
  }, { maxBodyBytes: 16 });
  try {
    const h = { ...headers(), origin: `https://${base.publicHost}`, 'content-length': '16' };
    const result = await new Promise((resolve, reject) => {
      const req = http.request({ hostname: '127.0.0.1', port: f.port, method: 'POST', headers: h }, res => {
        let body = '';
        res.on('data', chunk => { body += chunk; });
        res.on('end', () => resolve({ status: res.statusCode, body }));
      });
      req.on('error', reject);
      req.write('12345678');
      backendRead.then(() => req.end('abcdefgh'));
    });
    assert.deepEqual(result, { status: 200, body: '12345678abcdefgh' });
    const oversized = await call(f.port, { method: 'POST', headers: { ...h, 'content-length': '17' } }, ['12345678abcdefghi']);
    assert.equal(oversized.status, 413);
    assert.equal(calls, 1, 'oversized Content-Length reached backend');
  } finally { firstBytes(); await f.close(); }
});

test('chunked upload crossing its boundary is rejected and never completes upstream', async () => {
  let completed = 0;
  let bytesReceived = 0;
  const f = await fixture((req, res) => {
    req.on('data', chunk => { bytesReceived += chunk.length; });
    req.on('end', () => { completed++; res.end('unexpected'); });
  }, { maxBodyBytes: 16 });
  try {
    const result = await call(f.port, { method: 'POST', headers: { ...headers(),
      origin: `https://${base.publicHost}`, 'transfer-encoding': 'chunked' } }, ['12345678abcdefgh', 'x']);
    assert.equal(result.status, 413);
    assert.equal(completed, 0);
    assert.ok(bytesReceived <= 16);
  } finally { await f.close(); }
});

test('total request deadline bounds a hung application', async () => {
  const f = await fixture(() => {}, { requestTimeoutMs: 100 });
  try {
    const began = performance.now();
    assert.equal((await call(f.port)).status, 504);
    assert.ok(performance.now() - began < 1500);
  } finally { await f.close(); }
});

test('unavailable backend produces a generic response with no private request values', async () => {
  const f = await fixture((_req, res) => res.end('unused'));
  await new Promise(resolve => f.backend.close(resolve));
  try {
    const result = await call(f.port, { headers: { ...headers(), authorization: 'Bearer synthetic-private-value' } });
    assert.equal(result.status, 502);
    assert.doesNotMatch(result.body, /synthetic|example|8140|127\.0\.0\.1/);
  } finally { await f.close(); }
});

function upgradeRequest(port, h = {}, config = base) {
  return http.request({ hostname: '127.0.0.1', port, headers: { ...headers(config),
    origin: `https://${config.publicHost}`, connection: 'Upgrade', upgrade: 'websocket',
    'sec-websocket-version': '13', 'sec-websocket-key': 'dGhlIHNhbXBsZSBub25jZQ==', ...h } });
}
function websocketAccept(req, socket) {
  const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key']
    + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
}

test('WebSocket requires origin/owner, preserves app auth, and transports a real masked frame', async () => {
  let upgrades = 0;
  const f = await fixture((_req, res) => res.end('unused'), {}, (req, socket) => {
    upgrades++;
    assert.equal(req.headers.authorization, 'Bearer ws-fixture');
    assert.equal(req.headers.cookie, 'ws=fixture');
    assert.equal(req.headers['tailscale-user-login'], undefined);
    assert.equal(req.headers['x-forwarded-proto'], 'https');
    websocketAccept(req, socket);
    socket.once('data', frame => {
      assert.equal(frame[0], 0x81);
      assert.ok(frame[1] & 0x80);
      const length = frame[1] & 0x7f;
      const payload = Buffer.from(frame.subarray(6, 6 + length));
      for (let index = 0; index < length; index++) payload[index] ^= frame[2 + index % 4];
      socket.write(Buffer.concat([Buffer.from([0x81, length]), payload]));
    });
  });
  try {
    for (const h of [{ origin: 'https://evil.invalid' }, { 'tailscale-user-login': 'other@example.invalid' }]) {
      const status = await new Promise((resolve, reject) => {
        const req = upgradeRequest(f.port, h);
        req.on('response', res => { res.resume(); resolve(res.statusCode); });
        req.on('upgrade', (_res, socket) => { socket.destroy(); reject(new Error('Unexpected upgrade')); });
        req.on('error', reject); req.end();
      });
      assert.equal(status, 403);
    }
    const missingOrigin = await rawCall(f.port, `GET / HTTP/1.1\r\nHost: ${base.publicHost}\r\nX-Forwarded-Proto: https\r\nTailscale-User-Login: ${base.ownerLogin}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n`);
    assert.match(missingOrigin, /^HTTP\/1.1 403 /);
    assert.equal(upgrades, 0);
    const echoed = await new Promise((resolve, reject) => {
      const req = upgradeRequest(f.port, { authorization: 'Bearer ws-fixture', cookie: 'ws=fixture' });
      req.on('upgrade', (_res, socket) => {
        const payload = Buffer.from('hello proxy');
        const mask = Buffer.from([1, 2, 3, 4]);
        const encoded = Buffer.from(payload.map((byte, index) => byte ^ mask[index % 4]));
        socket.once('data', frame => { resolve(frame.subarray(2).toString()); socket.destroy(); });
        socket.on('error', reject);
        socket.write(Buffer.concat([Buffer.from([0x81, 0x80 | payload.length]), mask, encoded]));
      });
      req.on('response', res => { res.resume(); reject(new Error('WebSocket rejected')); });
      req.on('error', reject); req.end();
    });
    assert.equal(echoed, 'hello proxy');
    assert.equal(upgrades, 1);
  } finally { await f.close(); }
});

test('WebSocket lifetime is bounded, including a silent upgraded connection', async () => {
  const f = await fixture((_req, res) => res.end('unused'), { requestTimeoutMs: 100 }, websocketAccept);
  try {
    const began = performance.now();
    await new Promise((resolve, reject) => {
      const req = upgradeRequest(f.port);
      req.on('upgrade', (_res, socket) => { socket.on('close', resolve); socket.on('error', reject); });
      req.on('error', reject); req.end();
    });
    assert.ok(performance.now() - began < 1500);
  } finally { await f.close(); }
});

test('tagged Stirling WebSocket accepts same-origin members and rejects shared nodes before upgrading', async () => {
  let upgrades = 0;
  let status = memberStatus();
  const f = await fixture((_req, res) => res.end('unused'), stirling,
    (req, socket) => { upgrades++; websocketAccept(req, socket); }, async () => status);
  try {
    await new Promise((resolve, reject) => {
      const req = upgradeRequest(f.port, {}, stirling);
      req.removeHeader('tailscale-user-login');
      req.on('upgrade', (_res, socket) => { socket.destroy(); resolve(); });
      req.on('response', res => { res.resume(); reject(new Error('Tagged member upgrade denied')); });
      req.on('error', reject); req.end();
    });
    status.Peer.fixture.ShareeNode = true;
    const denied = await new Promise((resolve, reject) => {
      const req = upgradeRequest(f.port, {}, stirling);
      req.removeHeader('tailscale-user-login');
      req.on('response', res => { res.resume(); resolve(res.statusCode); });
      req.on('upgrade', (_res, socket) => { socket.destroy(); reject(new Error('Shared node was admitted')); });
      req.on('error', reject); req.end();
    });
    assert.equal(denied, 403);
    assert.equal(upgrades, 1);
  } finally { await f.close(); }
});

test('multi-route listener startup binds only IPv4 loopback and cleans up a partial failure', async () => {
  const servers = await start(validateConfig(example).map(config => ({ ...config, listenPort: 0 })));
  const firstPorts = servers.map(server => server.address().port);
  try {
    for (const server of servers) assert.equal(server.address().address, '127.0.0.1');
  } finally { await Promise.all(servers.map(server => server.shutdown())); }
  const blocker = net.createServer();
  const blockedPort = await listen(blocker);
  try {
    await assert.rejects(start([
      { ...base, listenPort: firstPorts[0] },
      { ...validateConfig(example)[1], listenPort: blockedPort },
    ]), { code: 'EADDRINUSE' });
    // The first listener must have been closed when the second could not bind.
    const probe = net.createServer();
    await new Promise((resolve, reject) => {
      probe.once('error', reject); probe.listen(firstPorts[0], '127.0.0.1', resolve);
    });
    await new Promise(resolve => probe.close(resolve));
  } finally { await new Promise(resolve => blocker.close(resolve)); }
});
