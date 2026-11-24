import { describe, expect, it } from 'vitest';
import { formatBytes, formatBytesPair } from '@/utils/format';
import { formatGiB, formatGiBPair, formatterForUnit } from '@/utils/metrics-format';
import { deviceExtraRows, toggleText } from '@/components/fleet/device-properties';
import { isNodeConnected, nodeStateInput, nodeStateView } from '@/components/fleet/node-status';
import { activityLabel, effectiveActivity, nodeActivity } from '@/components/fleet/activity-badge';
// Static import: the panel module pulls in the deployment UI, and loading it inside the test
// counted its cold transform against the 5 s test timeout on a fresh checkout.
import { servedNames } from '@/components/models/deployment-panels';

const GiB = 1024 ** 3;

describe('memory units: binary math, IEC labels, one formatter', () => {
  it('formats like nvidia-smi / the agent count', () => {
    expect(formatBytes(150_754_820_096)).toBe('140.4 GiB'); // H200, was "140.4 GB"
    expect(formatBytes(235.9 * GiB)).toBe('235.9 GiB');
    expect(formatBytes(512 * 1024 ** 2)).toBe('512 MiB');
    expect(formatBytes(2048)).toBe('2 KiB');
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(2 * 1024 ** 4)).toBe('2.0 TiB');
    expect(formatBytes(null)).toBe('—');
    expect(formatBytes(141 * GiB, 0)).toBe('141 GiB');
  });

  it('never prints decimal SI labels', () => {
    for (const v of [900, 5e5, 7e8, 1.5e11, 3e12]) expect(formatBytes(v)).not.toMatch(/\b[KMGT]B\b/);
  });

  it('metrics formatters and chart ticks agree with formatBytes', () => {
    expect(formatGiB(150_754_820_096)).toBe(formatBytes(150_754_820_096));
    expect(formatGiBPair(120 * GiB, 150_754_820_096)).toBe('120.0 / 140.4 GiB');
    expect(formatBytesPair(512 * GiB, 1.5 * 1024 ** 4)).toBe('0.5 / 1.5 TiB');
    expect(formatterForUnit('bytes')(81.23 * GiB)).toBe('81.2 GiB');
  });
});

describe('device properties', () => {
  it('humanises NVML flags and enums', () => {
    const rows = deviceExtraRows({ mig_mode_current: 0, mig_mode_pending: 0, persistence_mode: 1, ecc_mode_current: 1, compute_mode: 3, pstate: 'p0' });
    const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.value]));
    expect(byLabel['MIG mode']).toBe('Disabled');
    expect(byLabel['MIG mode after reset']).toBeUndefined(); // same as current
    expect(byLabel['Persistence mode']).toBe('Enabled');
    expect(byLabel.ECC).toBe('Enabled');
    expect(byLabel['Compute mode']).toBe('Exclusive process');
    expect(byLabel['Performance state']).toBe('P0');
    expect(rows.some((r) => /mig mode current/i.test(r.label))).toBe(false);
  });

  it('shows a pending MIG change and readable fallbacks', () => {
    const rows = deviceExtraRows({ mig_mode_current: 0, mig_mode_pending: 1, unified_memory_bytes: 64 * GiB, usable_memory_fraction: 0.818, some_new_flag: 'x' });
    const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.value]));
    expect(byLabel['MIG mode after reset']).toBe('Enabled');
    expect(byLabel['Unified memory']).toBe('64.0 GiB');
    expect(byLabel['Usable for models']).toBe('81.8%');
    expect(byLabel['Some new flag']).toBe('x');
    expect(toggleText('Disabled')).toBe('Disabled');
    expect(toggleText(true)).toBe('Enabled');
  });
});

describe('node state', () => {
  const beat = new Date().toISOString();
  it('connected + drained is neutral "Drained" and counts as connected', () => {
    const v = nodeStateView({ status: 'drained', detached: false, lastHeartbeatAt: beat });
    expect(v.label).toBe('Drained');
    expect(v.variant).toBe('inactive');
    expect(v.connected).toBe(true);
    expect(isNodeConnected({ status: 'drained', detached: false })).toBe(true);
  });

  it('disconnected + drained is red "Detached"', () => {
    const v = nodeStateView({ status: 'drained', detached: true, lastHeartbeatAt: beat });
    expect(v.label).toBe('Detached · drained');
    expect(v.variant).toBe('error');
    expect(v.connected).toBe(false);
  });

  it('offline with a recorded drain (backend: disconnected drained node) reads "Detached · drained"', () => {
    const v = nodeStateView(nodeStateInput({ status: 'offline', detached: true, drained_at: new Date().toISOString() }));
    expect(v.label).toBe('Detached · drained');
    expect(v.variant).toBe('error');
  });

  it('offline is detached even without the flag; online is green; removed is not detached', () => {
    expect(nodeStateView({ status: 'offline' }).label).toBe('Detached');
    expect(nodeStateView({ status: 'online', detached: false }).variant).toBe('online');
    expect(nodeStateView({ status: 'removed', detached: true }).label).toBe('Removed');
    expect(isNodeConnected({ status: 'pending' })).toBe(false);
  });
});

describe('activity', () => {
  it('never idle while memory is held outside GPU Deck', () => {
    expect(effectiveActivity('idle', { usedBytes: 120 * GiB, reservedBytes: 0 })).toBe('external');
    expect(effectiveActivity('idle', { usedBytes: 0.4 * GiB, reservedBytes: 0 })).toBe('idle');
    expect(effectiveActivity('idle', { usedBytes: 30 * GiB, reservedBytes: 32 * GiB })).toBe('idle');
    expect(effectiveActivity('idle', { usedBytes: 40 * GiB, unified: true })).toBe('idle');
    expect(effectiveActivity('detached', { usedBytes: 120 * GiB })).toBe('detached');
    expect(effectiveActivity('gpu_deck', { usedBytes: 120 * GiB })).toBe('gpu_deck');
  });

  it('labels external work as "Running (external)"', () => {
    expect(activityLabel('external', 3)).toBe('Running (external) · 3 processes');
    expect(activityLabel('external', 0, null, 120 * GiB)).toBe('Running (external) · 120.0 GiB held');
    expect(activityLabel('external', 1)).toBe('Running (external) · 1 process');
  });

  it('rolls device memory up to the node', () => {
    const held = { vram_used_bytes: 118 * GiB, vram_reserved_bytes: 0 };
    const quiet = { vram_used_bytes: 0.2 * GiB, vram_reserved_bytes: 0 };
    expect(nodeActivity('idle', [quiet, held])).toBe('external');
    expect(nodeActivity('idle', [quiet])).toBe('idle');
    expect(nodeActivity('detached', [held])).toBe('detached');
    expect(nodeActivity('idle', [{ ...held, vram_usage_stale: true }])).toBe('idle');
  });
});

describe('served names per deployment', () => {
  it('deployment name pins one deployment, model name balances', () => {
    expect(servedNames({ name: 'qwen3-0.6b-trtllm', model_name: 'qwen3-0.6b-hf' })).toEqual([
      { name: 'qwen3-0.6b-trtllm', scope: 'deployment', hint: 'This deployment only' },
      { name: 'qwen3-0.6b-hf', scope: 'model', hint: 'Balanced across every deployment of this model' },
    ]);
    expect(servedNames({ name: 'Qwen3-8B', model_name: 'qwen3-8b' }).map((n) => n.scope)).toEqual(['model']);
    expect(servedNames({ name: 'solo', model_name: undefined })).toHaveLength(1);
  });
});
