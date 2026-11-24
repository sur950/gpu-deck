/**
 * CSRF double-submit token.
 *
 * The backend sets a readable (non-HttpOnly) cookie next to the HttpOnly
 * session cookie. Every state-changing request echoes its value in the
 * X-CSRF-Token header; the backend rejects the request when they differ.
 */

export const CSRF_COOKIE = process.env.NEXT_PUBLIC_CSRF_COOKIE || 'gpudeck_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';

export function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const prefix = `${name}=`;
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

/** The current CSRF token, or null before the first authenticated response. */
export function getCsrfToken(): string | null {
  return readCookie(CSRF_COOKIE);
}
