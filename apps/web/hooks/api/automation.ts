/**
 * Automation hooks — React Query wrappers (key factory, toasts, invalidation)
 * for rules, rule runs, layouts, swaps and alerts, plus the reference lists
 * the builders read (deployments, models, nodes, devices, clusters).
 */

'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/hooks/api/automation-api';
import { mergeRuleSchemas } from '@/hooks/api/automation-schemas';
import type {
  AlertChannelInput,
  AlertChannelUpdateInput,
  AlertEventListParams,
  LayoutInput,
  RuleInput,
  RuleListParams,
  RuleRunListParams,
  SwapCreateInput,
  SwapListParams,
} from '@/hooks/api/automation-api';

function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

// ─── Keys ─────────────────────────────────────────────────────────────────────

export const automationKeys = {
  all: ['automation'] as const,
  rules: () => [...automationKeys.all, 'rules'] as const,
  ruleList: (params: RuleListParams) => [...automationKeys.rules(), 'list', params] as const,
  rule: (id: string) => [...automationKeys.rules(), 'detail', id] as const,
  ruleRuns: (id: string, params: RuleRunListParams) => [...automationKeys.rules(), 'runs', id, params] as const,
  allRuns: (params: RuleRunListParams) => [...automationKeys.all, 'rule-runs', params] as const,
  run: (id: string) => [...automationKeys.all, 'rule-run', id] as const,
  schemas: () => [...automationKeys.all, 'rule-schemas'] as const,
  layouts: () => [...automationKeys.all, 'layouts'] as const,
  layoutList: (params: object) => [...automationKeys.layouts(), 'list', params] as const,
  layout: (id: string) => [...automationKeys.layouts(), 'detail', id] as const,
  layoutPreview: (id: string) => [...automationKeys.layouts(), 'preview', id] as const,
  swaps: () => [...automationKeys.all, 'swaps'] as const,
  swapList: (params: SwapListParams) => [...automationKeys.swaps(), 'list', params] as const,
  swap: (id: string) => [...automationKeys.swaps(), 'detail', id] as const,
  channels: () => [...automationKeys.all, 'alert-channels'] as const,
  channelList: (params: object) => [...automationKeys.channels(), 'list', params] as const,
  events: () => [...automationKeys.all, 'alert-events'] as const,
  eventList: (params: AlertEventListParams) => [...automationKeys.events(), 'list', params] as const,
  event: (id: string) => [...automationKeys.events(), 'detail', id] as const,
  ref: (name: string) => [...automationKeys.all, 'ref', name] as const,
};

const REF_STALE = 60_000;

// ─── Rules ────────────────────────────────────────────────────────────────────

export function useRules(params: RuleListParams) {
  return useQuery({
    queryKey: automationKeys.ruleList(params),
    queryFn: () => api.listRules(params),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });
}

export function useRule(id: string | undefined) {
  return useQuery({
    queryKey: automationKeys.rule(id ?? ''),
    queryFn: () => api.getRule(id as string),
    enabled: !!id,
    staleTime: 15_000,
  });
}

export function useRuleRuns(ruleId: string | undefined, params: RuleRunListParams) {
  return useQuery({
    queryKey: automationKeys.ruleRuns(ruleId ?? '', params),
    queryFn: () => api.listRuleRuns(ruleId as string, params),
    enabled: !!ruleId,
    staleTime: 10_000,
    placeholderData: (prev) => prev,
  });
}

export function useAllRuleRuns(params: RuleRunListParams, enabled = true) {
  return useQuery({
    queryKey: automationKeys.allRuns(params),
    queryFn: () => api.listAllRuleRuns(params),
    enabled,
    staleTime: 10_000,
    placeholderData: (prev) => prev,
  });
}

export function useRuleRun(id: string | undefined) {
  return useQuery({
    queryKey: automationKeys.run(id ?? ''),
    queryFn: () => api.getRuleRun(id as string),
    enabled: !!id,
  });
}

/** Contract schemas: the bundled copy, overlaid with the server's when it answers. */
export function useRuleSchemas() {
  const query = useQuery({
    queryKey: automationKeys.schemas(),
    queryFn: api.getRuleSchemas,
    staleTime: Infinity,
    retry: false,
  });
  return { ...query, schemas: mergeRuleSchemas(query.data) };
}

