/**
 * Mock session cookies.
 *
 * The real backend sets an HttpOnly session cookie plus a readable CSRF
 * cookie. In mock mode the login handler writes both as plain cookies so the
 * route guard (`proxy.ts`, presence-only) and the CSRF header logic behave
 * exactly as they do against the backend.
 */

import { CSRF_COOKIE, readCookie } from '@/lib/api/csrf';

export const SESSION_COOKIE = process.env.NEXT_PUBLIC_SESSION_COOKIE || 'gpudeck_session';

const MOCK_SESSION_VALUE = 'mock-session';

export function hasMockSession(): boolean {
  return readCookie(SESSION_COOKIE) !== null;
}

export function openMockSession(csrfToken: string): void {
  const maxAge = 60 * 60 * 12;
  document.cookie = `${SESSION_COOKIE}=${MOCK_SESSION_VALUE}; path=/; max-age=${maxAge}; SameSite=Lax`;
  document.cookie = `${CSRF_COOKIE}=${csrfToken}; path=/; max-age=${maxAge}; SameSite=Lax`;
}

export function closeMockSession(): void {
  document.cookie = `${SESSION_COOKIE}=; path=/; max-age=0`;
  document.cookie = `${CSRF_COOKIE}=; path=/; max-age=0`;
}
