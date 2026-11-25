/**
 * server/gateway.mjs: the web image's single origin. /api/* goes to the admin API (cookies,
 * CSRF header, SSE streaming and WebSocket upgrades intact), everything else to Next.
 */
import http from 'node:http';
import net from 'node:net';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it } from 'vitest';
import { createGateway, forwardHeaders, routeFor } from '@/server/gateway.mjs';

const servers: (http.Server | net.Server)[] = [];
const sockets = new Set<net.Socket>();

function listen<T extends http.Server | net.Server>(server: T): Promise<string> {
  servers.push(server);
  server.on('connection', (s: net.Socket) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${(server.address() as AddressInfo).port}`));
  });
}

afterEach(async () => {
  for (const s of sockets) s.destroy();
  sockets.clear();
  appPaths.length = 0;
  await Promise.all(servers.splice(0).map((s) => new Promise((r) => s.close(() => r(null)))));
});

function fakeApi() {
  const seen: http.IncomingHttpHeaders[] = [];
  const server = http.createServer((req, res) => {
    seen.push(req.headers);
    if (req.url?.startsWith('/api/v1/live/events')) {
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      res.write('data: one\n\n');
      setTimeout(() => res.end('data: two\n\n'), 50);
      return;
    }
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      res.writeHead(200, { 'content-type': 'application/json', 'set-cookie': 'gpudeck_session=abc; HttpOnly; Path=/' });
      res.end(JSON.stringify({ path: req.url, method: req.method, body: Buffer.concat(chunks).toString() }));
    });
  });
  server.on('upgrade', (req, socket) => {
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n');
    socket.on('data', (d) => socket.write(`echo:${d.toString()}`));
  });
  return { server, seen };
}

// The fake Next app records the path it was asked for instead of echoing it into HTML.
const appPaths: string[] = [];

function fakeApp() {
  return http.createServer((req, res) => {
    appPaths.push(req.url ?? '');
    res.writeHead(200, { 'content-type': 'text/html' });
    res.end('<html>next</html>');
  });
}

async function stack() {
  const api = fakeApi();
  const apiUrl = await listen(api.server);
  const appUrl = await listen(fakeApp());
  const gw = createGateway({ apiUrl, appUrl, log: () => {} });
  const gwUrl = await listen(gw);
  return { api, gwUrl };
}

describe('gateway routing', () => {
  it('sends only /api to the admin API', () => {
    expect(routeFor('/api/v1/auth/login')).toBe('api');
    expect(routeFor('/api')).toBe('api');
    expect(routeFor('/api-keys')).toBe('app');
    expect(routeFor('/dashboard')).toBe('app');
  });

  it('adds forwarding headers and drops hop-by-hop ones', () => {
    const req = {
      headers: { host: 'gpu-deck.localtest.me:3180', connection: 'keep-alive, x-drop', 'x-drop': '1', cookie: 'a=b' },
      socket: { remoteAddress: '10.0.0.7' },
    } as unknown as http.IncomingMessage;
    const h = forwardHeaders(req);
    expect(h['x-forwarded-for']).toBe('10.0.0.7');
    expect(h['x-forwarded-host']).toBe('gpu-deck.localtest.me:3180');
    expect(h['x-forwarded-proto']).toBe('http');
    expect(h.cookie).toBe('a=b');
    expect(h.connection).toBeUndefined();
    expect(h['x-drop']).toBeUndefined();
  });
});

describe('gateway proxying', () => {
  it('proxies API calls with cookies, CSRF header, body and the original Host', async () => {
    const { api, gwUrl } = await stack();
    const res = await fetch(`${gwUrl}/api/v1/nodes/join-tokens`, {
      method: 'POST',
      headers: { cookie: 'gpudeck_session=s; gpudeck_csrf=c', 'x-csrf-token': 'c', 'content-type': 'application/json' },
      body: JSON.stringify({ max_uses: 3 }),
    });
    expect(res.status).toBe(200);
    expect(res.headers.get('set-cookie')).toContain('gpudeck_session=abc');
    expect(await res.json()).toEqual({ path: '/api/v1/nodes/join-tokens', method: 'POST', body: '{"max_uses":3}' });
    const seen = api.seen.at(-1)!;
    expect(seen.cookie).toContain('gpudeck_csrf=c');
    expect(seen['x-csrf-token']).toBe('c');
    expect(seen.host).toBe(new URL(gwUrl).host);
  });

  it('serves the UI from Next', async () => {
    const { gwUrl } = await stack();
    const res = await fetch(`${gwUrl}/login`);
    expect(await res.text()).toBe('<html>next</html>');
    expect(appPaths).toEqual(['/login']);
  });

  it('streams server-sent events', async () => {
    const { gwUrl } = await stack();
    const res = await fetch(`${gwUrl}/api/v1/live/events?topics=fleet`);
    expect(res.headers.get('content-type')).toBe('text/event-stream');
    expect(await res.text()).toBe('data: one\n\ndata: two\n\n');
  });

  it('passes WebSocket upgrades through', async () => {
    const { gwUrl } = await stack();
    const { port } = new URL(gwUrl);
    const reply = await new Promise<string>((resolve, reject) => {
      const sock = net.connect(Number(port), '127.0.0.1', () => {
        sock.write('GET /api/v1/live/ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n');
      });
      let buf = '';
      sock.on('data', (d) => {
        buf += d.toString();
        if (buf.includes('\r\n\r\n') && !buf.includes('echo:')) sock.write('ping');
        if (buf.includes('echo:ping')) {
          sock.destroy();
          resolve(buf);
        }
      });
      sock.on('error', reject);
    });
    expect(reply).toContain('101 Switching Protocols');
    expect(reply).toContain('echo:ping');
  });

  it('answers 502 in the API envelope when the admin API is down', async () => {
    const appUrl = await listen(fakeApp());
    const gwUrl = await listen(createGateway({ apiUrl: 'http://127.0.0.1:1', appUrl, log: () => {} }));
    const res = await fetch(`${gwUrl}/api/v1/health`);
    expect(res.status).toBe(502);
    expect((await res.json()).error.code).toBe('UPSTREAM_UNAVAILABLE');
  });
});