function useInvalidateRules() {
  const queryClient = useQueryClient();
  return (id?: string) => {
    void queryClient.invalidateQueries({ queryKey: automationKeys.rules() });
    if (id) void queryClient.invalidateQueries({ queryKey: automationKeys.rule(id) });
  };
}

export function useCreateRule() {
  const invalidate = useInvalidateRules();
  return useMutation({
    mutationFn: (body: RuleInput) => api.createRule(body),
    onSuccess: (rule) => {
      invalidate();
      toast.success(`Rule "${rule.name}" created${rule.dryRun ? ' in dry-run mode' : ''}`);
    },
    onError: (error) => toast.error(message(error, 'Could not create the rule')),
  });
}

export function useUpdateRule() {
  const invalidate = useInvalidateRules();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<RuleInput> }) => api.updateRule(id, body),
    onSuccess: (rule) => {
      invalidate(rule.id);
      toast.success(`Rule "${rule.name}" saved`);
    },
    onError: (error) => toast.error(message(error, 'Could not save the rule')),
  });
}

export function useDeleteRule() {
  const invalidate = useInvalidateRules();
  return useMutation({
    mutationFn: (id: string) => api.deleteRule(id),
    onSuccess: () => {
      invalidate();
      toast.success('Rule deleted');
    },
    onError: (error) => toast.error(message(error, 'Could not delete the rule')),
  });
}

/** Enable / disable with an optimistic flip in every cached list page (rolled back on failure). */
export function useToggleRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => (enabled ? api.enableRule(id) : api.disableRule(id)),
    onMutate: async ({ id, enabled }) => {
      await queryClient.cancelQueries({ queryKey: automationKeys.rules() });
      const lists = queryClient.getQueriesData<api.Paged<api.Rule>>({ queryKey: [...automationKeys.rules(), 'list'] });
      for (const [key, data] of lists) {
        if (data?.items) queryClient.setQueryData<api.Paged<api.Rule>>(key, { ...data, items: data.items.map((r) => (r.id === id ? { ...r, enabled } : r)) });
      }
      const detail = queryClient.getQueryData<api.Rule>(automationKeys.rule(id));
      if (detail) queryClient.setQueryData<api.Rule>(automationKeys.rule(id), { ...detail, enabled });
      return { lists, detail };
    },
    onError: (error, { id }, context) => {
      for (const [key, data] of context?.lists ?? []) queryClient.setQueryData(key, data);
      if (context?.detail) queryClient.setQueryData(automationKeys.rule(id), context.detail);
      toast.error(message(error, 'Could not change the rule'));
    },
    onSuccess: (rule) => {
      queryClient.setQueryData(automationKeys.rule(rule.id), rule);
      toast.success(rule.enabled ? `"${rule.name}" enabled` : `"${rule.name}" disabled — it will not be evaluated`);
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: automationKeys.rules() }),
  });
}

export function useDryRunRule(options: { silent?: boolean } = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.dryRunRule(id),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.rules() });
      void queryClient.invalidateQueries({ queryKey: [...automationKeys.all, 'rule-runs'] });
      if (!options.silent) {
        toast.success(run.outcome === 'dry_run' ? `Dry run: would act (${run.actions.length} action${run.actions.length === 1 ? '' : 's'})` : `Dry run: ${run.outcome.replace('_', ' ')}`);
      }
    },
    onError: (error) => toast.error(message(error, 'Dry run failed')),
  });
}

export function useRunRuleNow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.runRuleNow(id),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.rules() });
      void queryClient.invalidateQueries({ queryKey: [...automationKeys.all, 'rule-runs'] });
      void queryClient.invalidateQueries({ queryKey: automationKeys.swaps() });
      toast.success(run.fired ? `Rule fired: ${run.actions.length} action${run.actions.length === 1 ? '' : 's'} queued` : `Evaluated: ${run.outcome.replace('_', ' ')}`);
    },
    onError: (error) => toast.error(message(error, 'Could not run the rule')),
  });
}

// ─── Layouts ──────────────────────────────────────────────────────────────────

export function useLayouts(params: { page?: number; pageSize?: number; search?: string; sort?: string } = {}) {
  return useQuery({
    queryKey: automationKeys.layoutList(params),
    queryFn: () => api.listLayouts(params),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });
}

