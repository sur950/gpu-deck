'use client';

import { useEffect, useState } from 'react';
import { formatCountdown } from '@/components/automation/run-meta';

interface CountdownProps {
  /** Target instant (epoch ms); null renders the fallback. */
  at: number | null;
  fallback?: React.ReactNode;
  className?: string;
}

/** "in 12 s" that ticks every second, with the absolute time on hover. */
export function Countdown({ at, fallback = '—', className }: CountdownProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (at === null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [at]);
  if (at === null) return <span className={className}>{fallback}</span>;
  return (
    <time dateTime={new Date(at).toISOString()} title={new Date(at).toLocaleString()} className={className}>
      {formatCountdown(at - now)}
    </time>
  );
}
