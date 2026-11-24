/**
 * Security hooks — React Query wrappers (key factory, toasts, invalidation)
 * for API keys, the audit log and IP allow / block rules.
 */

'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/hooks/api/security-api';
import type {
  ApiKey,
  ApiKeyInput,
  ApiKeyListParams,
  ApiKeyUpdateInput,
  AuditListParams,
  IpRule,
  IpRuleInput,
  IpRuleScope,
  IpRuleUpdateInput,
  IpRuleAction,
} from '@/hooks/api/security-api';
import type { Paged } from '@/hooks/api/automation-api';

function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export interface IpRuleListParams {
  scope?: IpRuleScope | '';
  action?: IpRuleAction | '';
  enabled?: boolean | null;
}

export const securityKeys = {
  all: ['security'] as const,
  keys: () => [...securityKeys.all, 'api-keys'] as const,
  keyList: (params: ApiKeyListParams) => [...securityKeys.keys(), 'list', params] as const,
  keyStats: () => [...securityKeys.keys(), 'stats'] as const,
  key: (id: string) => [...securityKeys.keys(), 'detail', id] as const,
  audit: () => [...securityKeys.all, 'audit'] as const,
  auditList: (params: AuditListParams) => [...securityKeys.audit(), 'list', params] as const,
  auditEntry: (id: string) => [...securityKeys.audit(), 'detail', id] as const,
  ipRules: () => [...securityKeys.all, 'ip-rules'] as const,
  ipRuleList: (params: IpRuleListParams) => [...securityKeys.ipRules(), 'list', params] as const,
};

// ─── API keys ─────────────────────────────────────────────────────────────────

export function useApiKeys(params: ApiKeyListParams) {
  return useQuery({
    queryKey: securityKeys.keyList(params),
    queryFn: () => api.listApiKeys(params),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });
}

export function useApiKeyStats() {
  return useQuery({ queryKey: securityKeys.keyStats(), queryFn: api.getApiKeyStats, staleTime: 15_000 });
}

export function useApiKey(id: string | undefined) {
  return useQuery({
    queryKey: securityKeys.key(id ?? ''),
    queryFn: () => api.getApiKey(id as string),
    enabled: !!id,
    staleTime: 15_000,
  });
}

function useInvalidateKeys() {
  const queryClient = useQueryClient();
  return (key?: ApiKey) => {
    void queryClient.invalidateQueries({ queryKey: securityKeys.keys() });
    if (key) queryClient.setQueryData(securityKeys.key(key.id), key);
  };
}

/** Create returns the plaintext once — the caller shows it, it is never cached. */
export function useCreateApiKey() {
  const invalidate = useInvalidateKeys();
  return useMutation({
    mutationFn: (body: ApiKeyInput) => api.createApiKey(body),
    onSuccess: (key) => {
      invalidate();
      toast.success(`API key "${key.name}" created`);
    },
    onError: (error) => toast.error(message(error, 'Could not create the API key')),
  });
}

export function useUpdateApiKey() {
  const invalidate = useInvalidateKeys();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ApiKeyUpdateInput }) => api.updateApiKey(id, body),
    onSuccess: (key) => {
      invalidate(key);
      toast.success(`API key "${key.name}" saved`);
    },
    onError: (error) => toast.error(message(error, 'Could not save the API key')),
  });
}

/**
 * Status changes are optimistic: the row flips immediately in every cached
 * list page and rolls back if the backend refuses.
 */
function useKeyStatusMutation(fn: (id: string) => Promise<ApiKey>, nextStatus: ApiKey['status'], success: (k: ApiKey) => string, failure: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (key: ApiKey) => fn(key.id),
    onMutate: async (key) => {
      await queryClient.cancelQueries({ queryKey: securityKeys.keys() });
      const snapshots = queryClient.getQueriesData<Paged<ApiKey>>({ queryKey: securityKeys.keys() });
      for (const [queryKey, data] of snapshots) {
        if (data && 'items' in data) {
          queryClient.setQueryData<Paged<ApiKey>>(queryKey, {
            ...data,
            items: data.items.map((k) => (k.id === key.id ? { ...k, status: nextStatus } : k)),
          });
        }
      }
      return { snapshots };
    },
    onError: (error, _key, context) => {
      for (const [queryKey, data] of context?.snapshots ?? []) queryClient.setQueryData(queryKey, data);
      toast.error(message(error, failure));
    },
    onSuccess: (key) => {
      queryClient.setQueryData(securityKeys.key(key.id), key);
      toast.success(success(key));
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: securityKeys.keys() });
    },
  });
}

