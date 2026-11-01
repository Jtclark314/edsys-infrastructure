// Private Tailscale Serve bridge: member access for Stirling, owner access for changedetection.
import http from 'node:http';
import fs from 'node:fs';
import { Transform } from 'node:stream';
import { isIP } from 'node:net';
import { pathToFileURL } from 'node:url';

const CONFIG_PATH = '/mnt/ai-store/edsys-private-services/proxy.json';
const MAPPINGS = {
  'stirling-pdf': [8142, '10.254.230.50', 8080, 8452],
  changedetection: [8143, '127.0.0.1', 8141, 8453],
};
const LOOPBACK = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);
const METHODS = new Set(['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']);
const HOP_HEADERS = new Set(['connection', 'proxy-connection', 'keep-alive',
  'transfer-encoding', 'upgrade', 'trailer', 'te', 'proxy-authenticate', 'proxy-authorization']);
const GATE_HEADERS = new Set(['host', 'x-forwarded-proto', 'x-forwarded-host', 'x-forwarded-for',
  'tailscale-user-login', 'tailscale-funnel-request', 'origin']);
const STIRLING_CSP = "connect-src 'self' blob:; img-src 'self' data: blob:; font-src 'self' data:; worker-src 'self' blob:";

function exactKeys(object, expected) {
  return object && typeof object === 'object' && !Array.isArray(object)
    && Object.keys(object).length === expected.length
    && expected.every(key => Object.hasOwn(object, key));
}

export function validateConfig(config) {
  if (!exactKeys(config, ['ownerLogin', 'tailnet', 'routes'])
      || typeof config.ownerLogin !== 'string'
      || !/^[A-Za-z0-9][A-Za-z0-9@._+:-]{0,253}$/.test(config.ownerLogin)
      || !exactKeys(config.tailnet, ['stableID', 'dnsSuffix'])
      || typeof config.tailnet.stableID !== 'string'
      || !/^[A-Za-z0-9_-]{1,128}$/.test(config.tailnet.stableID)
      || typeof config.tailnet.dnsSuffix !== 'string'
      || !/^[a-z0-9-]+\.ts\.net$/.test(config.tailnet.dnsSuffix)
      || !Array.isArray(config.routes) || config.routes.length !== 2) {
    throw new Error('Invalid private proxy configuration');
  }
  const seen = new Set();
  for (const route of config.routes) {
    if (!exactKeys(route, ['name', 'listenPort', 'upstreamHost', 'upstreamPort', 'publicHost',
      'maxBodyBytes', 'requestTimeoutMs']) || !Object.hasOwn(MAPPINGS, route.name)
      || seen.has(route.name)) throw new Error('Invalid private proxy route');
    seen.add(route.name);
    const [listenPort, upstreamHost, upstreamPort, httpsPort] = MAPPINGS[route.name];
    const expectedSuffix = `:${httpsPort}`;
    const hostname = typeof route.publicHost === 'string'
      ? route.publicHost.slice(0, -expectedSuffix.length) : '';
    if (route.listenPort !== listenPort || route.upstreamPort !== upstreamPort
        || route.upstreamHost !== upstreamHost
        || typeof route.publicHost !== 'string'
        || !route.publicHost.endsWith(expectedSuffix)
        || hostname.length > 253
        || !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(hostname)
        || !hostname.endsWith(`.${config.tailnet.dnsSuffix}`)
        || !Number.isSafeInteger(route.maxBodyBytes) || route.maxBodyBytes < 1
        || route.maxBodyBytes > (route.name === 'stirling-pdf' ? 3 * 1024 * 1024 * 1024 : 32 * 1024 * 1024)
        || !Number.isSafeInteger(route.requestTimeoutMs) || route.requestTimeoutMs < 1000
        || route.requestTimeoutMs > (route.name === 'stirling-pdf' ? 7200000 : 1800000)) {
      throw new Error('Invalid private proxy route');
    }
  }
  return config.routes.map(route => Object.freeze({ ...route, ownerLogin: config.ownerLogin,
    tailnetStableID: config.tailnet.stableID, tailnetDnsSuffix: config.tailnet.dnsSuffix }));
}

export function readPrivateConfig(filename = CONFIG_PATH) {
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || (stat.mode & 0o777) !== 0o600 || stat.uid !== process.getuid()
        || stat.size > 65536) throw new Error('Invalid private proxy configuration file');
    return validateConfig(JSON.parse(fs.readFileSync(fd, 'utf8')));
  } finally {
    fs.closeSync(fd);
  }
}

