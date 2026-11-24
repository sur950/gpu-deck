/**
 * Auth context — AuthProvider and useAuth().
 *
 * Provides auth state (user, session, isLoading, isAuthenticated) and
 * the core auth actions (login, logout) to the component tree.
 * Wraps the entire app via app/layout.tsx.
 *
 * lib/auth.ts  → raw API calls (no React)
 * context/auth-context.tsx → React context + provider (this file)
 * hooks/use-auth.ts → React Query mutations that consume the context
 */

'use client';

import {
  createContext,
  useContext,
  useState,
  useEffect,
  type JSX,
  type ReactNode,
} from 'react';
import { AuthError, ApiError, ForbiddenError, NetworkError } from '@/lib/api/client';
import { apiLogin, apiLogout, apiGetMe, buildUser } from '@/lib/auth';
import type {
  User,
  Session,
  LoginResult,
  CachedUser,
  AuthContextType,
} from '@/types/auth';

// ─── Session cache key ────────────────────────────────────────────────────────

const SESSION_CACHE_KEY = 'gpu_deck_user';

// ─── Context ──────────────────────────────────────────────────────────────────

export const AuthContext = createContext<AuthContextType | undefined>(undefined);

function cacheUser(user: User): void {
  const cache: CachedUser = { username: user.username, full_name: user.fullName };
  sessionStorage.setItem(SESSION_CACHE_KEY, JSON.stringify(cache));
}

// ─── Provider ─────────────────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [sessionCheckFailed, setSessionCheckFailed] = useState(false);

  // Restore session on mount by validating the HttpOnly cookie via GET /auth/me
  useEffect(() => {
    let mounted = true;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    async function loadSession() {
      try {
        const me = await apiGetMe();
        const restoredUser = buildUser(me);

        if (mounted) {
          setSessionCheckFailed(false);
          setUser(restoredUser);
          setSession({
            id: '',
            userId: restoredUser.id,
            expiresAt: '',
            createdAt: '',
            userAgent: '',
          });
        }

        cacheUser(restoredUser);
      } catch (error) {
        if (error instanceof NetworkError) {
          // Backend is unreachable — HealthCheckProvider shows the reconnecting bar.
        } else if (!(error instanceof AuthError)) {
          console.error('[Auth] Session check failed:', error);
        }
        sessionStorage.removeItem(SESSION_CACHE_KEY);
        if (mounted) {
          setUser(null);
          setSession(null);
          // Only a 401 means the session is gone; anything else is the backend
          // being unreachable or failing, and the cookie may still be valid —
          // keep checking (1 s, 2 s, 4 s … up to 10 s apart) until it answers.
          const transient = !(error instanceof AuthError);
          setSessionCheckFailed(transient);
          if (transient) {
            retryTimer = setTimeout(loadSession, Math.min(1_000 * 2 ** attempt++, 10_000));
          }
        }
      } finally {
        if (mounted) setIsLoading(false);
      }
    }

    loadSession();
    return () => {
      mounted = false;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

  async function refreshUser(): Promise<void> {
    try {
      const me = await apiGetMe();
      setUser(buildUser(me));
    } catch {
      // silently ignore — caller can handle if needed
    }
  }

  async function login(username: string, password: string): Promise<LoginResult> {
    try {
      const response = await apiLogin(username, password);
      const me = await apiGetMe();
      const loggedInUser = buildUser(me);

      cacheUser(loggedInUser);

      const expiresAt = new Date(Date.now() + response.expires_in * 1000).toISOString();
      const newSession: Session = {
        id: '',
        userId: loggedInUser.id,
        expiresAt,
        createdAt: new Date().toISOString(),
        userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
      };

      setUser(loggedInUser);
      setSession(newSession);

      const redirectTo = '/dashboard';
      return { success: true, session: newSession, user: loggedInUser, redirectTo };
    } catch (error) {
      const message =
        error instanceof ApiError || error instanceof AuthError || error instanceof ForbiddenError
          ? error.message
          : error instanceof NetworkError
            ? 'Cannot reach the server. Please try again.'
            : 'Invalid username or password';
      return { success: false, error: message };
    }
  }

  async function logout(): Promise<void> {
    try {
      await apiLogout();
    } catch {
      // Don't block local cleanup on API failure
    } finally {
      sessionStorage.removeItem(SESSION_CACHE_KEY);
      setUser(null);
      setSession(null);
    }
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        session,
        isLoading,
        isAuthenticated: !!user && !!session,
        sessionCheckFailed,
        login,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// ─── useAuth ──────────────────────────────────────────────────────────────────

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
