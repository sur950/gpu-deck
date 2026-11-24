'use client';

import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/format';
import { heartbeatFreshness, type HeartbeatFreshness } from '@/types/fleet';

const DOT: Record<HeartbeatFreshness, string> = {
  live: 'bg-success',
  late: 'bg-warning',
  stale: 'bg-error',
  never: 'bg-muted-foreground/40',
};

const LABEL: Record<HeartbeatFreshness, string> = {
  live: 'Heartbeat is current',
  late: 'Heartbeat is late',
  stale: 'No heartbeat for over 90 s',
  never: 'Never connected',
};

/** Seconds-precision age for fresh heartbeats ("3s ago"), relative wording after a minute. */
function ageText(at: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - new Date(at).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  return formatRelativeTime(at);
}

interface HeartbeatProps {
  at: string | null | undefined;
  className?: string;
  /** Hide the text, keep the dot. */
  dotOnly?: boolean;
}

/**
 * Agent heartbeat freshness: a dot that pulses while the agent is live
 * (green < 20 s, amber < 90 s, red after) and an age that ticks every second.
 */
export function Heartbeat({ at, className, dotOnly = false }: HeartbeatProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const freshness = heartbeatFreshness(at, now);
  return (
    <span className={cn('inline-flex items-center gap-1.5 tabular-nums', className)} title={at ? `${LABEL[freshness]} · ${new Date(at).toLocaleString()}` : LABEL.never}>
      <span className="relative flex size-2 shrink-0">
        {freshness === 'live' && <span className={cn('absolute inline-flex size-full animate-ping rounded-full opacity-50', DOT.live)} />}
        <span className={cn('relative inline-flex size-2 rounded-full', DOT[freshness])} />
      </span>
      {!dotOnly && <span className={cn(freshness === 'live' ? 'text-muted-foreground' : freshness === 'never' ? 'text-muted-foreground' : 'text-foreground')}>{at ? ageText(at, now) : 'never'}</span>}
    </span>
  );
}