function repeatedGateHeader(req) {
  const seen = new Set();
  for (let index = 0; index < (req.rawHeaders?.length ?? 0); index += 2) {
    const name = req.rawHeaders[index].toLowerCase();
    if (GATE_HEADERS.has(name)) {
      if (seen.has(name)) return true;
      seen.add(name);
    }
  }
  return false;
}

export function allowed(req, config, websocket = false) {
  const h = req.headers;
  const origin = `https://${config.publicHost}`;
  const crossSiteNavigation = ['GET', 'HEAD'].includes(req.method)
    && h['sec-fetch-mode'] === 'navigate' && h['sec-fetch-dest'] === 'document'
    && h['sec-fetch-user'] === '?1';
  const machineApiWrite = config.name === 'stirling-pdf' && !websocket
    && typeof req.url === 'string' && req.url.startsWith('/api/')
    && !Object.hasOwn(h, 'origin') && !Object.keys(h).some(name => name.startsWith('sec-fetch-'));
  return LOOPBACK.has(req.socket.remoteAddress) && !repeatedGateHeader(req)
    && METHODS.has(req.method)
    && h.host === config.publicHost && h['x-forwarded-proto'] === 'https'
    && !Object.hasOwn(h, 'tailscale-funnel-request')
    && (config.name === 'stirling-pdf' || (typeof h['tailscale-user-login'] === 'string'
      && h['tailscale-user-login'].toLowerCase() === config.ownerLogin.toLowerCase()))
    && (!Object.hasOwn(h, 'origin') || h.origin === origin)
    && (h['sec-fetch-site'] !== 'cross-site' || crossSiteNavigation)
    && (!(websocket || !['GET', 'HEAD'].includes(req.method)) || h.origin === origin || machineApiWrite)
    && (!websocket || req.method === 'GET')
    && typeof req.url === 'string' && req.url.startsWith('/') && !req.url.startsWith('//');
}

// Read only the local daemon; no CLI subprocess, remote API key or stored peer inventory.
export function readTailnetStatus() {
  return new Promise((resolve, reject) => {
    let bytes = 0;
    const chunks = [];
    const req = http.request({ socketPath: '/var/run/tailscale/tailscaled.sock',
      hostname: 'local-tailscaled.sock', path: '/localapi/v0/status?peers=true', method: 'GET' }, res => {
      if (res.statusCode !== 200) { res.resume(); reject(new Error('Tailnet status unavailable')); return; }
      res.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > 4 * 1024 * 1024) { req.destroy(new Error('Tailnet status exceeds limit')); return; }
        chunks.push(chunk);
      });
      res.on('error', reject);
      res.on('end', () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
        catch { reject(new Error('Invalid tailnet status')); }
      });
    });
    const timer = setTimeout(() => req.destroy(new Error('Tailnet status timed out')), 1000);
    timer.unref();
    req.on('error', reject);
    req.on('close', () => clearTimeout(timer));
    req.end();
  });
}

export function isTailnetMember(status, ip, config, now = Date.now()) {
  if (typeof ip !== 'string' || !isIP(ip) || status?.BackendState !== 'Running'
      || status.CurrentTailnet?.StableID !== config.tailnetStableID
      || status.CurrentTailnet?.MagicDNSSuffix !== config.tailnetDnsSuffix) return false;
  const suffix = `.${config.tailnetDnsSuffix}`;
  const nodes = [status.Self, ...Object.values(status.Peer ?? {})];
  return nodes.some(node => {
    if (!node || node.InNetworkMap !== true || node.ShareeNode === true
        || (node.AltSharerUserID !== undefined && node.AltSharerUserID !== 0)
        || node.Expired === true || !Array.isArray(node.TailscaleIPs)
        || !node.TailscaleIPs.includes(ip) || typeof node.DNSName !== 'string') return false;
    const dnsName = node.DNSName.replace(/\.$/, '');
    const nodeName = dnsName.slice(0, -suffix.length);
    if (!dnsName.endsWith(suffix) || !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(nodeName)) return false;
    if (node.KeyExpiry && node.KeyExpiry !== '0001-01-01T00:00:00Z') {
      const expiry = Date.parse(node.KeyExpiry);
      if (!Number.isFinite(expiry) || expiry <= now) return false;
    }
    return true;
  });
}