export function useLayout(id: string | undefined) {
  return useQuery({
    queryKey: automationKeys.layout(id ?? ''),
    queryFn: () => api.getLayout(id as string),
    enabled: !!id,
    staleTime: 15_000,
  });
}

function useInvalidateLayouts() {
  const queryClient = useQueryClient();
  return (id?: string) => {
    void queryClient.invalidateQueries({ queryKey: automationKeys.layouts() });
    if (id) void queryClient.invalidateQueries({ queryKey: automationKeys.layout(id) });
  };
}

export function useCreateLayout() {
  const invalidate = useInvalidateLayouts();
  return useMutation({
    mutationFn: (body: LayoutInput) => api.createLayout(body),
    onSuccess: (layout) => {
      invalidate();
      toast.success(`Layout "${layout.name}" created`);
    },
    onError: (error) => toast.error(message(error, 'Could not create the layout')),
  });
}

export function useUpdateLayout() {
  const invalidate = useInvalidateLayouts();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<LayoutInput> }) => api.updateLayout(id, body),
    onSuccess: (layout) => {
      invalidate(layout.id);
      toast.success(`Layout "${layout.name}" saved`);
    },
    onError: (error) => toast.error(message(error, 'Could not save the layout')),
  });
}

export function useDeleteLayout() {
  const invalidate = useInvalidateLayouts();
  return useMutation({
    mutationFn: (id: string) => api.deleteLayout(id),
    onSuccess: () => {
      invalidate();
      toast.success('Layout deleted');
    },
    onError: (error) => toast.error(message(error, 'Could not delete the layout')),
  });
}

export function useCaptureLayout() {
  const invalidate = useInvalidateLayouts();
  return useMutation({
    mutationFn: (body: { name: string; description?: string | null; cluster_id?: string | null }) => api.captureLayout(body),
    onSuccess: (layout) => {
      invalidate();
      toast.success(`Captured the current placement as "${layout.name}"`);
    },
    onError: (error) => toast.error(message(error, 'Could not capture the current layout')),
  });
}

export function usePreviewLayout() {
  return useMutation({
    mutationFn: (id: string) => api.previewLayout(id),
    onError: (error) => toast.error(message(error, 'Could not preview the layout')),
  });
}

export function useApplyLayout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { drain_timeout_seconds: number; prefetch_only: boolean } }) => api.applyLayout(id, body),
    onSuccess: (result, vars) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.layouts() });
      void queryClient.invalidateQueries({ queryKey: automationKeys.swaps() });
      const n = result.swapOperationIds.length;
      toast.success(
        vars.body.prefetch_only
          ? `Prefetch started (${result.commandIds.length} command${result.commandIds.length === 1 ? '' : 's'})`
          : n === 0
            ? 'Layout already matches the current placement'
            : `Applying layout: ${n} swap${n === 1 ? '' : 's'} queued`,
      );
    },
    onError: (error) => toast.error(message(error, 'Could not apply the layout')),
  });
}

// ─── Swaps ────────────────────────────────────────────────────────────────────

export function useSwaps(params: SwapListParams, options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: automationKeys.swapList(params),
    queryFn: () => api.listSwaps(params),
    staleTime: 5_000,
    refetchInterval: options.refetchInterval ?? false,
    placeholderData: (prev) => prev,
  });
}

export function useSwap(id: string | undefined, options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: automationKeys.swap(id ?? ''),
    queryFn: () => api.getSwap(id as string),
    enabled: !!id,
    staleTime: 3_000,
    refetchInterval: options.refetchInterval ?? false,
  });
}

export function useCreateSwap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SwapCreateInput) => api.createSwap(body),
    onSuccess: (swap) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.swaps() });
      toast.success(swap.dryRun ? 'Swap planned (dry run)' : `Swap queued: ${swap.kind.replace('_', ' ')}`);
    },
    onError: (error) => toast.error(message(error, 'Could not start the swap')),
  });
}

export function useCancelSwap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.cancelSwap(id),
    onSuccess: (swap) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.swaps() });
      toast.success(`Swap ${swap.state === 'cancelled' ? 'cancelled' : 'cancel requested'}`);
    },
    onError: (error) => toast.error(message(error, 'Could not cancel the swap')),
  });
}

