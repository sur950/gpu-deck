/**
 * Auth mocks: login / logout / me / csrf / change-password / sessions.
 * Credentials: admin / admin-admin-admin (any 12+ char password after a change).
 */

import { ADMIN_USER } from '@/lib/mocks/fixtures/fleet';
import { uuid } from '@/lib/mocks/fixtures/random';
import { bodyOf, fail, noContent, ok, paginate, registerMockHandlers } from '@/lib/mocks/registry';
import { closeMockSession, openMockSession } from '@/lib/mocks/session';
import type { Schemas } from '@/lib/mocks/types';

const CSRF_TOKEN = 'mock-csrf-token-5f1c';
let password = 'admin-admin-admin';
let failedAttempts = 0;
let lockedUntil = 0;
let passwordChangedAt = ADMIN_USER.password_changed_at;

const CURRENT_SESSION_ID = uuid('session:current');

const SESSIONS: Schemas['Session'][] = [
  {
    id: CURRENT_SESSION_ID,
    ip_address: '192.168.1.24',
    user_agent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.2 Safari/605.1.15',
    created_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    last_active_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 9 * 3_600_000).toISOString(),
    is_current: true,
  },
  {
    id: uuid('session:cli'),
    ip_address: '192.168.1.24',
    user_agent: 'gpudeck-cli/0.1.0',
    created_at: new Date(Date.now() - 26 * 3_600_000).toISOString(),
    last_active_at: new Date(Date.now() - 40 * 60_000).toISOString(),
    expires_at: new Date(Date.now() + 6 * 3_600_000).toISOString(),
    is_current: false,
  },
  {
    id: uuid('session:terraform'),
    ip_address: '10.8.0.12',
    user_agent: 'terraform-provider-gpudeck/0.1.0',
    created_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    last_active_at: new Date(Date.now() - 5 * 3_600_000).toISOString(),
    expires_at: new Date(Date.now() + 2 * 3_600_000).toISOString(),
    is_current: false,
  },
];

function me(): Schemas['AdminUser'] {
  return { ...ADMIN_USER, password_changed_at: passwordChangedAt };
}

registerMockHandlers('auth', [
  {
    method: 'POST',
    path: '/auth/login',
    handle: (req) => {
      const body = bodyOf<Schemas['LoginRequest']>(req);
      if (Date.now() < lockedUntil) {
        return fail(401, 'ACCOUNT_LOCKED', `Too many failed attempts. Try again in ${Math.ceil((lockedUntil - Date.now()) / 60_000)} min.`);
      }
      if (body.username !== 'admin' || body.password !== password) {
        failedAttempts += 1;
        if (failedAttempts >= 5) {
          lockedUntil = Date.now() + 15 * 60_000;
          failedAttempts = 0;
          return fail(401, 'ACCOUNT_LOCKED', 'Too many failed attempts. The account is locked for 15 minutes.');
        }
        return fail(401, 'INVALID_CREDENTIALS', 'Invalid username or password');
      }
      failedAttempts = 0;
      openMockSession(CSRF_TOKEN);
      const expiresIn = 12 * 3600;
      const result: Schemas['LoginResult'] = {
        session_id: CURRENT_SESSION_ID,
        expires_at: new Date(Date.now() + expiresIn * 1000).toISOString(),
        expires_in: expiresIn,
        csrf_token: CSRF_TOKEN,
        user: { ...me(), last_login_at: new Date().toISOString() },
        must_change_password: false,
      };
      return ok(result);
    },
  },
  {
    method: 'POST',
    path: '/auth/logout',
    handle: () => {
      closeMockSession();
      return noContent();
    },
  },
  { method: 'GET', path: '/auth/me', handle: () => ok(me()) },
  {
    method: 'GET',
    path: '/auth/csrf',
    handle: () => {
      openMockSession(CSRF_TOKEN);
      const token: Schemas['CsrfToken'] = { csrf_token: CSRF_TOKEN, header_name: 'X-CSRF-Token', cookie_name: 'gpudeck_csrf' };
      return ok(token);
    },
  },
  {
    method: 'POST',
    path: '/auth/change-password',
    handle: (req) => {
      const body = bodyOf<Schemas['ChangePasswordRequest']>(req);
      if (body.current_password !== password) return fail(401, 'INVALID_CREDENTIALS', 'The current password is wrong');
      if (!body.new_password || body.new_password.length < 12) {
        return fail(422, 'VALIDATION_ERROR', 'New password must be at least 12 characters', undefined);
      }
      password = body.new_password;
      passwordChangedAt = new Date().toISOString();
      return noContent();
    },
  },
  { method: 'GET', path: '/auth/sessions', handle: (req) => paginate(SESSIONS, req.query) },
  {
    method: 'DELETE',
    path: '/auth/sessions/:session_id',
    handle: (req) => {
      const idx = SESSIONS.findIndex((s) => s.id === req.params.session_id);
      if (idx < 0) return fail(404, 'NOT_FOUND', 'Session not found');
      if (SESSIONS[idx].is_current) return fail(409, 'CONFLICT', 'Use logout to end the current session');
      SESSIONS.splice(idx, 1);
      return noContent();
    },
  },
  {
    method: 'POST',
    path: '/auth/logout-all',
    handle: () => {
      for (let i = SESSIONS.length - 1; i >= 0; i -= 1) {
        if (!SESSIONS[i].is_current) SESSIONS.splice(i, 1);
      }
      return noContent();
    },
  },
]);
