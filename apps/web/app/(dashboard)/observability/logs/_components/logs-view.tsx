'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, Pause, Play, Radio, History, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { SearchInput } from '@/components/shared/search-input';
import { RangePills, ScopeSelect } from '@/components/charts/range-bar';
import { LiveIndicator } from '@/components/charts/live-indicator';
import { LogViewer } from '@/components/observability/log-viewer';
import { useLogHistory, useLogStream } from '@/hooks/use-logs';
import { useDeployments } from '@/hooks/use-metrics';
import { useNodes } from '@/hooks/use-topology';
import { useDebounce } from '@/hooks/use-debounce';
import { useUrlState } from '@/hooks/use-url-state';
import { findPreset } from '@/components/charts/series-utils';
import { logExportUrl } from '@/lib/api/logs';
import { LOG_LEVELS, LOG_SOURCES, LOG_SOURCE_LABELS } from '@/types/logs';
import type { LogChunk, LogLevel, LogSource } from '@/types/logs';

const DEFAULTS = { mode: 'live', range: '1h', level: '', source: '', node: '', deployment: '', q: '', request: '' };
const HISTORY_RANGES = ['15m', '1h', '6h', '24h', '7d'];
const LIVE_SEED_MS = 15 * 60_000;
const MAX_LINES = 2_000;

const LEVEL_OPTIONS = [
  { value: 'debug', label: 'Debug and up' },
  { value: 'info', label: 'Info and up' },
  { value: 'warn', label: 'Warnings and errors' },
  { value: 'error', label: 'Errors only' },
];

/** Wall clock for event handlers (re-anchors the history window). */
const nowMs = (): number => Date.now();

function toggleSource(current: LogSource[], source: LogSource): LogSource[] {
  return current.includes(source) ? current.filter((s) => s !== source) : [...current, source];
}

