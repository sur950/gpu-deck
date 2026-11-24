import { describe, expect, it } from 'vitest';
import {
  checkLabel,
  checkTone,
  relativeTime,
  sortServers,
  suggestApiUrl,
  toneOf,
  validateForm,
  type ProbeResult,
  type Server,
} from '../src/lib/model';

const server = (over: Partial<Server>): Server => ({
  id: 'a',
  name: 'Alpha',
  url: 'https://a.example.com',
  createdAt: 1,
  insecure: false,
  ...over,
});

const probe = (over: Partial<ProbeResult>): ProbeResult => ({
  origin: 'https://a.example.com',
  apiBase: 'https://a.example.com',
  insecure: false,
  isGpuDeck: true,
  branding: {
    appName: 'GPU Deck',
    appByline: 'GPU control plane',
    brandColor: '#B3122E',
  },
  health: { status: 'healthy', version: '1.0.0', checks: [] },
  error: null,
  latencyMs: 12,
  ...over,
});

describe('model helpers', () => {
  it('sorts by last use, then name', () => {
    const list = sortServers([
      server({ id: '1', name: 'beta' }),
      server({ id: '2', name: 'Alpha' }),
      server({ id: '3', name: 'zulu', lastUsedAt: 5 }),
    ]);
    expect(list.map((s) => s.id)).toEqual(['3', '2', '1']);
  });

  it('formats relative times', () => {
    const now = 1_000_000_000;
    expect(relativeTime(undefined, now)).toBe('Never used');
    expect(relativeTime(now - 10_000, now)).toBe('Used just now');
    expect(relativeTime(now - 5 * 60_000, now)).toBe('Used 5 min ago');
    expect(relativeTime(now - 3 * 3_600_000, now)).toBe('Used 3 h ago');
    expect(relativeTime(now - 2 * 86_400_000, now)).toBe('Used 2 days ago');
  });

  it('maps probes and checks to tones', () => {
    expect(toneOf(undefined)).toBe('unknown');
    expect(toneOf(probe({}))).toBe('ok');
    expect(
      toneOf(
        probe({ health: { status: 'degraded', version: null, checks: [] } }),
      ),
    ).toBe('warn');
    expect(
      toneOf(
        probe({ health: { status: 'unhealthy', version: null, checks: [] } }),
      ),
    ).toBe('down');
    expect(toneOf(probe({ isGpuDeck: false }))).toBe('down');
    expect(checkTone('healthy')).toBe('ok');
    expect(checkTone('unhealthy')).toBe('down');
    expect(checkTone('degraded')).toBe('warn');
    expect(checkLabel('agent_hub')).toBe('Agent hub');
    expect(checkLabel('object_store')).toBe('Object store');
  });

  it('validates the form against saved servers', () => {
    const existing = [
      server({ id: 'a', name: 'Alpha', url: 'https://a.example.com' }),
    ];
    expect(validateForm({ name: '', url: '' }, existing).errors).toEqual({
      name: 'Give the server a name.',
      url: 'Enter the server URL.',
    });
    expect(
      validateForm({ name: 'alpha', url: 'a.example.com' }, existing).errors,
    ).toEqual({
      name: 'A server named "Alpha" already exists.',
      url: 'This URL is already saved as "Alpha".',
    });
    // editing itself is fine
    expect(
      validateForm(
        { id: 'a', name: 'Alpha', url: 'https://a.example.com' },
        existing,
      ).errors,
    ).toEqual({});
    const local = validateForm(
      { name: 'Lab', url: 'http://localhost:3180', apiUrl: 'ftp://x' },
      existing,
    );
    expect(local.insecure).toBe(true);
    expect(local.errors.apiUrl).toMatch(/not ftp/);
    expect(
      validateForm(
        { name: 'x'.repeat(65), url: 'https://b.example.com' },
        existing,
      ).errors.name,
    ).toMatch(/64/);
  });

  it('suggests the dev admin API only for the local web dev server', () => {
    expect(suggestApiUrl('http://localhost:3180')).toBe(
      'http://localhost:8180',
    );
    expect(suggestApiUrl('http://127.0.0.1:3180')).toBe(
      'http://127.0.0.1:8180',
    );
    expect(suggestApiUrl('https://gpudeck.example.com')).toBeNull();
    expect(suggestApiUrl(undefined)).toBeNull();
  });
});
