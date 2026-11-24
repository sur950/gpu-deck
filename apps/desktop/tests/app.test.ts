import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectApp } from '../src/app';
import type { Backend } from '../src/lib/ipc';
import type {
  Prefill,
  ProbeResult,
  Server,
  ServersView,
} from '../src/lib/model';

const flush = () => new Promise((r) => setTimeout(r, 0));

const healthy: ProbeResult = {
  origin: 'http://localhost:3180',
  apiBase: 'http://localhost:8180',
  insecure: true,
  isGpuDeck: true,
  branding: {
    appName: 'GPU Deck',
    appByline: 'GPU control plane',
    brandColor: '#B3122E',
  },
  health: {
    status: 'healthy',
    version: '1.4.2',
    checks: [
      { name: 'database', status: 'healthy' },
      { name: 'redis', status: 'healthy' },
    ],
  },
  error: null,
  latencyMs: 8,
};

function fakeBackend(initial: Server[] = []) {
  let servers = [...initial];
  let prefillCb: ((p: Prefill) => void) | null = null;
  const backend = {
    list: vi.fn(
      async (): Promise<ServersView> => ({
        servers,
        lastServerId: servers[0]?.id ?? null,
        currentServerId: null,
        appVersion: '0.1.0',
        platform: 'macos',
      }),
    ),
    save: vi.fn(async (input) => {
      const s: Server = {
        id: input.id ?? `id-${servers.length + 1}`,
        name: input.name.trim(),
        url: new URL(
          input.url.includes('://') ? input.url : `https://${input.url}`,
        ).origin,
        apiUrl: input.apiUrl,
        createdAt: 1,
        insecure: input.url.startsWith('http://'),
      };
      servers = [...servers.filter((x) => x.id !== s.id), s];
      return s;
    }),
    remove: vi.fn(async (id: string) => {
      servers = servers.filter((s) => s.id !== id);
    }),
    probe: vi.fn(async () => healthy),
    connect: vi.fn(async () => {}),
    prefill: vi.fn(async () => null),
    back: vi.fn(async () => false),
    onPrefill: vi.fn((cb: (p: Prefill) => void) => {
      prefillCb = cb;
    }),
    onServersChanged: vi.fn(),
  } satisfies Backend;
  return { backend, emitPrefill: (p: Prefill) => prefillCb?.(p) };
}

function input(id: string): HTMLInputElement {
  const el = document.getElementById(id);
  if (!(el instanceof HTMLInputElement)) throw new Error(`no input ${id}`);
  return el;
}

