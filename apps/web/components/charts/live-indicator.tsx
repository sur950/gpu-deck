'use client';

import { cn } from '@/lib/utils';
import type { LiveStatus } from '@/hooks/use-live-events';

const STATUS: Record<LiveStatus, { label: string; dot: string; ping: boolean }> = {
  idle: { label: 'Paused', dot: 'bg-muted-foreground/50', ping: false },
  connecting: { label: 'Connecting', dot: 'bg-warning', ping: false },
  open: { label: 'Live', dot: 'bg-success', ping: true },
  reconnecting: { label: 'Reconnecting', dot: 'bg-warning', ping: true },
  closed: { label: 'Disconnected', dot: 'bg-error', ping: false },
};

interface LiveIndicatorProps {
  status: LiveStatus;
  /** e.g. "2 s" — shown after the label when live. */
  detail?: string;
  className?: string;
}

/** Stream state chip: pulsing green when the SSE feed is open. */
export function LiveIndicator({ status, detail, className }: LiveIndicatorProps) {
  const cfg = STATUS[status];
  return (
    <span
      role="status"
      className={cn('inline-flex h-9 items-center gap-2 rounded-full border border-border bg-background/60 px-3 text-[11px] font-medium text-muted-foreground', className)}
    >
      <span className="relative flex size-2">
        {cfg.ping && <span className={cn('absolute inline-flex size-full animate-ping rounded-full opacity-60', cfg.dot)} />}
        <span className={cn('relative inline-flex size-2 rounded-full', cfg.dot)} />
      </span>
      {cfg.label}
      {status === 'open' && detail && <span className="tabular-nums text-muted-foreground/70">· {detail}</span>}
    </span>
  );
}
