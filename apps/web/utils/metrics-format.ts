/**
 * Number formatting for GPU and inference metrics. Precise, unit-aware,
 * compact enough for tiles and axis ticks.
 */

import { formatBytes, formatBytesPair } from '@/utils/format';

const GiB = 1024 ** 3;
const MiB = 1024 ** 2;

/** Binary bytes as GPUs report them: "31.4 GiB", "512 MiB" (same as `formatBytes`). */
export function formatGiB(bytes: number | null | undefined, decimals = 1): string {
  return formatBytes(bytes, decimals);
}

/** Axis ticks: whole GiB ("35 GiB"), MiB below 1 GiB. */
export function formatGiBCompact(bytes: number): string {
  if (bytes === 0) return '0';
  if (Math.abs(bytes) >= GiB) return `${Math.round(bytes / GiB)} GiB`;
  return `${Math.round(bytes / MiB)} MiB`;
}

/** "31.4 / 45.0 GiB" (same as `formatBytesPair`). */
export function formatGiBPair(used: number | null | undefined, total: number | null | undefined): string {
  return formatBytesPair(used, total);
}

export function formatPct(value: number | null | undefined, decimals = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${value.toFixed(decimals)}%`;
}

export function formatWatts(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (value >= 1000) return `${(value / 1000).toFixed(2)} kW`;
  return `${value.toFixed(value < 10 ? 1 : 0)} W`;
}

export function formatTemp(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${value.toFixed(0)} °C`;
}

export function formatMhz(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${Math.round(value).toLocaleString()} MHz`;
}

/** Latency: "118 ms", "1.45 s", "2m 03s". */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (value < 1) return `${value.toFixed(2)} ms`;
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)} s`;
  const m = Math.floor(value / 60_000);
  const s = Math.round((value % 60_000) / 1000);
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

/** Tokens per second: "148 tok/s". */
export function formatTps(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${formatCompact(value, 1)} tok/s`;
}

/** 1,284 → "1.3K", 1,284,000 → "1.28M"; integers under 1000 stay whole. */
export function formatCompact(value: number | null | undefined, decimals = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 10_000) return `${(value / 1e3).toFixed(1)}K`;
  if (abs >= 1000) return `${(value / 1e3).toFixed(2)}K`;
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(abs < 10 ? decimals : 0);
}

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return value < 10_000 ? Math.round(value).toLocaleString() : formatCompact(value);
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return Math.round(value).toLocaleString();
}

export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(0)}%`;
}

export function formatUsd(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(value);
}

/** Pick a formatter from a metric unit string (contract `TimeSeries.unit`). */
export function formatterForUnit(unit: string): (value: number | null | undefined, compact?: boolean) => string {
  switch (unit) {
    case '%':
      return (v, compact) => formatPct(v, compact ? 0 : 1);
    case 'bytes':
      return (v, compact) => (compact && v !== null && v !== undefined ? formatGiBCompact(v) : formatGiB(v));
    case 'W':
      return (v, compact) => (compact && v !== null && v !== undefined ? `${Math.round(v)} W` : formatWatts(v));
    case '°C':
      return (v) => formatTemp(v);
    case 'MHz':
      return (v, compact) => (compact && v !== null && v !== undefined ? (v >= 1000 ? `${(v / 1000).toFixed(1)} GHz` : `${Math.round(v)} MHz`) : formatMhz(v));
    case 'ms':
      return (v, compact) =>
        compact && v !== null && v !== undefined ? (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v >= 10_000 ? 0 : 1)}s` : `${Math.round(v)}ms`) : formatMs(v);
    case 'tok/s':
      return (v, compact) => (compact ? formatCompact(v, 0) : formatTps(v));
    case 'tokens':
      return (v) => formatTokens(v);
    case 'ratio':
      return (v) => formatRatio(v);
    case 'USD':
      return (v, compact) => formatUsd(v, compact ? 0 : 2);
    case 'req/min':
      return (v, compact) => (compact ? formatCompact(v, 0) : `${formatCompact(v, 1)} req/min`);
    default:
      return (v) => formatCompact(v);
  }
}

/** Uptime / elapsed: "6h 12m", "3d 4h", "48s". */
export function formatElapsed(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return '—';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return '—';
  return id.length > length ? `${id.slice(0, length)}…` : id;
}
