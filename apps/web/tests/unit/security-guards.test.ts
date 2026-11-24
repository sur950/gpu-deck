/**
 * Security guards: post-login redirect, the route guard's exemptions, and
 * mock mode staying off in production builds.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { safeRedirect } from '@/app/(auth)/_components/LoginForm';
import { isMockMode } from '@/lib/mocks';
import { isGuardExempt } from '@/proxy';

describe('safeRedirect (login ?from=)', () => {
  it('keeps same-origin paths with their query', () => {
    expect(safeRedirect('/nodes')).toBe('/nodes');
    expect(safeRedirect('/deployments/abc?tab=logs')).toBe('/deployments/abc?tab=logs');
  });

  it.each([
    null,
    '',
    'https://evil.example',
    '//evil.example',
    '/\\evil.example',
    '/\t/evil.example',
    '/\n/evil.example',
    'javascript:alert(1)',
    '/login',
    '/login?from=/nodes',
  ])('falls back to the dashboard for %j', (from) => {
    expect(safeRedirect(from)).toBe('/dashboard');
  });
});

describe('isGuardExempt (proxy.ts)', () => {
  it('exempts Next internals, API routes and static assets', () => {
    expect(isGuardExempt('/_next/static/chunks/a.js')).toBe(true);
    expect(isGuardExempt('/api/health')).toBe(true);
    expect(isGuardExempt('/brand/gpudeck-mark.svg')).toBe(true);
    expect(isGuardExempt('/favicon.svg')).toBe(true);
  });

  it('guards pages that merely look like exempt paths', () => {
    expect(isGuardExempt('/api-keys')).toBe(false);
    expect(isGuardExempt('/api-keys/new')).toBe(false);
    expect(isGuardExempt('/catalog/qwen3-0.6b')).toBe(false);
    expect(isGuardExempt('/dashboard')).toBe(false);
  });
});

describe('isMockMode', () => {
  afterEach(() => vi.unstubAllEnvs());

  it('is on only when requested outside production', () => {
    vi.stubEnv('NEXT_PUBLIC_API_MOCKS', '1');
    vi.stubEnv('NODE_ENV', 'development');
    expect(isMockMode()).toBe(true);
  });

  it('can never be enabled in a production build', () => {
    vi.stubEnv('NEXT_PUBLIC_API_MOCKS', '1');
    vi.stubEnv('NODE_ENV', 'production');
    expect(isMockMode()).toBe(false);
  });
});