export function createTailnetAuthorizer({ readStatus = readTailnetStatus, clock = Date.now, cacheMs = 2000 } = {}) {
  let cached;
  let expires = 0;
  let pending;
  return async (req, config) => {
    const ip = req.headers['x-forwarded-for'];
    // Serve 1.104.4 replaces XFF with one authenticated source IP, not a chain.
    if (typeof ip !== 'string' || !isIP(ip)
        || req.headers['x-forwarded-host'] !== config.publicHost) return false;
    try {
      if (!cached || clock() >= expires) {
        pending ??= Promise.resolve().then(readStatus).then(status => {
          cached = status;
          expires = clock() + cacheMs;
          return status;
        }).finally(() => { pending = undefined; });
        await pending;
      }
      return isTailnetMember(cached, ip, config, clock());
    } catch {
      cached = undefined;
      expires = 0;
      return false;
    }
  };
}

function cleanHeaders(headers) {
  const remove = new Set(HOP_HEADERS);
  for (const name of String(headers.connection ?? '').split(',')) remove.add(name.trim().toLowerCase());
  return Object.fromEntries(Object.entries(headers).filter(([name]) =>
    !remove.has(name) && !name.startsWith('tailscale-')
    && !name.startsWith('x-forwarded-') && name !== 'forwarded'));
}

function upstreamHeaders(req, config, websocket = false) {
  const result = cleanHeaders(req.headers);
  // Host has already passed the exact route gate. Application auth/cookies survive.
  result.host = config.publicHost;
  result['x-forwarded-proto'] = 'https';
  result['x-forwarded-host'] = config.publicHost;
  if (websocket) Object.assign(result, { connection: 'Upgrade', upgrade: 'websocket' });
  return result;
}

function secureCookies(headers) {
  const result = { ...headers };
  const cookies = result['set-cookie'];
  if (cookies !== undefined) {
    const normalize = cookie => {
      // Inspect attribute names only; preserve the original cookie bytes and order.
      const attributes = new Set(cookie.split(';').slice(1)
        .map(attribute => attribute.split('=', 1)[0].trim().toLowerCase()));
      return cookie + (attributes.has('secure') ? '' : '; Secure')
        + (attributes.has('samesite') ? '' : '; SameSite=Lax');
    };
    result['set-cookie'] = Array.isArray(cookies) ? cookies.map(normalize) : normalize(cookies);
  }
  return result;
}

function responseHeaders(headers, config) {
  const result = { ...secureCookies(cleanHeaders(headers)), 'x-frame-options': 'DENY',
    'referrer-policy': 'no-referrer', 'cache-control': 'no-store' };
  if (config?.name === 'stirling-pdf') {
    const existing = result['content-security-policy'];
    result['content-security-policy'] = existing
      ? [...(Array.isArray(existing) ? existing : [existing]), STIRLING_CSP] : STIRLING_CSP;
  }
  return result;
}

function deny(res, status, message) {
  if (res.headersSent) return res.destroy();
  res.writeHead(status, { ...responseHeaders({ 'content-type': 'text/plain; charset=utf-8' }), connection: 'close' });
  res.end(message);
}

