import Link from 'next/link';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ExternalBadge } from '@/components/models/external-badge';
import { formatBytes } from '@/utils/format';
import type { DeviceProcess, DeviceVendor } from '@/types/fleet';

/**
 * Per-process GPU memory (latest sample): pid, executable name (never its arguments), memory and
 * owner: a GPU Deck deployment, an external server GPU Deck observes, or nothing GPU Deck knows.
 */
export function GpuProcessesTable({
  processes,
  total,
  vendor,
  stale = false,
}: {
  processes: DeviceProcess[];
  total: number;
  vendor: DeviceVendor;
  /** Detached node / no fresh sample: last known processes, greyed. */
  stale?: boolean;
}) {
  if (processes.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        {vendor === 'nvidia'
          ? 'No process holds memory on this GPU.'
          : 'This vendor plugin does not list processes per GPU.'}
      </p>
    );
  }
  const rows = [...processes].sort(
    (a, b) => (b.used_memory_bytes ?? 0) - (a.used_memory_bytes ?? 0),
  );
  return (
    <div className={stale ? 'overflow-x-auto opacity-60' : 'overflow-x-auto'} title={stale ? 'Last known processes: the node is detached or has no fresh sample' : undefined}>
      {stale && <p className="pb-2 text-xs text-muted-foreground">Last known processes (stale: no fresh sample from the node).</p>}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>PID</TableHead>
            <TableHead>Process</TableHead>
            <TableHead className="text-right">VRAM</TableHead>
            <TableHead className="min-w-[140px]">Share of GPU</TableHead>
            <TableHead>Belongs to</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((p) => {
            const bytes = p.used_memory_bytes ?? null;
            const share =
              bytes !== null && total > 0 ? (bytes / total) * 100 : null;
            return (
              <TableRow key={p.pid}>
                <TableCell className="font-mono text-xs tabular-nums">
                  {p.pid}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {p.name ?? '—'}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {bytes !== null ? formatBytes(bytes) : 'n/a'}
                </TableCell>
                <TableCell>
                  {share !== null ? (
                    <>
                      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-foreground/50"
                          style={{ width: `${Math.min(100, share)}%` }}
                        />
                      </div>
                      <div className="mt-0.5 text-[10.5px] text-muted-foreground tabular-nums">
                        {share.toFixed(1)}%
                      </div>
                    </>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell>
                  {p.deployment_id ? (
                    <span className="inline-flex items-center gap-1.5">
                      <Link
                        href={`/deployments/${p.deployment_id}`}
                        className="text-xs font-medium hover:underline"
                      >
                        {p.deployment_name ?? p.deployment_id.slice(0, 8)}
                      </Link>
                      {p.origin === 'external' && <ExternalBadge compact />}
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      Not GPU Deck (used outside GPU Deck)
                    </span>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
