'use client';

/**
 * Vault hooks — React Query over lib/api/vault.ts. Every key starts with
 * `vault`, so one invalidation also refreshes the credential pickers in the
 * cluster form (`['vault', 'picker', …]`). Reveal is never cached.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/vault';
import { ApiError } from '@/lib/api/client';
import type { Credential, CredentialCreate, CredentialKind, CredentialListParams, CredentialUpdate } from '@/types/vault';

export const vaultKeys = {
  all: ['vault'] as const,
  metadata: ['vault', 'metadata'] as const,
  stats: ['vault', 'stats'] as const,
  lists: ['vault', 'list'] as const,
  list: (params: CredentialListParams) => ['vault', 'list', params] as const,
  options: ['vault', 'options'] as const,
  detail: (id: string) => ['vault', 'detail', id] as const,
  usage: (id: string) => ['vault', 'usage', id] as const,
};

/** Backend message, with friendlier text for the vault's stable codes. */
export function vaultErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'CREDENTIAL_IN_USE') return `${error.message}. Detach it first (or pick another credential there).`;
    if (error.code === 'CREDENTIAL_SYSTEM') return 'System credentials (internal CA, TLS keys) are managed by GPU Deck.';
    if (error.code === 'DUPLICATE_RESOURCE') return 'A credential with this name already exists.';
    return error.message;
  }
  return error instanceof Error ? error.message : 'Request failed';
}

export function useVaultMetadata() {
  return useQuery({ queryKey: vaultKeys.metadata, queryFn: api.getVaultMetadata, staleTime: 10 * 60_000 });
}

/** Kind catalog keyed by id (empty until the metadata loads). */
export function useCredentialKinds(): { kinds: CredentialKind[]; byId: Record<string, CredentialKind>; isLoading: boolean } {
  const meta = useVaultMetadata();
  const kinds = meta.data?.kinds ?? [];
  const byId: Record<string, CredentialKind> = {};
  for (const k of kinds) byId[k.id] = k;
  return { kinds, byId, isLoading: meta.isLoading };
}

export function useVaultStats() {
  return useQuery({ queryKey: vaultKeys.stats, queryFn: api.getVaultStats, staleTime: 15_000 });
}

export function useCredentials(params: CredentialListParams) {
  return useQuery({
    queryKey: vaultKeys.list(params),
    queryFn: () => api.listCredentials(params),
    staleTime: 15_000,
    placeholderData: keepPreviousData,
  });
}

/** Every active credential (pickers filter client-side by kind). */
export function useCredentialOptionsAll() {
  return useQuery({
    queryKey: vaultKeys.options,
    queryFn: async () => (await api.listCredentials({ status: 'active', pageSize: 500 })).items,
    staleTime: 30_000,
  });
}

export function useCredential(id: string | null | undefined) {
  return useQuery({ queryKey: vaultKeys.detail(id ?? ''), queryFn: () => api.getCredential(id as string), enabled: !!id });
}

export function useCredentialUsage(id: string | null | undefined) {
  return useQuery({ queryKey: vaultKeys.usage(id ?? ''), queryFn: () => api.getCredentialUsage(id as string), enabled: !!id, staleTime: 10_000 });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: vaultKeys.all });
}

export function useCreateCredential() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: CredentialCreate) => api.createCredential(body),
    onSuccess: (c) => {
      // Pickers select the new credential right away; the refetch below confirms it.
      qc.setQueryData<Credential[]>(vaultKeys.options, (old) => (old ? [c, ...old.filter((x) => x.id !== c.id)] : old));
      toast.success(`Credential “${c.name}” saved to the vault`);
      void invalidate();
    },
  });
}

export function useUpdateCredential() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CredentialUpdate }) => api.updateCredential(id, body),
    onSuccess: (c) => {
      qc.setQueryData<Credential>(vaultKeys.detail(c.id), c);
      toast.success(`Credential “${c.name}” updated`);
      void invalidate();
    },
  });
}

export function useDeleteCredential() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (c: Pick<Credential, 'id' | 'name'>) => api.deleteCredential(c.id),
    onSuccess: (_, c) => {
      toast.success(`Credential “${c.name}” deleted`);
      void invalidate();
    },
    onError: (error) => toast.error(`Could not delete: ${vaultErrorMessage(error)}`),
  });
}

/** Plaintext on demand (audited server-side); the result is never put in the query cache. */
export function useRevealCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.revealCredential(id),
    onSuccess: (_, id) => void qc.invalidateQueries({ queryKey: vaultKeys.usage(id) }),
    onError: (error) => toast.error(`Reveal failed: ${vaultErrorMessage(error)}`),
  });
}

export function useTestCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.testCredential(id),
    onSuccess: (result) => void qc.invalidateQueries({ queryKey: vaultKeys.usage(result.id) }),
    onError: (error) => toast.error(`Test failed: ${vaultErrorMessage(error)}`),
  });
}