export function useRetrySwap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.retrySwap(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.swaps() });
      toast.success('Swap queued again');
    },
    onError: (error) => toast.error(message(error, 'Could not retry the swap')),
  });
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

export function useAlertChannels(params: { page?: number; pageSize?: number; search?: string; type?: api.AlertChannelType | '' } = {}) {
  return useQuery({
    queryKey: automationKeys.channelList(params),
    queryFn: () => api.listAlertChannels(params),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useCreateAlertChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AlertChannelInput) => api.createAlertChannel(body),
    onSuccess: (channel) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.channels() });
      toast.success(`Channel "${channel.name}" created`);
    },
    onError: (error) => toast.error(message(error, 'Could not create the channel')),
  });
}

export function useUpdateAlertChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AlertChannelUpdateInput }) => api.updateAlertChannel(id, body),
    onSuccess: (channel) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.channels() });
      toast.success(`Channel "${channel.name}" saved`);
    },
    onError: (error) => toast.error(message(error, 'Could not save the channel')),
  });
}

export function useDeleteAlertChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteAlertChannel(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.channels() });
      toast.success('Channel deleted');
    },
    onError: (error) => toast.error(message(error, 'Could not delete the channel')),
  });
}

export function useTestAlertChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.testAlertChannel(id),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.channels() });
      if (result.ok) toast.success(`Test message delivered${result.latencyMs !== null ? ` in ${result.latencyMs} ms` : ''}`);
      else toast.error(`Test failed${result.error ? `: ${result.error}` : ''}`);
    },
    onError: (error) => toast.error(message(error, 'Test send failed')),
  });
}

export function useAlertEvents(params: AlertEventListParams, options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: automationKeys.eventList(params),
    queryFn: () => api.listAlertEvents(params),
    staleTime: 10_000,
    refetchInterval: options.refetchInterval ?? false,
    placeholderData: (prev) => prev,
  });
}

export function useAlertEvent(id: string | undefined) {
  return useQuery({
    queryKey: automationKeys.event(id ?? ''),
    queryFn: () => api.getAlertEvent(id as string),
    enabled: !!id,
  });
}

function useEventAction(fn: (id: string) => Promise<api.AlertEvent>, success: string, failure: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (event) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.events() });
      queryClient.setQueryData(automationKeys.event(event.id), event);
      toast.success(success);
    },
    onError: (error) => toast.error(message(error, failure)),
  });
}

export function useAcknowledgeAlert() {
  return useEventAction(api.acknowledgeAlertEvent, 'Alert acknowledged', 'Could not acknowledge the alert');
}

export function useResolveAlert() {
  return useEventAction(api.resolveAlertEvent, 'Alert resolved', 'Could not resolve the alert');
}

export function useResendAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, channelId }: { id: string; channelId?: string }) => api.resendAlertEvent(id, channelId),
    onSuccess: (event) => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.events() });
      queryClient.setQueryData(automationKeys.event(event.id), event);
      toast.success('Alert re-sent');
    },
    onError: (error) => toast.error(message(error, 'Could not re-send the alert')),
  });
}

// ─── Reference lists ──────────────────────────────────────────────────────────

export function useRefDeployments(enabled = true) {
  return useQuery({ queryKey: automationKeys.ref('deployments'), queryFn: api.listRefDeployments, staleTime: REF_STALE, enabled });
}

export function useRefModels(enabled = true) {
  return useQuery({ queryKey: automationKeys.ref('models'), queryFn: api.listRefModels, staleTime: REF_STALE, enabled });
}

export function useRefNodes(enabled = true) {
  return useQuery({ queryKey: automationKeys.ref('nodes'), queryFn: api.listRefNodes, staleTime: REF_STALE, enabled });
}

export function useRefClusters(enabled = true) {
  return useQuery({ queryKey: automationKeys.ref('clusters'), queryFn: api.listRefClusters, staleTime: REF_STALE, enabled });
}

export function useRefDevices(enabled = true) {
  return useQuery({ queryKey: automationKeys.ref('devices'), queryFn: api.listRefDevices, staleTime: 15_000, enabled });
}

export function useRefInstances(enabled = true) {
  return useQuery({ queryKey: automationKeys.ref('instances'), queryFn: api.listRefInstances, staleTime: 15_000, enabled });
}
