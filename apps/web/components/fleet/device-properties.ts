/**
 * Vendor-specific device properties (`Device.extra`, straight from the agent's device plugin)
 * turned into readable rows: NVML enums and 0/1 flags become words ("MIG mode: Disabled"),
 * byte counts use the shared memory formatter, unknown keys get a sentence-case label.
 */

import { formatBytes } from '@/utils/format';

export interface DevicePropertyRow {
  label: string;
  value: string;
  mono?: boolean;
}

/** NVML `nvmlComputeMode_t`. */
const COMPUTE_MODES: Record<string, string> = {
  '0': 'Default (shared)',
  '1': 'Exclusive thread',
  '2': 'Prohibited',
  '3': 'Exclusive process',
  default: 'Default (shared)',
  exclusive_thread: 'Exclusive thread',
  prohibited: 'Prohibited',
  exclusive_process: 'Exclusive process',
};

/** Known keys: display label and how to read the value. */
const KNOWN: Record<string, { label: string; kind: 'toggle' | 'compute_mode' | 'bytes' | 'fraction' | 'pct' | 'upper' | 'text' }> = {
  mig_mode_current: { label: 'MIG mode', kind: 'toggle' },
  mig_mode: { label: 'MIG mode', kind: 'toggle' },
  mig: { label: 'MIG mode', kind: 'toggle' },
  mig_mode_pending: { label: 'MIG mode after reset', kind: 'toggle' },
  persistence_mode: { label: 'Persistence mode', kind: 'toggle' },
  persistence: { label: 'Persistence mode', kind: 'toggle' },
  ecc: { label: 'ECC', kind: 'toggle' },
  ecc_mode: { label: 'ECC', kind: 'toggle' },
  ecc_mode_current: { label: 'ECC', kind: 'toggle' },
  ecc_mode_pending: { label: 'ECC after reboot', kind: 'toggle' },
  compute_mode: { label: 'Compute mode', kind: 'compute_mode' },
  pstate: { label: 'Performance state', kind: 'upper' },
  performance_state: { label: 'Performance state', kind: 'upper' },
  vram_type: { label: 'Memory type', kind: 'upper' },
  compute_partition: { label: 'Compute partition', kind: 'upper' },
  memory_partition: { label: 'Memory partition', kind: 'upper' },
  unified_memory_bytes: { label: 'Unified memory', kind: 'bytes' },
  alloc_system_memory_bytes: { label: 'GPU-allocated memory', kind: 'bytes' },
  usable_memory_fraction: { label: 'Usable for models', kind: 'fraction' },
  renderer_utilization_pct: { label: 'Renderer utilisation', kind: 'pct' },
  tiler_utilization_pct: { label: 'Tiler utilisation', kind: 'pct' },
  wired_limit_dynamic: { label: 'Dynamic wired limit', kind: 'toggle' },
};

/** 0/1, booleans and the usual strings → "Enabled" / "Disabled"; anything else unchanged. */
export function toggleText(value: unknown): string {
  if (value === true || value === 1 || value === '1') return 'Enabled';
  if (value === false || value === 0 || value === '0') return 'Disabled';
  if (typeof value === 'string') {
    const v = value.trim().toLowerCase();
    if (['enabled', 'enable', 'on', 'true', 'yes'].includes(v)) return 'Enabled';
    if (['disabled', 'disable', 'off', 'false', 'no'].includes(v)) return 'Disabled';
    if (v === 'n/a' || v === 'not supported' || v === 'unsupported') return 'Not supported';
  }
  return valueText(value);
}

function valueText(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number' || typeof value === 'string') return String(value);
  if (Array.isArray(value)) return value.length === 0 ? 'None' : `${value.length} item${value.length === 1 ? '' : 's'}`;
  return JSON.stringify(value);
}

/** "mig_devices" → "Mig devices"; acronyms kept upper case. */
export function propertyLabel(key: string): string {
  const words = key.split(/[_\s]+/).filter(Boolean);
  const text = words.map((w) => (['mig', 'ecc', 'pci', 'pcie', 'numa', 'gpu', 'vram', 'nvml', 'rocm', 'cuda'].includes(w.toLowerCase()) ? w.toUpperCase() : w.toLowerCase())).join(' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Rows for a device's `extra` map, in a stable order (known keys first). */
export function deviceExtraRows(extra: Record<string, unknown> | null | undefined): DevicePropertyRow[] {
  const entries = Object.entries(extra ?? {});
  const rows: (DevicePropertyRow & { order: number })[] = [];
  const knownOrder = Object.keys(KNOWN);
  for (const [key, value] of entries) {
    if (key === 'mig_devices') {
      const n = Array.isArray(value) ? value.length : 0;
      rows.push({ label: 'MIG instances', value: n ? String(n) : 'None', order: 1 });
      continue;
    }
    if (key === 'xids') continue;
    const known = KNOWN[key];
    if (!known) {
      const bytes = key.endsWith('_bytes') && typeof value === 'number';
      rows.push({ label: propertyLabel(key.replace(/_bytes$/, '')), value: bytes ? formatBytes(value as number) : valueText(value), mono: !bytes && typeof value === 'string', order: 1000 });
      continue;
    }
    let text: string;
    switch (known.kind) {
      case 'toggle':
        text = toggleText(value);
        break;
      case 'compute_mode':
        text = COMPUTE_MODES[String(value).toLowerCase()] ?? valueText(value);
        break;
      case 'bytes':
        text = typeof value === 'number' ? formatBytes(value) : valueText(value);
        break;
      case 'fraction':
        text = typeof value === 'number' ? `${Math.round(value * 1000) / 10}%` : valueText(value);
        break;
      case 'pct':
        text = typeof value === 'number' ? `${Math.round(value)}%` : valueText(value);
        break;
      case 'upper':
        text = typeof value === 'string' ? value.toUpperCase() : valueText(value);
        break;
      default:
        text = valueText(value);
    }
    rows.push({ label: known.label, value: text, order: knownOrder.indexOf(key) });
  }
  // A pending MIG / ECC mode equal to the current one says nothing.
  const dedup = rows.filter((r, i) => {
    if (r.label === 'MIG mode after reset') return rows.find((x) => x.label === 'MIG mode')?.value !== r.value;
    if (r.label === 'ECC after reboot') return rows.find((x) => x.label === 'ECC')?.value !== r.value;
    return rows.findIndex((x) => x.label === r.label) === i;
  });
  return dedup.sort((a, b) => a.order - b.order).map((r) => ({ label: r.label, value: r.value, mono: r.mono }));
}
