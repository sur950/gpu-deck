'use client';

import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

function mmss(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

/** Wall clock that re-renders every `intervalMs` (keeps render pure). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Remaining milliseconds until `expiresAt`, ticking every second. */
export function useRemaining(expiresAt: string | null | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return expiresAt ? Date.parse(expiresAt) - now : 0;
}

interface ExpiryCountdownProps {
  createdAt: string;
  expiresAt: string;
  /** Diameter in px of the ring variant. */
  size?: number;
  variant?: 'ring' | 'inline';
  className?: string;
}

/**
 * Time left on a one-time join token. The ring drains as the TTL runs out and
 * turns amber in the last quarter, red once expired.
 */
export function ExpiryCountdown({ createdAt, expiresAt, size = 112, variant = 'ring', className }: ExpiryCountdownProps) {
  const remaining = useRemaining(expiresAt);
  const ttl = Math.max(1, Date.parse(expiresAt) - Date.parse(createdAt));
  const fraction = Math.max(0, Math.min(1, remaining / ttl));
  const expired = remaining <= 0;
  const low = !expired && fraction < 0.25;
  const tone = expired ? 'text-error' : low ? 'text-warning' : 'text-foreground';

  if (variant === 'inline') {
    return (
      <span className={cn('inline-flex items-center gap-1.5 font-mono text-xs tabular-nums', tone, className)} title={`Expires ${new Date(expiresAt).toLocaleString()}`}>
        <span className={cn('size-1.5 rounded-full', expired ? 'bg-error' : low ? 'bg-warning animate-pulse' : 'bg-success')} aria-hidden />
        {expired ? 'expired' : mmss(remaining)}
      </span>
    );
  }

  const stroke = 6;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className={cn('relative shrink-0', className)} style={{ width: size, height: size }} role="timer" aria-live="off" aria-label={expired ? 'Token expired' : `Token expires in ${mmss(remaining)}`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--muted))" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={expired ? 'rgb(var(--error-rgb, 229 50 31))' : low ? 'rgb(var(--warning-rgb, 201 138 23))' : 'rgb(var(--foreground))'}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - fraction)}
          className="transition-[stroke-dashoffset] duration-1000 ease-linear"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={cn('font-mono text-xl font-semibold tabular-nums leading-none', tone)}>{expired ? '0:00' : mmss(remaining)}</span>
        <span className="mt-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{expired ? 'expired' : 'left'}</span>
      </div>
    </div>
  );
}
