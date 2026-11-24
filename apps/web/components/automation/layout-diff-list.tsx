'use client';

import { ArrowRight, ShieldCheck } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { LayoutDiffEntry } from '@/hooks/api/automation-api';
import { DIFF_META, gib, type ClientDiffEntry, type DiffAction } from '@/components/automation/layout-model';

export function DiffChip({ action }: { action: DiffAction }) {
  const meta = DIFF_META[action];
  return (
    <span className={cn('inline-flex h-5 w-[78px] shrink-0 items-center gap-1 rounded-[5px] px-1.5 text-[11px] font-semibold ring-1 ring-inset', meta.className)}>
      <span className="w-2.5 text-center font-mono">{meta.sign}</span>
      {meta.label}
    </span>
  );
}

/** Server diff sides are `{replicas, placements: [{node, devices, replicas}]}`; fall back to compact JSON. */
export function describeSide(side: Record<string, unknown> | null): string {
  if (!side) return 'not loaded';
  const placements = side.placements as { node?: string; devices?: string[]; replicas?: number }[] | undefined;
  if (Array.isArray(placements) && placements.length) {
    return placements.map((p) => `${p.devices?.length ? p.devices.join(' + ') : (p.node ?? 'scheduler')}${(p.replicas ?? 1) > 1 ? ` ×${p.replicas}` : ''}`).join(', ');
  }
  if (typeof side.replicas === 'number') return `${side.replicas} replica${side.replicas === 1 ? '' : 's'}`;
  return Object.entries(side)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(', ');
}

interface ClientDiffListProps {
  entries: ClientDiffEntry[];
  names: Map<string, string>;
  colors: Map<string, string>;
  showUnchanged?: boolean;
}

/** Edits vs what runs now — recomputed on every drag. */
export function ClientDiffList({ entries, names, colors, showUnchanged = false }: ClientDiffListProps) {
  const changed = entries.filter((e) => e.action !== 'noop' || e.protected);
  const unchanged = entries.filter((e) => e.action === 'noop' && !e.protected);
  const list = showUnchanged ? entries : changed;
  if (!list.length) return <p className="py-4 text-center text-xs text-muted-foreground">Matches what runs now — applying changes nothing.</p>;
  return (
    <ul className="space-y-1.5">
      {list.map((e) => (
        <li key={e.deploymentId} className="rounded-lg border border-border bg-background/50 px-3 py-2">
          <div className="flex items-center gap-2">
            <DiffChip action={e.action} />
            <span className="h-3.5 w-1 rounded-full" style={{ backgroundColor: colors.get(e.deploymentId) }} />
            <span className="truncate text-xs font-semibold">{names.get(e.deploymentId) ?? e.deploymentId.slice(0, 8)}</span>
            <span className="ml-auto font-mono text-[11px] tabular-nums text-muted-foreground">
              {e.fromReplicas} → {e.toReplicas}
            </span>
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-1 pl-[86px] text-[11px] text-muted-foreground">
            <span>{e.fromWhere.length ? e.fromWhere.join(', ') : 'not loaded'}</span>
            <ArrowRight className="size-3" />
            <span className="text-foreground/80">{e.toWhere.length ? e.toWhere.join(', ') : 'unloaded'}</span>
          </p>
          {e.protected && (
            <p className="mt-1 flex items-center gap-1 pl-[86px] text-[11px] text-success-dark dark:text-success-light">
              <ShieldCheck className="size-3" /> always-on — stays loaded
            </p>
          )}
        </li>
      ))}
      {!showUnchanged && unchanged.length > 0 && <li className="px-1 pt-1 text-[11px] text-muted-foreground">{unchanged.length} deployment{unchanged.length === 1 ? '' : 's'} unchanged</li>}
    </ul>
  );
}

/** The authoritative server preview (`POST /layouts/{id}/preview`). */
export function ServerDiffList({ entries }: { entries: LayoutDiffEntry[] }) {
  const changed = entries.filter((e) => e.action !== 'noop' || e.protected);
  const unchanged = entries.length - changed.length;
  if (!changed.length) return <p className="py-4 text-center text-xs text-muted-foreground">Nothing to change — this layout is already in place.</p>;
  return (
    <ul className="space-y-1.5">
      {changed.map((e) => (
        <li key={e.deploymentId + e.action} className="rounded-lg border border-border bg-background/50 px-3 py-2">
          <div className="flex items-center gap-2">
            <DiffChip action={e.action} />
            <span className="truncate text-xs font-semibold">{e.deploymentName ?? e.deploymentId.slice(0, 8)}</span>
            {e.needsPrefetchBytes > 0 && <span className="ml-auto text-[11px] text-muted-foreground">prefetch {gib(e.needsPrefetchBytes)}</span>}
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-1 pl-[86px] text-[11px] text-muted-foreground">
            <span>{describeSide(e.from)}</span>
            <ArrowRight className="size-3" />
            <span className="text-foreground/80">{e.action === 'unload' ? 'unloaded' : describeSide(e.to)}</span>
          </p>
          {e.protected && (
            <p className="mt-1 flex items-center gap-1 pl-[86px] text-[11px] text-success-dark dark:text-success-light">
              <ShieldCheck className="size-3" /> protected (always-on)
            </p>
          )}
          {e.placementFeasible === false && <p className="mt-1 pl-[86px] text-[11px] font-medium text-destructive">{e.placementReasons[0] ?? 'Does not fit'}</p>}
        </li>
      ))}
      {unchanged > 0 && <li className="px-1 pt-1 text-[11px] text-muted-foreground">{unchanged} deployment{unchanged === 1 ? '' : 's'} unchanged</li>}
    </ul>
  );
}
