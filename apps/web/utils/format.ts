/**
 * Formatting utilities for numbers, dates, currency, etc.
 */

/**
 * Format large numbers with K, M, B, T suffixes
 * @param value - Number to format
 * @param decimals - Number of decimal places (default: 1)
 * @returns Formatted string like "10K", "1.5M", "2.3B", "1.2T"
 */
export function formatNumber(value: number, decimals: number = 1): string {
  if (value < 10000) {
    return value.toLocaleString();
  }

  const units = [
    { value: 1e12, suffix: 'T' }, // Trillion
    { value: 1e9, suffix: 'B' },  // Billion
    { value: 1e6, suffix: 'M' },  // Million
    { value: 1e3, suffix: 'K' },  // Thousand
  ];

  for (const unit of units) {
    if (value >= unit.value) {
      const formatted = (value / unit.value).toFixed(decimals);
      // Remove trailing zeros after decimal point
      return formatted.replace(/\.0+$/, '') + unit.suffix;
    }
  }

  return value.toLocaleString();
}

/**
 * Format currency with proper symbols
 * @param value - Amount to format
 * @param currency - Currency code (default: USD)
 * @returns Formatted currency string like "$1,234.56"
 */
export function formatCurrency(value: number, currency: string = 'USD'): string {
  if (value > 0 && value < 0.01) {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 6,
    }).format(value);
  }
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

/**
 * Format currency with short form (K, M, B, T suffixes)
 * @param value - Amount to format
 * @param currency - Currency code (default: USD)
 * @param decimals - Number of decimal places (default: 1)
 * @returns Formatted currency string like "$10K", "$1.5M", "$2.3B"
 */
export function formatCurrencyShort(value: number, currency: string = 'USD', decimals: number = 1): string {
  const symbol = currency === 'USD' ? '$' : new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(0).replace('0', '');

  if (value < 1000) {
    const maxDecimals = value > 0 && value < 0.01 ? 6 : 2;
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: maxDecimals,
    }).format(value);
  }

  const units = [
    { value: 1e12, suffix: 'T' }, // Trillion
    { value: 1e9, suffix: 'B' },  // Billion
    { value: 1e6, suffix: 'M' },  // Million
    { value: 1e3, suffix: 'K' },  // Thousand
  ];

  for (const unit of units) {
    if (value >= unit.value) {
      const formatted = (value / unit.value).toFixed(decimals);
      // Remove trailing zeros after decimal point
      return symbol + formatted.replace(/\.0+$/, '') + unit.suffix;
    }
  }

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

/**
 * Format date to readable string
 * @param date - Date to format
 * @param format - Format style (default: 'short')
 * @returns Formatted date string
 */
export function formatDate(
  date: Date | string,
  format: 'short' | 'medium' | 'long' = 'short'
): string {
  const d = typeof date === 'string' ? new Date(date) : date;

  const optionsMap: Record<string, Intl.DateTimeFormatOptions> = {
    short: { month: 'short', day: 'numeric', year: 'numeric' },
    medium: { month: 'long', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' },
    long: {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    },
  };

  return new Intl.DateTimeFormat('en-US', optionsMap[format]).format(d);
}

/**
 * Format date with time in compact format
 * @param date - Date to format
 * @returns Formatted string like "Jan 15, 2024, 2:30 PM"
 */
export function formatDateTime(date: Date | string): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(d);
}

/**
 * Format relative time (e.g., "2 hours ago", "in 3 days")
 * @param date - Date to format
 * @returns Relative time string
 */
export function formatRelativeTime(date: Date | string | undefined | null): string {
  if (!date) return '—';
  const d = typeof date === 'string' ? new Date(date) : date;
  if (isNaN(d.getTime())) return '—';
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffSec < 60) return 'just now';
  if (diffMin < 60) return `${diffMin} minute${diffMin > 1 ? 's' : ''} ago`;
  if (diffHour < 24) return `${diffHour} hour${diffHour > 1 ? 's' : ''} ago`;
  if (diffDay < 30) return `${diffDay} day${diffDay > 1 ? 's' : ''} ago`;

  return formatDate(d, 'short');
}

/**
 * Memory and storage sizes. One convention everywhere (VRAM, RAM, disks, model files, slices,
 * charts, tooltips): binary math with IEC labels, 1 GiB = 2^30 bytes, as nvidia-smi (MiB),
 * the agent and the scheduler count. "141 GB" on a spec sheet is 131.6 GiB here, never "131.6 GB".
 *
 * B and KiB / MiB are whole numbers; GiB and TiB keep `decimals` (default 1).
 * @example formatBytes(150_754_820_096) → "140.4 GiB" · formatBytes(536_870_912) → "512 MiB"
 */
export function formatBytes(bytes: number | null | undefined, decimals: number = 1): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '—';
  const abs = Math.abs(bytes);
  if (abs < 1024) return `${Math.round(bytes)} B`;
  if (abs < 1024 ** 2) return `${Math.round(bytes / 1024)} KiB`;
  if (abs < 1024 ** 3) return `${Math.round(bytes / 1024 ** 2)} MiB`;
  if (abs < 1024 ** 4) return `${(bytes / 1024 ** 3).toFixed(decimals)} GiB`;
  return `${(bytes / 1024 ** 4).toFixed(decimals)} TiB`;
}

/** "31.4 / 140.4 GiB": both values in the larger value's unit (GiB, or TiB from 1 TiB). */
export function formatBytesPair(used: number | null | undefined, total: number | null | undefined, decimals: number = 1): string {
  if (used === null || used === undefined || !Number.isFinite(used) || !total) return '—';
  const [div, unit] = total >= 1024 ** 4 ? [1024 ** 4, 'TiB'] : [1024 ** 3, 'GiB'];
  return `${(used / div).toFixed(decimals)} / ${(total / div).toFixed(decimals)} ${unit}`;
}

/**
 * Format percentage
 * @param value - Decimal value (e.g., 0.87 for 87%)
 * @param decimals - Number of decimal places (default: 1)
 * @returns Formatted percentage string like "87.0%"
 */
export function formatPercentage(value: number, decimals: number = 1): string {
  return `${(value * 100).toFixed(decimals)}%`;
}

/**
 * Format duration in milliseconds to human readable string
 * @param ms - Duration in milliseconds
 * @returns Formatted string like "1.2s", "3.5m", "2.1h"
 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}m`;
  return `${(ms / 3600000).toFixed(1)}h`;
}
