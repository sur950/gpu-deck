/**
 * Account hooks: profile, sessions, revoke one / all others.
 */

'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { getProfile, getSessions, logoutOtherSessions, revokeSession } from '@/lib/api/account';

export const accountKeys = {
  all: ['account'] as const,
  profile: () => [...accountKeys.all, 'profile'] as const,
  sessions: () => [...accountKeys.all, 'sessions'] as const,
};

export function useProfile() {
  return useQuery({ queryKey: accountKeys.profile(), queryFn: getProfile, staleTime: 60_000 });
}

export function useAdminSessions() {
  return useQuery({ queryKey: accountKeys.sessions(), queryFn: getSessions, staleTime: 15_000, refetchInterval: 60_000 });
}

export function useRevokeSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: revokeSession,
    onSuccess: () => {
      toast.success('Session signed out');
      void queryClient.invalidateQueries({ queryKey: accountKeys.sessions() });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : 'Could not sign out that session'),
  });
}

export function useLogoutOtherSessions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: logoutOtherSessions,
    onSuccess: () => {
      toast.success('Every other session is signed out');
      void queryClient.invalidateQueries({ queryKey: accountKeys.sessions() });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : 'Could not sign out the other sessions'),
  });
}
