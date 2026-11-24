/**
 * Account API: the signed-in admin's profile and sessions
 * (`/auth/me`, `/auth/sessions`, `/auth/sessions/{id}`, `/auth/logout-all`).
 */

import { apiDelete, apiGet, apiPost, toQuery } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';
import type { AdminProfile, AdminSession } from '@/types/account';

type Schemas = components['schemas'];

function mapSession(row: Schemas['Session']): AdminSession {
  return {
    id: row.id,
    ipAddress: row.ip_address ?? null,
    userAgent: row.user_agent ?? null,
    createdAt: row.created_at,
    lastActiveAt: row.last_active_at ?? null,
    expiresAt: row.expires_at,
    isCurrent: row.is_current,
  };
}

export async function getProfile(): Promise<AdminProfile> {
  const row = await apiGet<Schemas['AdminUser']>('/auth/me');
  return {
    id: row.id,
    username: row.username,
    displayName: row.display_name ?? null,
    createdAt: row.created_at ?? null,
    lastLoginAt: row.last_login_at ?? null,
    lastLoginIp: row.last_login_ip ?? null,
    passwordChangedAt: row.password_changed_at ?? null,
  };
}

export async function getSessions(): Promise<AdminSession[]> {
  const rows = await apiGet<Schemas['Session'][]>(`/auth/sessions${toQuery({ page_size: 100 })}`);
  return rows.map(mapSession);
}

export function revokeSession(sessionId: string): Promise<void> {
  return apiDelete<void>(`/auth/sessions/${encodeURIComponent(sessionId)}`);
}

/** Revoke every session except the current one. */
export function logoutOtherSessions(): Promise<void> {
  return apiPost<void>('/auth/logout-all');
}
