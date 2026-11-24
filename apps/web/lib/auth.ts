/**
 * Auth API calls and shared utilities.
 * No React — plain TypeScript only.
 * The React context (AuthProvider, useAuth) lives in context/auth-context.tsx.
 *
 * GPU Deck has one seeded admin: sign in with username + password, change the
 * password after signing in, sign out. No SSO, LDAP, invites, sign-up or
 * password reset.
 */

import { apiGet, apiPost } from '@/lib/api/client';
import { AUTH_ENDPOINTS } from '@/lib/api/endpoints';
import type { LoginApiResponse, MeResponse, User } from '@/types/auth';

// ─── Internal API calls ───────────────────────────────────────────────────────

export function apiLogin(username: string, password: string): Promise<LoginApiResponse> {
  return apiPost<LoginApiResponse>(AUTH_ENDPOINTS.login, { username, password });
}

export function apiLogout(): Promise<void> {
  return apiPost<void>(AUTH_ENDPOINTS.logout);
}

export function apiGetMe(): Promise<MeResponse> {
  return apiGet<MeResponse>(AUTH_ENDPOINTS.me);
}

export function apiChangePassword(currentPassword: string, newPassword: string): Promise<void> {
  return apiPost<void>(AUTH_ENDPOINTS.changePassword, {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

// ─── Shared helper ────────────────────────────────────────────────────────────

export function buildUser(me: MeResponse): User {
  return {
    id: me.id,
    username: me.username,
    fullName: me.display_name?.trim() || me.username,
    role: 'admin',
    createdAt: me.created_at ?? new Date().toISOString(),
    lastLoginAt: me.last_login_at ?? null,
    passwordChangedAt: me.password_changed_at ?? null,
  };
}