export function useRevokeApiKey() {
  return useKeyStatusMutation(api.revokeApiKey, 'revoked', (k) => `"${k.name}" revoked — requests with it now fail`, 'Could not revoke the key');
}

export function useSuspendApiKey() {
  return useKeyStatusMutation(api.suspendApiKey, 'suspended', (k) => `"${k.name}" suspended`, 'Could not suspend the key');
}

export function useActivateApiKey() {
  return useKeyStatusMutation(api.activateApiKey, 'active', (k) => `"${k.name}" active again`, 'Could not activate the key');
}

export function useRotateApiKey() {
  const invalidate = useInvalidateKeys();
  return useMutation({
    mutationFn: (id: string) => api.rotateApiKey(id),
    onSuccess: (key) => {
      invalidate();
      toast.success(`"${key.name}" rotated — the old secret stops working now`);
    },
    onError: (error) => toast.error(message(error, 'Could not rotate the key')),
  });
}

export function useDeleteApiKey() {
  const invalidate = useInvalidateKeys();
  return useMutation({
    mutationFn: (id: string) => api.deleteApiKey(id),
    onSuccess: () => {
      invalidate();
      toast.success('API key deleted');
    },
    onError: (error) => toast.error(message(error, 'Could not delete the key')),
  });
}

// ─── Audit log ────────────────────────────────────────────────────────────────

export function useAuditLogs(params: AuditListParams) {
  return useQuery({
    queryKey: securityKeys.auditList(params),
    queryFn: () => api.listAuditLogs(params),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });
}

export function useAuditLog(id: string | undefined) {
  return useQuery({
    queryKey: securityKeys.auditEntry(id ?? ''),
    queryFn: () => api.getAuditLog(id as string),
    enabled: !!id,
    staleTime: 60_000,
  });
}

// ─── IP rules ─────────────────────────────────────────────────────────────────

export function useIpRules(params: IpRuleListParams = {}) {
  return useQuery({
    queryKey: securityKeys.ipRuleList(params),
    queryFn: () => api.listIpRules(params),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });
}

function useInvalidateIpRules() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: securityKeys.ipRules() });
}

export function useCreateIpRule() {
  const invalidate = useInvalidateIpRules();
  return useMutation({
    mutationFn: (body: IpRuleInput) => api.createIpRule(body),
    onSuccess: (rule) => {
      invalidate();
      toast.success(`${rule.action === 'allow' ? 'Allow' : 'Block'} rule for ${rule.cidr} added`);
    },
    onError: (error) => toast.error(message(error, 'Could not add the rule')),
  });
}

export function useUpdateIpRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: IpRuleUpdateInput }) => api.updateIpRule(id, body),
    onMutate: async ({ id, body }) => {
      await queryClient.cancelQueries({ queryKey: securityKeys.ipRules() });
      const snapshots = queryClient.getQueriesData<Paged<IpRule>>({ queryKey: securityKeys.ipRules() });
      for (const [queryKey, data] of snapshots) {
        if (data && 'items' in data) {
          queryClient.setQueryData<Paged<IpRule>>(queryKey, {
            ...data,
            items: data.items.map((r) =>
              r.id === id
                ? {
                    ...r,
                    enabled: body.enabled ?? r.enabled,
                    ...(body.cidr ? { cidr: body.cidr } : {}),
                    ...(body.action ? { action: body.action } : {}),
                    ...(body.scope ? { scope: body.scope } : {}),
                  }
                : r,
            ),
          });
        }
      }
      return { snapshots };
    },
    onError: (error, _vars, context) => {
      for (const [queryKey, data] of context?.snapshots ?? []) queryClient.setQueryData(queryKey, data);
      toast.error(message(error, 'Could not save the rule'));
    },
    onSuccess: (rule) => toast.success(`Rule for ${rule.cidr} saved`),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: securityKeys.ipRules() }),
  });
}

export function useDeleteIpRule() {
  const invalidate = useInvalidateIpRules();
  return useMutation({
    mutationFn: (id: string) => api.deleteIpRule(id),
    onSuccess: () => {
      invalidate();
      toast.success('Rule deleted');
    },
    onError: (error) => toast.error(message(error, 'Could not delete the rule')),
  });
}

export function useCheckIp() {
  return useMutation({
    mutationFn: (body: { ip: string; scope: IpRuleScope }) => api.checkIp(body),
    onError: (error) => toast.error(message(error, 'Could not check the address')),
  });
}
