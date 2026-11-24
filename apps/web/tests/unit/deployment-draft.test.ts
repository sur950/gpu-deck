/**
 * Deployment wizard edit round trip: opening a deployment in the wizard and
 * saving without changes must not alter its settings (the PATCH sends the
 * whole spec).
 */
import { describe, expect, it } from 'vitest';
import { draftFromDeployment, toUpdate } from '@/components/models/wizard/draft';
import { DEPLOYMENTS } from '@/lib/mocks/fixtures/fleet';
import type { Deployment } from '@/types/models';

describe('deployment draft round trip', () => {
  it('keeps the exact slice size and every stored JSON key', () => {
    const d = {
      ...DEPLOYMENTS[0],
      slice_vram_bytes: 7_812_500_000,
      slice_compute_pct: 40,
      kv_sharing: true,
      kv_sharing_config: { backend: 'mooncake', remote_url: 'tcp://kv:5555' },
      traffic_config: { strategy: 'weighted', weights: { a: 3, b: 1 }, failover: false, max_retries: 3, health_eject: true },
      spec_decoding: { enabled: true, method: 'eagle', num_draft_tokens: 6, acceptance_threshold: 0.8 },
    } as unknown as Deployment;
    const body = toUpdate(draftFromDeployment(d));
    expect(body.slice_vram_bytes).toBe(7_812_500_000);
    expect(body.slice_compute_pct).toBe(40);
    expect(body.kv_sharing_config).toEqual({ backend: 'mooncake', remote_url: 'tcp://kv:5555' });
    expect(body.traffic_config).toEqual(d.traffic_config);
    expect(body.spec_decoding).toEqual({ ...d.spec_decoding, draft_model_version_id: null });
  });
});