export function createProxy(config, { requestUpstream = http.request,
  authorizeTailnet = createTailnetAuthorizer() } = {}) {
  const sockets = new Set();
  const authorize = async (req, websocket = false) => {
    if (!allowed(req, config, websocket)) return false;
    if (config.name !== 'stirling-pdf') return true;
    try { return await authorizeTailnet(req, config); } catch { return false; }
  };
  const server = http.createServer({ maxHeaderSize: 16384 }, async (req, res) => {
    const accepted = await authorize(req);
    if (req.destroyed || res.destroyed) return;
    if (!accepted) return deny(res, 403, 'Verified private access through Tailscale HTTPS is required.');
    const length = req.headers['content-length'];
    if (length !== undefined && (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)))) {
      return deny(res, 400, 'Invalid request size.');
    }
    if (length !== undefined && Number(length) > config.maxBodyBytes) {
      return deny(res, 413, 'Request exceeds the configured upload limit.');
    }
    let received = 0;
    let failed = false;
    let upstream;
    const limiter = new Transform({ transform(chunk, _encoding, callback) {
      received += chunk.length;
      if (received > config.maxBodyBytes) {
        callback(Object.assign(new Error('Upload limit exceeded'), { code: 'BODY_LIMIT' }));
      } else callback(null, chunk);
    } });
    const fail = (status, message) => {
      if (failed) return;
      failed = true;
      req.unpipe(limiter);
      limiter.destroy();
      upstream?.destroy();
      deny(res, status, message);
      req.resume();
    };
    const timer = setTimeout(() => fail(504, 'Local application request timed out.'), config.requestTimeoutMs);
    timer.unref();
    upstream = requestUpstream({ hostname: config.upstreamHost, port: config.upstreamPort,
      method: req.method, path: req.url, headers: upstreamHeaders(req, config) }, response => {
      if (failed) { response.destroy(); return; }
      res.writeHead(response.statusCode, responseHeaders(response.headers, config));
      response.pipe(res);
      response.on('error', () => res.destroy());
      response.on('aborted', () => res.destroy());
    });
    limiter.on('error', error => fail(error.code === 'BODY_LIMIT' ? 413 : 502,
      error.code === 'BODY_LIMIT' ? 'Request exceeds the configured upload limit.' : 'Local application is unavailable.'));
    upstream.on('error', () => fail(502, 'Local application is starting or unavailable.'));
    req.on('aborted', () => { upstream.destroy(); limiter.destroy(); });
    req.on('error', () => { upstream.destroy(); limiter.destroy(); });
    res.on('close', () => { clearTimeout(timer); upstream.destroy(); limiter.destroy(); });
    req.pipe(limiter).pipe(upstream);
  });
  server.headersTimeout = Math.min(30000, config.requestTimeoutMs);
  server.requestTimeout = config.requestTimeoutMs;
  server.keepAliveTimeout = 5000;
  server.maxHeadersCount = 64;
  server.on('connection', socket => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  server.on('upgrade', async (req, socket, head) => {
    const accepted = await authorize(req, true);
    if (socket.destroyed) return;
    if (!accepted || req.headers.upgrade?.toLowerCase() !== 'websocket'
        || head.length > config.maxBodyBytes) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      return;
    }
    let remote;
    const upstream = requestUpstream({ hostname: config.upstreamHost, port: config.upstreamPort,
      method: req.method, path: req.url, headers: upstreamHeaders(req, config, true) });
    const timer = setTimeout(() => { upstream.destroy(); remote?.destroy(); socket.destroy(); }, config.requestTimeoutMs);
    timer.unref();
    upstream.on('upgrade', (response, peer, upstreamHead) => {
      remote = peer;
      const headers = { ...secureCookies(cleanHeaders(response.headers)), connection: 'Upgrade', upgrade: 'websocket' };
      const lines = Object.entries(headers).flatMap(([key, value]) =>
        (Array.isArray(value) ? value : [value]).map(item => `${key}: ${item}`));
      socket.write(`HTTP/1.1 101 Switching Protocols\r\n${lines.join('\r\n')}\r\n\r\n`);
      if (upstreamHead.length) socket.write(upstreamHead);
      if (head.length) peer.write(head);
      socket.pipe(peer).pipe(socket);
      peer.on('error', () => socket.destroy());
      peer.on('close', () => socket.destroy());
    });
    upstream.on('response', response => {
      response.resume();
      socket.end('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
    });
    upstream.on('error', () => socket.destroy());
    socket.on('error', () => { upstream.destroy(); remote?.destroy(); });
    socket.on('close', () => { clearTimeout(timer); upstream.destroy(); remote?.destroy(); });
    upstream.end();
  });
  server.on('clientError', (_error, socket) => {
    if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
  });
  server.shutdown = () => {
    for (const socket of sockets) socket.destroy();
    server.closeAllConnections();
    return new Promise(resolve => server.close(resolve));
  };
  return server;
}

export async function start(configs) {
  const servers = [];
  try {
    for (const config of configs) {
      const server = createProxy(config);
      servers.push(server);
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(config.listenPort, '127.0.0.1', resolve);
      });
    }
    return servers;
  } catch (error) {
    await Promise.all(servers.map(server => server.shutdown()));
    throw error;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const servers = await start(readPrivateConfig());
    console.log('Private application identity bridges ready on loopback.');
    const stop = async () => { await Promise.all(servers.map(server => server.shutdown())); process.exit(0); };
    process.once('SIGTERM', stop);
    process.once('SIGINT', stop);
  } catch {
    console.error('Private application proxy failed to load its configuration or bind its approved loopback ports.');
    process.exitCode = 1;
  }
}
