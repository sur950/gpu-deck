/**
 * Usage hooks — React Query wrappers for metering, price plans and billing
 * (server costs per API key, model, node and cluster; monthly reports).
 */

'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/hooks/api/usage-api';
import type { BillingReportInput, BillingReportStatus, PricePlanInput, UsageFilters, UsageGroupBy } from '@/hooks/api/usage-api';

function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export const usageKeys = {
  all: ['usage'] as const,
  summary: (filters: UsageFilters, groupBy: UsageGroupBy[]) => [...usageKeys.all, 'summary', filters, groupBy] as const,
  events: (filters: UsageFilters, params: object) => [...usageKeys.all, 'events', filters, params] as const,
  plans: () => [...usageKeys.all, 'price-plans'] as const,
  planList: (params: object) => [...usageKeys.plans(), 'list', params] as const,
  estimate: (params: object) => [...usageKeys.all, 'estimate', params] as const,
  reports: () => [...usageKeys.all, 'reports'] as const,
  reportList: (params: object) => [...usageKeys.reports(), 'list', params] as const,
  report: (id: string) => [...usageKeys.reports(), 'detail', id] as const,
};

// ─── Metering ─────────────────────────────────────────────────────────────────

export function useUsageSummary(filters: UsageFilters, groupBy: UsageGroupBy[], enabled = true) {
  return useQuery({
    queryKey: usageKeys.summary(filters, groupBy),
    queryFn: () => api.getUsageSummary(filters, groupBy),
    enabled,
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useUsageEvents(filters: UsageFilters, params: { page?: number; pageSize?: number; sort?: string }) {
  return useQuery({
    queryKey: usageKeys.events(filters, params),
    queryFn: () => api.listUsageEvents(filters, params),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}

// ─── Price plans ──────────────────────────────────────────────────────────────

export function usePricePlans(params: { page?: number; pageSize?: number; search?: string; sort?: string } = {}) {
  return useQuery({
    queryKey: usageKeys.planList(params),
    queryFn: () => api.listPricePlans(params),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useCreatePricePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: PricePlanInput) => api.createPricePlan(body),
    onSuccess: (plan) => {
      void queryClient.invalidateQueries({ queryKey: usageKeys.plans() });
      toast.success(`Price plan "${plan.name}" created`);
    },
    onError: (error) => toast.error(message(error, 'Could not create the price plan')),
  });
}

export function useUpdatePricePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<PricePlanInput> }) => api.updatePricePlan(id, body),
    onSuccess: (plan) => {
      void queryClient.invalidateQueries({ queryKey: usageKeys.plans() });
      toast.success(`Price plan "${plan.name}" saved`);
    },
    onError: (error) => toast.error(message(error, 'Could not save the price plan')),
  });
}

export function useDeletePricePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deletePricePlan(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: usageKeys.plans() });
      toast.success('Price plan deleted');
    },
    onError: (error) => toast.error(message(error, 'Could not delete the price plan')),
  });
}

/** Attach a plan to a cluster or node; invalidates the reference lists the billing page reads. */
export function useAttachPricePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, id, pricePlanId }: { kind: 'cluster' | 'node'; id: string; pricePlanId: string | null }) =>
      kind === 'cluster' ? api.setClusterPricePlan(id, pricePlanId) : api.setNodePricePlan(id, pricePlanId),
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({ queryKey: ['automation', 'ref'] });
      toast.success(vars.pricePlanId ? `Price plan attached to the ${vars.kind}` : `Price plan removed from the ${vars.kind}`);
    },
    onError: (error) => toast.error(message(error, 'Could not update the price plan attachment')),
  });
}

// ─── Billing ──────────────────────────────────────────────────────────────────

export function useBillingEstimate(params: { periodStart?: string; periodEnd?: string; clusterId?: string } = {}) {
  return useQuery({
    queryKey: usageKeys.estimate(params),
    queryFn: () => api.getBillingEstimate(params),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useBillingReports(params: { page?: number; pageSize?: number; search?: string; status?: BillingReportStatus | ''; sort?: string } = {}, options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: usageKeys.reportList(params),
    queryFn: () => api.listBillingReports(params),
    staleTime: 15_000,
    refetchInterval: options.refetchInterval ?? false,
    placeholderData: (prev) => prev,
  });
}

export function useBillingReport(id: string | undefined, options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: usageKeys.report(id ?? ''),
    queryFn: () => api.getBillingReport(id as string),
    enabled: !!id,
    staleTime: 15_000,
    refetchInterval: options.refetchInterval ?? false,
  });
}

export function useCreateBillingReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: BillingReportInput) => api.createBillingReport(body),
    onSuccess: (report) => {
      void queryClient.invalidateQueries({ queryKey: usageKeys.reports() });
      toast.success(`Report "${report.name}" is being generated`);
    },
    onError: (error) => toast.error(message(error, 'Could not create the report')),
  });
}

export function useRegenerateBillingReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.regenerateBillingReport(id),
    onSuccess: (report) => {
      void queryClient.invalidateQueries({ queryKey: usageKeys.reports() });
      toast.success(`Regenerating "${report.name}"`);
    },
    onError: (error) => toast.error(message(error, 'Could not regenerate the report')),
  });
}

export function useDeleteBillingReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteBillingReport(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: usageKeys.reports() });
      toast.success('Report deleted');
    },
    onError: (error) => toast.error(message(error, 'Could not delete the report')),
  });
}
