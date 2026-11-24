/**
 * Where an engine instance listens on its node right now. Engines bind to loopback on a port the
 * agent picks when it starts them, so the port can change after an agent or engine restart;
 * tools on the node should read it here (or call the API layer, which always follows it).
 */

import { CopyButton } from '@/components/shared/copy-button';
import { cn } from '@/lib/utils';

const HINT = 'Engine port on the node (loopback). The agent picks a free port each time it starts the engine, so it can change after an agent or engine restart; the API layer always follows the current one.';

export function engineAddress(port: number | null | undefined): string | null {
  return port ? `127.0.0.1:${port}` : null;
}

interface EngineEndpointProps {
  port: number | null | undefined;
  nodeName?: string | null;
  /** Inline "engine :10241" for table sub-lines; otherwise the full address with a copy button. */
  compact?: boolean;
  className?: string;
}

export function EngineEndpoint({ port, nodeName, compact = false, className }: EngineEndpointProps) {
  const address = engineAddress(port);
  const title = `${HINT}${address && nodeName ? ` Current: http://${address} on ${nodeName}.` : ''}`;
  if (compact) {
    return (
      <span className={cn('font-mono', className)} title={title}>
        {port ? `engine :${port}` : 'no port yet'}
      </span>
    );
  }
  if (!address) return <span className={cn('text-muted-foreground', className)}>not listening yet</span>;
  return (
    <span className={cn('inline-flex items-center gap-1', className)} title={title}>
      <code className="font-mono text-[11px]">http://{address}</code>
      {nodeName && <span className="text-[11px] text-muted-foreground">on {nodeName}</span>}
      <CopyButton value={`http://${address}`} successText="Engine address copied" />
    </span>
  );
}
