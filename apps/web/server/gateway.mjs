/**
 * Production entry point of the web image: one origin for the UI and the admin API.
 *
 *   node server/gateway.mjs          (the image runs it from /app/apps/web)
 *
 * The Next.js standalone server (server.js) listens on 127.0.0.1:<GPUDECK_WEB_INTERNAL_PORT>;
 * this gateway listens on PORT (3180) and
 *   - forwards /api/* to the admin API at GPUDECK_API_INTERNAL_URL (runtime, e.g.
 *     http://admin-api:8180), streaming both ways: cookies, the CSRF header, file uploads and
 *     downloads, server-sent events (/api/v1/live/events, /api/v1/logs/stream) and WebSocket
 *     upgrades (/api/v1/live/ws) pass through untouched;
 *   - forwards everything else to Next.
 *
 * The browser bundle has no API URL compiled in (it calls the relative /api/v1), so one image
 * works on any hostname, behind any TLS proxy, and in the desktop app's remote shell. The
 * original Host is kept and X-Forwarded-For / -Proto / -Host are added, as a reverse proxy
 * would (add the web container to TRUSTED_PROXIES to have client IPs believed).
 */
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import tls from 'node:tls';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

/** Pick the upstream for a request path: the admin API for /api/*, else the UI. */
export function routeFor(pathname, prefixes = ['/api/']) {
  const p = pathname || '/';
  return prefixes.some((pre) => p === pre.replace(/\/$/, '') || p.startsWith(pre)) ? 'api' : 'app';
}

/** Request headers for the upstream: hop-by-hop removed, X-Forwarded-* appended. */
export function forwardHeaders(req, { upgrade = false } = {}) {
  /** @type {Record<string, string | string[]>} */
  const out = {};
  const connectionTokens = String(req.headers.connection || '')
    .split(',')
    .map((t) => t.trim().toLowerCase())
    .filter(Boolean);
  for (const [name, value] of Object.entries(req.headers)) {
    const key = name.toLowerCase();
    if (value === undefined) continue;
    if (!upgrade && (HOP_BY_HOP.has(key) || connectionTokens.includes(key))) continue;
    out[key] = value;
  }
  const peer = req.socket?.remoteAddress || '';
  const prior = req.headers['x-forwarded-for'];
  out['x-forwarded-for'] = prior ? `${prior}, ${peer}` : peer;
  if (!req.headers['x-forwarded-proto']) out['x-forwarded-proto'] = req.socket?.encrypted ? 'https' : 'http';
  if (!req.headers['x-forwarded-host'] && req.headers.host) out['x-forwarded-host'] = req.headers.host;
  if (upgrade) {
    out.connection = 'Upgrade';
    out.upgrade = req.headers.upgrade;
  }
  return out;
}

function jsonError(res, status, code, message) {
  if (res.headersSent) {
    res.destroy();
    return;
  }
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  res.end(JSON.stringify({ success: false, data: null, error: { code, message }, meta: null }));
}

function proxyHttp(req, res, target, log) {
  const lib = target.protocol === 'https:' ? https : http;
  const upstream = lib.request(
    {
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || (target.protocol === 'https:' ? 443 : 80),
      method: req.method,
      path: req.url,
      headers: forwardHeaders(req),
    },
    (up) => {
      const headers = {};
      for (const [name, value] of Object.entries(up.headers)) {
        if (!HOP_BY_HOP.has(name.toLowerCase()) && value !== undefined) headers[name] = value;
      }
      res.writeHead(up.statusCode || 502, up.statusMessage, headers);
      // Flush streamed responses (SSE) chunk by chunk.
      if (typeof res.flushHeaders === 'function') res.flushHeaders();
      up.pipe(res);
      up.on('error', () => res.destroy());
    },
  );
  upstream.on('error', (err) => {
    log?.(`gateway: ${req.method} ${req.url} -> ${target.origin}: ${err.message}`);
    jsonError(res, 502, 'UPSTREAM_UNAVAILABLE', 'The admin API is not reachable from the web container');
  });
  res.on('close', () => upstream.destroy());
  req.pipe(upstream);
}

