'use client';

/**
 * Observability › GPUs / infra: network distance of every node: the nearest API layer's
 * round trip over the serving path and the control plane's, plus the API layers reporting.
 */

import Link from 'next/link';
import { Network } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { EmptyState } from '@/components/shared/EmptyState';
import { RttValue, formatRtt } from '@/components/observability/latency';
import { useLiveLatency } from '@/hooks/use-latency';

export function FleetLatencyCard({ nodeId }: { nodeId?: string }) {
  const live = useLiveLatency(nodeId);
  const nodes = live.data?.nodes ?? [];
  const layers = live.data?.apiLayers ?? [];
  return (
    <Card className="gap-0 rounded-xl py-0 shadow-premium">
      <CardHeader className="border-b border-border px-5 py-4">
        <CardTitle className="text-sm font-semibold">Network</CardTitle>
        <CardDescription className="text-xs">
          Round trip from the API layers (over the path they serve each node by) and from the control plane.{' '}
          {layers.length > 0 && (
            <span>
              Reporting: {layers.map((l) => `${l.id}${l.region ? ` (${l.region})` : ''}`).join(', ')}.
            </span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0 py-0">
        {nodes.length === 0 ? (
          <div className="px-5 py-4">
            <EmptyState icon={Network} title="No round trips measured" description="API layers probe every connected node every few seconds; values show here within a minute." />
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Node</TableHead>
                <TableHead>Nearest API layer</TableHead>
                <TableHead className="text-right">Serving RTT</TableHead>
                <TableHead className="text-right">Jitter</TableHead>
                <TableHead className="pr-5 text-right">Control plane</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {nodes.map((n) => {
                const nearest = n.apiLayers[0];
                return (
                  <TableRow key={n.nodeId}>
                    <TableCell className="pl-5 text-xs">
                      <Link href={`/nodes/${n.nodeId}?tab=network`} className="font-mono font-semibold hover:underline">
                        {n.nodeName ?? n.nodeId.slice(0, 8)}
                      </Link>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {nearest ? `${nearest.apiLayerId}${nearest.region ? ` · ${nearest.region}` : ''}${nearest.mode ? ` · ${nearest.mode}` : ''}` : '—'}
                    </TableCell>
                    <TableCell className="text-right text-xs">
                      <RttValue ms={n.bestRttMs} />
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">{formatRtt(nearest?.jitterMs)}</TableCell>
                    <TableCell className="pr-5 text-right font-mono text-xs tabular-nums">{formatRtt(n.hub?.rttMs)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