function type(id: string, value: string) {
  const el = input(id);
  el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

function button(label: string | RegExp): HTMLButtonElement {
  const found = [...document.querySelectorAll('button')].find((b) =>
    typeof label === 'string'
      ? b.textContent?.trim().startsWith(label)
      : label.test(b.textContent ?? ''),
  );
  if (!found) throw new Error(`no button ${label}`);
  return found;
}

let root: HTMLElement;
beforeEach(() => {
  document.body.innerHTML = '<div id="app"></div>';
  root = document.getElementById('app')!;
});

describe('ConnectApp', () => {
  it('shows the first-run form, validates, warns on http, saves and connects', async () => {
    const { backend } = fakeBackend();
    const app = new ConnectApp(root, backend);
    await app.start();
    expect(root.textContent).toContain('Connect to your GPU fleet.');
    expect(root.textContent).toContain('GPU Deck');
    expect(root.textContent).toContain('GPU control plane');

    button('Add and connect').click();
    await flush();
    expect(root.textContent).toContain('Enter the server URL.');
    expect(backend.save).not.toHaveBeenCalled();

    type('server-url', 'http://gpudeck.example.com');
    expect(root.textContent).toContain('only allowed for localhost');

    type('server-url', 'http://localhost:3180');
    expect(root.textContent).toContain('plain http address');
    type('server-name', 'Local dev');
    type('server-api', 'http://localhost:8180');

    button('Check server').click();
    await flush();
    expect(backend.probe).toHaveBeenCalledWith(
      'http://localhost:3180',
      'http://localhost:8180',
    );
    expect(root.querySelector('[data-testid="probe"]')?.textContent).toContain(
      '1.4.2',
    );
    expect(root.textContent).toContain('Database');

    button('Add and connect').click();
    await flush();
    await flush();
    expect(backend.save).toHaveBeenCalledWith({
      id: undefined,
      name: 'Local dev',
      url: 'http://localhost:3180',
      apiUrl: 'http://localhost:8180',
    });
    expect(backend.connect).toHaveBeenCalledWith('id-1', undefined);
    expect(root.querySelector('.server')?.textContent).toContain('Local dev');
  });

  it('lists saved servers, shows health, edits and removes with confirmation', async () => {
    const { backend } = fakeBackend([
      {
        id: 's1',
        name: 'Prod',
        url: 'https://gpudeck.example.com',
        createdAt: 1,
        lastUsedAt: 2,
        insecure: false,
      },
      {
        id: 's2',
        name: 'Lab',
        url: 'http://192.168.1.5:3180',
        createdAt: 1,
        insecure: true,
      },
    ]);
    const app = new ConnectApp(root, backend, () => 3);
    await app.start();
    await flush();
    const rows = [...root.querySelectorAll('.server')];
    expect(
      rows.map((r) => r.querySelector('.server-name')?.textContent),
    ).toEqual(['Prod', 'Lab']);
    expect(backend.probe).toHaveBeenCalledTimes(2);
    expect(root.querySelector('h1')?.textContent).toBe('Prod');
    expect(root.querySelector('.led[data-tone="ok"]')).not.toBeNull();

    (rows[1] as HTMLButtonElement).click();
    expect(root.querySelector('h1')?.textContent).toBe('Lab');
    expect(root.textContent).toContain('http · unencrypted');

    button('Edit').click();
    expect(input('server-name').value).toBe('Lab');
    type('server-name', 'Lab box');
    button(/^Save$/).click();
    await flush();
    await flush();
    expect(backend.save).toHaveBeenCalledWith(
      expect.objectContaining({ id: 's2', name: 'Lab box' }),
    );

    button('Remove').click();
    expect(backend.remove).not.toHaveBeenCalled();
    button('Remove Lab box').click();
    await flush();
    expect(backend.remove).toHaveBeenCalledWith('s2');
  });

  it('opens prefilled for deep links to unknown servers and never auto-connects', async () => {
    const { backend, emitPrefill } = fakeBackend([
      {
        id: 's1',
        name: 'Prod',
        url: 'https://gpudeck.example.com',
        createdAt: 1,
        insecure: false,
      },
    ]);
    const app = new ConnectApp(root, backend);
    await app.start();
    emitPrefill({
      url: 'https://new.example.com',
      path: '/deployments/9',
      reason: 'A link asked to open a server that is not saved.',
    });
    expect(input('server-url').value).toBe('https://new.example.com');
    expect(root.textContent).toContain('not saved');
    expect(backend.connect).not.toHaveBeenCalled();
    button('Add and connect').click();
    type('server-name', 'New');
    button('Add and connect').click();
    await flush();
    await flush();
    // The pending deep-link path is passed along on the explicit connect.
    expect(backend.connect).toHaveBeenCalledWith(
      expect.any(String),
      '/deployments/9',
    );
  });

  it('renders server-provided strings as text, never HTML', async () => {
    const { backend } = fakeBackend([
      {
        id: 's1',
        name: '<img src=x onerror=alert(1)>',
        url: 'https://a.example.com',
        createdAt: 1,
        insecure: false,
      },
    ]);
    backend.probe.mockResolvedValue({
      ...healthy,
      branding: {
        appName: '<script>x</script>',
        appByline: '',
        brandColor: '#B3122E',
      },
    });
    const app = new ConnectApp(root, backend);
    await app.start();
    await flush();
    expect(root.querySelector('img[src="x"]')).toBeNull();
    expect(root.querySelector('script')).toBeNull();
    expect(root.textContent).toContain('<script>x</script>');
  });

  it('explains unreachable servers and suggests the dev admin API', async () => {
    const { backend } = fakeBackend([
      {
        id: 's1',
        name: 'Dev',
        url: 'http://localhost:3180',
        createdAt: 1,
        insecure: true,
      },
    ]);
    backend.probe.mockResolvedValue({
      ...healthy,
      isGpuDeck: false,
      branding: null,
      health: null,
      apiBase: 'http://localhost:3180',
      error: 'http://localhost:3180/api/v1/branding answered HTTP 404',
    });
    const app = new ConnectApp(root, backend);
    await app.start();
    await flush();
    expect(root.textContent).toContain('Not reachable');
    expect(root.textContent).toContain('answered HTTP 404');
    expect(root.textContent).toContain('http://localhost:8180');
  });
});
