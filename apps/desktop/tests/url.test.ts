import { describe, expect, it } from 'vitest';
import { isLocalOrPrivateHost, normalizeServerUrl } from '../src/lib/url';

describe('normalizeServerUrl', () => {
  it('defaults to https and returns the origin', () => {
    expect(normalizeServerUrl('GPUDeck.example.com')).toEqual({
      ok: true,
      origin: 'https://gpudeck.example.com',
      insecure: false,
      host: 'gpudeck.example.com',
    });
    expect(
      normalizeServerUrl('https://gpudeck.example.com:443/'),
    ).toMatchObject({ origin: 'https://gpudeck.example.com' });
    expect(
      normalizeServerUrl(' https://gpudeck.example.com:8443 '),
    ).toMatchObject({ origin: 'https://gpudeck.example.com:8443' });
  });

  it.each([
    'http://localhost:3180',
    'http://app.localhost',
    'http://127.0.0.1:3180',
    'http://10.0.0.5',
    'http://172.20.1.1',
    'http://192.168.1.20:3180',
    'http://169.254.1.1',
    'http://100.64.0.1',
    'http://[::1]:3180',
    'http://[fd00::1]',
    'http://[fe80::1]',
    'http://gpu-box.local',
  ])('allows http for local/private %s with a warning flag', (url) => {
    const r = normalizeServerUrl(url);
    expect(r.ok).toBe(true);
    expect(r.ok && r.insecure).toBe(true);
  });

  it.each([
    'http://gpudeck.example.com',
    'http://8.8.8.8',
    'http://172.32.0.1',
    'http://[2001:db8::1]',
    'http://localhost.evil.com',
  ])('rejects http for public %s', (url) => {
    const r = normalizeServerUrl(url);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.error).toMatch(/only allowed for localhost/);
  });

  it.each([
    ['', /Enter the server URL/],
    ['ftp://x.example.com', /not ftp/],
    ['javascript://x', /not javascript/],
    ['https://admin:pw@x.example.com', /username or password/],
    ['https://x.example.com/dashboard', /base address only/],
    ['https://x.example.com/?a=1', /base address only/],
    ['https://x.example.com/#frag', /base address only/],
    ['https://exa mple.com', /spaces/],
  ])('rejects %s', (url, msg) => {
    const r = normalizeServerUrl(url);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.error).toMatch(msg);
  });

  it('classifies hosts', () => {
    expect(isLocalOrPrivateHost('localhost')).toBe(true);
    expect(isLocalOrPrivateHost('[::ffff:192.168.0.1]')).toBe(true);
    expect(isLocalOrPrivateHost('example.com')).toBe(false);
  });
});