/** Observability › Logs: live tail (SSE) or time-range history, one filter row, level-coloured lines. */
export function LogsView() {
  const { params, setParams } = useUrlState(DEFAULTS);
  const live = params.mode !== 'history';
  const sources = useMemo(() => params.source.split(',').filter((s): s is LogSource => (LOG_SOURCES as string[]).includes(s)), [params.source]);
  const level = (LOG_LEVELS as string[]).includes(params.level) ? (params.level as LogLevel) : undefined;

  const [search, setSearch] = useState(params.q);
  const debounced = useDebounce(search, 300);
  useEffect(() => {
    if (debounced !== params.q) setParams({ q: debounced });
  }, [debounced, params.q, setParams]);

  const [paused, setPaused] = useState(false);
  const [follow, setFollow] = useState(true);
  const [clearedAt, setClearedAt] = useState<string | null>(null);

  const nodes = useNodes();
  const deployments = useDeployments();
  const nodeNames = useMemo(() => new Map((nodes.data ?? []).map((n) => [n.id, n.name])), [nodes.data]);

  // History: the selected range. Live: the last 15 minutes as a seed, then the stream.
  const [anchor, setAnchor] = useState(() => Date.now());
  const preset = findPreset(params.range, '1h');
  const span = live ? LIVE_SEED_MS : preset.ms;
  const filters = {
    nodeId: params.node || undefined,
    deploymentId: params.deployment || undefined,
    sources: sources.length > 0 ? sources : undefined,
    level,
    search: params.q || undefined,
    requestId: params.request || undefined,
  };
  const history = useLogHistory({
    ...filters,
    from: new Date(anchor - span).toISOString(),
    to: new Date(anchor).toISOString(),
    limit: live ? 200 : 300,
  });
  const tail = useLogStream(filters, live && !paused);

  const lines: LogChunk[] = useMemo(() => {
    const fromHistory = (history.data?.pages ?? []).flatMap((p) => p.items);
    if (!live) return fromHistory; // newest first
    const seen = new Set<string>();
    const merged: LogChunk[] = [];
    for (const c of [...fromHistory].reverse().concat(tail.chunks)) {
      const key = `${c.id}-${c.ts}`;
      if (seen.has(key)) continue;
      if (params.request && c.requestId !== params.request) continue;
      if (clearedAt && c.ts <= clearedAt) continue;
      seen.add(key);
      merged.push(c);
    }
    merged.sort((a, b) => a.ts.localeCompare(b.ts));
    return merged.length > MAX_LINES ? merged.slice(merged.length - MAX_LINES) : merged;
  }, [history.data, tail.chunks, live, params.request, clearedAt]);

  const counts = useMemo(() => {
    const c = { warn: 0, error: 0 };
    for (const l of lines) if (l.level === 'warn' || l.level === 'error') c[l.level] += 1;
    return c;
  }, [lines]);

  const setMode = (mode: 'live' | 'history') => {
    setAnchor(nowMs());
    setFollow(true);
    setPaused(false);
    setParams({ mode });
  };

  const onFollowChange = useCallback((f: boolean) => setFollow(f), []);

  async function exportLogs() {
    const url = logExportUrl({ from: new Date(anchor - span).toISOString(), to: new Date(anchor).toISOString(), nodeId: filters.nodeId, sources: filters.sources, level });
    try {
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = href;
      a.download = `gpu-deck-logs-${new Date(anchor).toISOString().slice(0, 16).replace(/[:T]/g, '-')}.txt`;
      a.click();
      URL.revokeObjectURL(href);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Export failed');
    }
  }

  const statusText = live
    ? `${lines.length.toLocaleString()} lines${paused ? ' · paused' : ''}`
    : `${lines.length.toLocaleString()} newest lines · ${preset.label.toLowerCase()}`;

  return (
    <PageContainer>
      <PageHeader
        title="Logs"
        description="Engine, agent, rule and swap logs from every node — live, or for a time range."
        actions={
          <div role="radiogroup" aria-label="Mode" className="inline-flex h-9 items-center gap-0.5 rounded-full border border-border bg-background/60 p-0.5">
            {(
              [
                { id: 'live', label: 'Live tail', icon: Radio },
                { id: 'history', label: 'History', icon: History },
              ] as const
            ).map((m) => {
              const active = (m.id === 'live') === live;
              const Icon = m.icon;
              return (
                <button
                  key={m.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setMode(m.id)}
                  className={cn(
                    'inline-flex h-7 items-center gap-1.5 rounded-full px-3 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30',
                    active ? 'bg-foreground text-background shadow-premium' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                  )}
                >
                  <Icon className="size-3.5" />
                  {m.label}
                </button>
              );
            })}
          </div>
        }
      />

      <Card className="gap-0 overflow-hidden rounded-xl py-0">
        <div className="flex flex-col gap-3 border-b border-border px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <SearchInput value={search} onChange={setSearch} placeholder="Search log text…" className="max-w-xs" />
            <ScopeSelect value={params.level} onValueChange={(v) => setParams({ level: v })} placeholder="All levels" options={LEVEL_OPTIONS} width="w-[170px]" ariaLabel="Minimum level" />
            <ScopeSelect value={params.node} onValueChange={(v) => setParams({ node: v })} placeholder="All nodes" options={(nodes.data ?? []).map((n) => ({ value: n.id, label: n.name }))} width="w-[160px]" ariaLabel="Node" />
            <ScopeSelect
              value={params.deployment}
              onValueChange={(v) => setParams({ deployment: v })}
              placeholder="All deployments"
              options={(deployments.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
              width="w-[170px]"
              ariaLabel="Deployment"
            />
            {!live && <RangePills value={preset.id} onChange={(id) => {
                  setAnchor(nowMs());
                  setParams({ range: id });
                }} presets={HISTORY_RANGES} />}
          </div>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Sources">
            <span className="mr-1 text-[11px] font-medium text-muted-foreground">Sources</span>
            {LOG_SOURCES.map((source) => {
              const on = sources.length === 0 || sources.includes(source);
              return (
                <button
                  key={source}
                  type="button"
                  aria-pressed={sources.includes(source)}
                  onClick={() => setParams({ source: toggleSource(sources, source).join(',') })}
                  className={cn(
                    'h-7 rounded-full border px-2.5 text-[11px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30',
                    sources.includes(source)
                      ? 'border-foreground/30 bg-foreground/10 text-foreground'
                      : on
                        ? 'border-border text-muted-foreground hover:bg-accent hover:text-foreground'
                        : 'border-border text-muted-foreground/60 hover:bg-accent',
                  )}
                >
                  {LOG_SOURCE_LABELS[source]}
                </button>
              );
            })}
            {sources.length > 0 && (
              <button type="button" onClick={() => setParams({ source: '' })} className="ml-1 text-[11px] font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
                All sources
              </button>
            )}
            {params.request && (
              <span className="ml-2 inline-flex h-7 items-center gap-1.5 rounded-full border border-info/30 bg-info/10 pl-2.5 pr-1 text-[11px] font-medium text-info-dark dark:text-info-light">
                request <span className="font-mono">{params.request.slice(0, 13)}…</span>
                <button type="button" aria-label="Clear request filter" onClick={() => setParams({ request: '' })} className="rounded-full p-0.5 hover:bg-info/20">
                  <X className="size-3" />
                </button>
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2 text-xs text-muted-foreground">
          <div className="flex items-center gap-3">
            {live ? <LiveIndicator status={paused ? 'idle' : tail.status} className="h-7" /> : null}
            <span className="tabular-nums">{statusText}</span>
            {counts.error > 0 && <span className="font-semibold text-error-dark tabular-nums dark:text-error-light">{counts.error} error{counts.error === 1 ? '' : 's'}</span>}
            {counts.warn > 0 && <span className="font-semibold text-warning-dark tabular-nums dark:text-warning-light">{counts.warn} warning{counts.warn === 1 ? '' : 's'}</span>}
          </div>
          <div className="flex items-center gap-1.5">
            {live && (
              <>
                <div className="mr-2 flex items-center gap-2">
                  <Switch id="follow" checked={follow} onCheckedChange={(v) => setFollow(Boolean(v))} />
                  <Label htmlFor="follow" className="text-xs font-normal text-muted-foreground">
                    Follow
                  </Label>
                </div>
                <Button variant="ghost" size="xs" className="gap-1.5" onClick={() => setPaused((p) => !p)}>
                  {paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
                  {paused ? 'Resume' : 'Pause'}
                </Button>
                <Button
                  variant="ghost"
                  size="xs"
                  className="gap-1.5"
                  onClick={() => {
                    setClearedAt(new Date().toISOString());
                    tail.clear();
                  }}
                >
                  <Trash2 className="size-3.5" />
                  Clear
                </Button>
              </>
            )}
            <Button variant="ghost" size="xs" className="gap-1.5" onClick={() => void exportLogs()}>
              <Download className="size-3.5" />
              Export
            </Button>
          </div>
        </div>

        {history.isError && lines.length === 0 ? (
          <div className="p-4">
            <ErrorState title="Couldn't load logs" description="The log query failed. Try again, or narrow the time range." onRetry={() => void history.refetch()} />
          </div>
        ) : (
          <LogViewer
            className="h-[calc(100vh-380px)] min-h-[420px]"
            chunks={lines}
            order={live ? 'asc' : 'desc'}
            follow={live ? follow : false}
            onFollowChange={live ? onFollowChange : undefined}
            search={params.q}
            showDay={!live && preset.ms > 24 * 3_600_000}
            nodeNames={nodeNames}
            onRequestClick={(id) => setParams({ request: id })}
            emptyTitle={history.isPending ? 'Loading log lines…' : 'No log lines'}
            emptyDescription={history.isPending ? undefined : live ? 'Waiting for new lines that match these filters.' : 'Nothing in this range matches these filters.'}
            footer={
              !live && history.hasNextPage ? (
                <div className="flex justify-center py-3 font-sans">
                  <Button variant="outline" size="sm" className="rounded-sm" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>
                    {history.isFetchingNextPage ? 'Loading…' : 'Load older lines'}
                  </Button>
                </div>
              ) : null
            }
          />
        )}
      </Card>
    </PageContainer>
  );
}
