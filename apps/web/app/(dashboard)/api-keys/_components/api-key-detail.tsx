'use client';

import { useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Activity, Ban, CalendarClock, Coins, MessageSquare, Pause, Play, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { StatusBadge, toBadgeVariant } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { DetailList, SectionLabel } from '@/components/shared/detail-list';
import { RelativeTime } from '@/components/shared/relative-time';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { BarList } from '@/components/charts/bar-list';
import { TimeSeriesChart } from '@/components/charts/time-series-chart';
import type { ChartRow } from '@/components/charts/series-utils';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { useActivateApiKey, useApiKey, useRevokeApiKey, useRotateApiKey, useSuspendApiKey } from '@/hooks/api/security';
import type { ApiKeyCreated } from '@/hooks/api/security-api';
import { useUsageSummary } from '@/hooks/api/usage';
import { bucketStartMs, fmtTokens, fmtUsd } from '@/components/usage/usage-shared';
import { ExpiryCell, ExpiryDialog, RevealDialog } from '@/app/(dashboard)/api-keys/_components/api-keys-list';
import { useNow } from '@/components/automation/use-now';

export function ApiKeyDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const now = useNow(60_000);
  const { data: key, isLoading, isError, refetch } = useApiKey(id);
  const [range] = useState(() => {
    const to = new Date();
    const from = new Date(to.getTime() - 30 * 86_400_000);
    from.setUTCHours(0, 0, 0, 0);
    return { from: from.toISOString(), to: to.toISOString() };
  });
  const filters = useMemo(() => ({ apiKeyId: id, from: range.from, to: range.to }), [id, range]);
  const byDay = useUsageSummary(filters, ['day']);
  const byModel = useUsageSummary(filters, ['model']);
  const revoke = useRevokeApiKey();
  const suspend = useSuspendApiKey();
  const activate = useActivateApiKey();
  const rotate = useRotateApiKey();
  const [confirm, setConfirm] = useState<'revoke' | 'rotate' | null>(null);
  const [expiryOpen, setExpiryOpen] = useState(false);
  const [revealed, setRevealed] = useState<ApiKeyCreated | null>(null);

  if (isLoading) return <DashboardSkeleton />;
  if (isError || !key) {
    return (
      <PageContainer>
        <ErrorState title="API key not found" description="It may have been deleted." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const totals = byDay.data?.totals;
  const chart: ChartRow[] = (byDay.data?.rows ?? []).map((r) => ({ t: bucketStartMs(r.group.day), requests: r.totals.requestCount, cost: r.totals.costUsd })).filter((r) => !Number.isNaN(r.t));

  return (
    <PageContainer>
      <PageHeader
        title={key.name}
        description={key.description ?? 'API key for the serving API (OpenAI, Anthropic and Ollama formats).'}
        actions={
          <>
            <StatusBadge variant={toBadgeVariant(key.status)}>{key.status}</StatusBadge>
            {key.status !== 'revoked' && (
              <>
                <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setConfirm('rotate')}>
                  <RotateCw className="size-4" /> Rotate
                </Button>
                <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setExpiryOpen(true)}>
                  <CalendarClock className="size-4" /> Expiry
                </Button>
                {key.status === 'active' && (
                  <Button variant="outline" size="sm" className="rounded-sm" onClick={() => suspend.mutate(key)}>
                    <Pause className="size-4" /> Suspend
                  </Button>
                )}
                {key.status === 'suspended' && (
                  <Button variant="outline" size="sm" className="rounded-sm" onClick={() => activate.mutate(key)}>
                    <Play className="size-4" /> Activate
                  </Button>
                )}
                <Button variant="destructive" size="sm" className="rounded-sm" onClick={() => setConfirm('revoke')}>
                  <Ban className="size-4" /> Revoke
                </Button>
              </>
            )}
          </>
        }
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {byDay.isLoading || !totals ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[104px] rounded-md" />)
        ) : (
          <>
            <StatCard label="Requests (30 d)" value={fmtTokens(totals.requestCount)} icon={Activity} footnote={`${key.totalRequests.toLocaleString()} all time`} />
            <StatCard label="Tokens (30 d)" value={fmtTokens(totals.inputTokens + totals.outputTokens)} icon={MessageSquare} footnote={`${fmtTokens(totals.cachedTokens)} cached input`} />
            <StatCard label="Cost (30 d)" value={fmtUsd(totals.costUsd)} icon={Coins} footnote="Tokens + allocated GPU minutes" />
            <StatCard label="Last used" value={key.lastUsedAt ? <RelativeTime value={key.lastUsedAt} /> : 'never'} icon={CalendarClock} footnote={<>Expires <ExpiryCell value={key.expiresAt} now={now} /></>} />
          </>
        )}
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card className="gap-0 rounded-xl py-0">
          <CardHeader className="border-b border-border px-5 py-4">
            <CardTitle className="text-sm font-semibold">Requests per day</CardTitle>
            <CardDescription>Last 30 days</CardDescription>
          </CardHeader>
          <CardContent className="px-2 pb-4 pt-4 sm:px-4">
            {byDay.isLoading ? (
              <Skeleton className="h-[240px]" />
            ) : (
              <TimeSeriesChart data={chart} series={[{ key: 'requests', label: 'Requests', color: 'rgb(var(--chart-2))', kind: 'bar' }]} fromMs={Date.parse(range.from)} toMs={Date.parse(range.to)} formatValue={(v) => (v === null ? '—' : fmtTokens(v))} height={240} emptyTitle="No requests yet" emptyDescription="Requests with this key show up within a minute." />
            )}
          </CardContent>
        </Card>
        <div className="space-y-4">
          <Card className="rounded-xl">
            <CardContent className="space-y-3">
              <SectionLabel>Key</SectionLabel>
              <DetailList
                items={[
                  { label: 'Prefix', value: `${key.keyPrefix}••••••••`, mono: true },
                  { label: 'Created', value: new Date(key.createdAt).toLocaleString() },
                  { label: 'Last rotated', value: key.lastRotatedAt ? <RelativeTime value={key.lastRotatedAt} /> : 'never' },
                  { label: 'Expires', value: key.expiresAt ? new Date(key.expiresAt).toLocaleDateString() : 'never' },
                  { label: 'Revoked', value: key.revokedAt ? new Date(key.revokedAt).toLocaleString() : null },
                  ...Object.entries(key.metadata).map(([k, v]) => ({ label: k, value: String(v), mono: true })),
                ]}
              />
            </CardContent>
          </Card>
          <Card className="rounded-xl">
            <CardContent className="space-y-3">
              <SectionLabel>Cost by model (30 d)</SectionLabel>
              {byModel.isLoading ? <Skeleton className="h-24" /> : (byModel.data?.rows.length ?? 0) === 0 ? <p className="text-xs text-muted-foreground">No usage yet.</p> : <BarList items={(byModel.data?.rows ?? []).map((r) => ({ id: r.group.model, label: r.labels.model ?? r.group.model, value: r.totals.costUsd, valueLabel: fmtUsd(r.totals.costUsd), extraLabel: `${fmtTokens(r.totals.requestCount)} req` }))} />}
            </CardContent>
          </Card>
        </div>
      </div>
      <ConfirmationDialog open={confirm === 'revoke'} onOpenChange={(o) => !o && setConfirm(null)} title={`Revoke “${key.name}”?`} description="Requests with this key fail immediately. Revoked keys cannot be re-activated." confirmText="Revoke key" variant="destructive" onConfirm={() => revoke.mutate(key, { onSuccess: () => void refetch() })} isLoading={revoke.isPending} />
      <ConfirmationDialog open={confirm === 'rotate'} onOpenChange={(o) => !o && setConfirm(null)} title={`Rotate “${key.name}”?`} description="A new secret is issued and the current one stops working at once." confirmText="Rotate now" onConfirm={() => rotate.mutate(key.id, { onSuccess: setRevealed })} isLoading={rotate.isPending} />
      {expiryOpen && <ExpiryDialog key={key.id} apiKey={key} onClose={() => setExpiryOpen(false)} />}
      <RevealDialog created={revealed} onClose={() => { setRevealed(null); router.refresh(); }} />
    </PageContainer>
  );
}
