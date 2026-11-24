/**
 * Auth hooks — React Query mutations for auth operations.
 *
 * Responsibilities:
 *   lib/auth.ts                → raw API calls, no React
 *   context/auth-context.tsx   → AuthProvider, AuthContext, useAuth()
 *   hooks/use-auth.ts          → React Query mutations (this file)
 *
 * Every component/page that needs auth imports from here only.
 * useAuth and AuthProvider are re-exported for convenience.
 */

'use client';

import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useAuth, AuthProvider } from '@/context/auth-context';
import { apiChangePassword } from '@/lib/auth';
import type { LoginResult } from '@/types/auth';

// Re-export so components import everything from one place
export { useAuth, AuthProvider };

// ─── Login ────────────────────────────────────────────────────────────────────

interface LoginVars { username: string; password: string; }

export function useLogin() {
  const { login } = useAuth();
  return useMutation({
    mutationFn: async ({ username, password }: LoginVars): Promise<LoginResult> => {
      const result = await login(username, password);
      if (!result.success) throw new Error(result.error ?? 'Invalid username or password');
      return result;
    },
    // Error toast omitted — LoginForm shows inline error next to the field
  });
}

// ─── Logout ───────────────────────────────────────────────────────────────────

export function useLogout() {
  const { logout } = useAuth();
  return useMutation({
    mutationFn: () => logout(),
    onError: () => { toast.error('Logout failed. Please try again.'); },
  });
}

// ─── Change password (signed-in admin) ────────────────────────────────────────

interface ChangePasswordVars { currentPassword: string; newPassword: string; }

export function useChangePassword() {
  const { refreshUser } = useAuth();
  return useMutation({
    mutationFn: ({ currentPassword, newPassword }: ChangePasswordVars) =>
      apiChangePassword(currentPassword, newPassword),
    onSuccess: async () => {
      toast.success('Password changed.');
      await refreshUser();
    },
    // Error shown inline by the form
  });
}
