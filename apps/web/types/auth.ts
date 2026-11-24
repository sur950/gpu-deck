// Auth-related type definitions — one seeded admin, no roles or orgs.

// ─── Re-exports ───────────────────────────────────────────────────────────────
export type { AuthError, ApiError, ForbiddenError } from '@/lib/api/client';

// ─── Raw backend response shapes (snake_case — as returned by the API) ────────

export interface LoginApiResponse {
  expires_in: number;
  user?: {
    id: string;
    username: string;
  };
}

export interface MeResponse {
  id: string;
  username: string;
  display_name?: string | null;
  created_at?: string;
  last_login_at?: string | null;
  password_changed_at?: string | null;
}

// ─── Internal session cache (non-sensitive display data in sessionStorage) ────

export interface CachedUser {
  username: string;
  full_name: string;
}

// ─── Auth context shape ───────────────────────────────────────────────────────

export interface AuthContextType {
  user: User | null;
  session: Session | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /**
   * The session check failed for a reason other than an invalid session
   * (backend unreachable, 5xx). The session may still be valid, and the check
   * keeps retrying on its own, so callers wait instead of sending the user to /login.
   */
  sessionCheckFailed: boolean;
  login: (username: string, password: string) => Promise<LoginResult>;
  logout: () => Promise<void>;
  /** Re-fetch the current admin and update the in-memory user (e.g. after a password change). */
  refreshUser: () => Promise<void>;
}

/** The only role: the seeded admin. Kept as a union so menus can format it. */
export type UserRole = 'admin';

export interface User {
  id: string;
  username: string;
  fullName: string;
  role: UserRole;
  createdAt: string;
  lastLoginAt: string | null;
  passwordChangedAt: string | null;
}

export interface Session {
  id: string;
  userId: string;
  expiresAt: string;
  createdAt: string;
  userAgent: string;
}

export interface LoginResult {
  success: boolean;
  redirectTo?: string;
  session?: Session;
  user?: User;
  error?: string;
}
