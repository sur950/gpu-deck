'use client';

import { useEffect, useState } from 'react';
import { formatRelativeTime } from '@/utils/format';

interface RelativeTimeProps {
  value: string | null | undefined;
  className?: string;
  /** Re-render interval in ms (default 30 s) so "just now" ages. */
  tickMs?: number;
}

/** "5 minutes ago" that stays current, with the full timestamp on hover. */
export function RelativeTime({ value, className, tickMs = 30_000 }: RelativeTimeProps) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), tickMs);
    return () => clearInterval(id);
  }, [tickMs]);
  if (!value) return <span className={className}>—</span>;
  const date = new Date(value);
  return (
    <time dateTime={value} title={date.toLocaleString()} className={className}>
      {formatRelativeTime(date)}
    </time>
  );
}