function proxyUpgrade(req, socket, head, target, log) {
  const port = Number(target.port || (target.protocol === 'https:' ? 443 : 80));
  const onConnect = () => {
    const headers = forwardHeaders(req, { upgrade: true });
    const lines = [`${req.method} ${req.url} HTTP/1.1`];
    for (const [name, value] of Object.entries(headers)) {
      for (const v of Array.isArray(value) ? value : [value]) lines.push(`${name}: ${v}`);
    }
    upstream.write(`${lines.join('\r\n')}\r\n\r\n`);
    if (head && head.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  };
  const upstream =
    target.protocol === 'https:'
      ? tls.connect({ host: target.hostname, port, servername: target.hostname }, onConnect)
      : net.connect({ host: target.hostname, port }, onConnect);
  const close = () => {
    upstream.destroy();
    socket.destroy();
  };
  upstream.on('error', (err) => {
    log?.(`gateway: upgrade ${req.url} -> ${target.origin}: ${err.message}`);
    if (socket.writable) socket.end('HTTP/1.1 502 Bad Gateway\r\nconnection: close\r\n\r\n');
    close();
  });
  socket.on('error', close);
  upstream.on('close', () => socket.destroy());
  socket.on('close', () => upstream.destroy());
}

/**
 * Build the gateway server.
 * @param {{ apiUrl?: string | null, appUrl: string, prefixes?: string[], log?: (msg: string) => void }} opts
 */
export function createGateway({ apiUrl, appUrl, prefixes = ['/api/'], log = console.error }) {
  const api = apiUrl ? new URL(apiUrl) : null;
  const app = new URL(appUrl);
  const pick = (req) => {
    const pathname = (req.url || '/').split('?')[0];
    return routeFor(pathname, prefixes) === 'api' ? api : app;
  };
  const server = http.createServer((req, res) => {
    const target = pick(req);
    if (!target) {
      jsonError(res, 503, 'API_NOT_CONFIGURED', 'GPUDECK_API_INTERNAL_URL is not set on the web container');
      return;
    }
    proxyHttp(req, res, target, log);
  });
  server.on('upgrade', (req, socket, head) => {
    const target = pick(req);
    if (!target) {
      socket.end('HTTP/1.1 503 Service Unavailable\r\nconnection: close\r\n\r\n');
      return;
    }
    proxyUpgrade(req, socket, head, target, log);
  });
  // Long-lived streams (SSE, WebSocket) must not be cut by the default timeouts.
  server.requestTimeout = 0;
  server.headersTimeout = 60_000;
  server.keepAliveTimeout = 65_000;
  return server;
}

async function waitForPort(host, port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ok = await new Promise((resolve) => {
      const s = net.connect({ host, port }, () => {
        s.destroy();
        resolve(true);
      });
      s.on('error', () => resolve(false));
    });
    if (ok) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`Next.js did not start on ${host}:${port}`);
}

async function main() {
  const port = Number(process.env.PORT || 3180);
  const host = process.env.HOSTNAME || '0.0.0.0';
  const internalPort = Number(process.env.GPUDECK_WEB_INTERNAL_PORT || 3189);
  const apiUrl = (process.env.GPUDECK_API_INTERNAL_URL || '').trim() || null;
  const here = path.dirname(fileURLToPath(import.meta.url));
  // The standalone server reads PORT / HOSTNAME when it loads.
  process.env.PORT = String(internalPort);
  process.env.HOSTNAME = '127.0.0.1';
  await import(path.join(here, '..', 'server.js'));
  await waitForPort('127.0.0.1', internalPort, 60_000);
  if (!apiUrl) console.warn('gateway: GPUDECK_API_INTERNAL_URL is not set; /api requests answer 503');
  const server = createGateway({ apiUrl, appUrl: `http://127.0.0.1:${internalPort}` });
  server.listen(port, host, () => {
    console.log(`gateway: http://${host}:${port} (UI -> 127.0.0.1:${internalPort}, /api -> ${apiUrl ?? 'unset'})`);
  });
  const stop = () => server.close(() => process.exit(0));
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
