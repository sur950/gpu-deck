/**
 * Observed (external) deployments and per-process GPU memory: read-only guards in the mock API
 * (as the control plane answers 409 EXTERNAL_DEPLOYMENT), the GPU memory map counting memory
 * used outside GPU Deck, and the processes table.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { matchMockHandler } from '@/lib/mocks/registry';
import '@/lib/mocks/handlers';
import type { MockRequest } from '@/lib/mocks/types';
import { freeVram } from '@/components/fleet/slice-dialogs';
import { outsideBytes } from '@/components/fleet/slice-bar';
import { GpuProcessesTable } from '@/components/fleet/gpu-processes-table';
import { ExternalBadge } from '@/components/models/external-badge';
import { IDS } from '@/lib/mocks/fixtures/fleet';
import { isExternal, type Deployment } from '@/types/models';
import type { Device } from '@/types/fleet';

const GB = 1_000_000_000;

function call(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  body: unknown = {},
) {
  const url = new URL(`http://api.test/api/v1${path}`);
  const route = url.pathname.replace('/api/v1', '');
  const match = matchMockHandler(method, route);
  if (!match) throw new Error(`no mock for ${method} ${route}`);
  const req: MockRequest = {
    method,
    path: route,
    params: match.params,
    query: url.searchParams,
    body,
    url,
  };
  return match.handler.handle(req) as {
    status?: number;
    data?: unknown;
    error?: { code: string };
  };
}

describe('external deployments in the mock API', () => {
  it('lists them, filters by origin and refuses every mutation with 409', () => {
    const all = call('GET', '/deployments?page_size=100') as {
      data: Deployment[];
    };
    const ext = all.data.find((d) => d.id === IDS.deployments.extVllm);
    expect(ext && isExternal(ext)).toBe(true);
    expect(ext?.model_id).toBeNull();
    const external = call('GET', '/deployments?origin=external') as {
      data: Deployment[];
    };
    expect(external.data.map((d) => d.id)).toEqual([IDS.deployments.extVllm]);
    const managed = call('GET', '/deployments?origin=managed') as {
      data: Deployment[];
    };
    expect(managed.data.some((d) => d.id === IDS.deployments.extVllm)).toBe(
      false,
    );

    for (const [method, path, body] of [
      [
        'POST',
        `/deployments/${IDS.deployments.extVllm}/scale`,
        { replicas: 2 },
      ],
      ['POST', `/deployments/${IDS.deployments.extVllm}/stop`, {}],
      ['POST', `/deployments/${IDS.deployments.extVllm}/restart`, {}],
      [
        'PATCH',
        `/deployments/${IDS.deployments.extVllm}`,
        { description: 'x' },
      ],
      ['DELETE', `/deployments/${IDS.deployments.extVllm}`, {}],
      ['POST', `/instances/${IDS.instances.extVllm}/drain`, {}],
      ['DELETE', `/instances/${IDS.instances.extVllm}`, {}],
      [
        'POST',
        '/benchmarks',
        { name: 'b', deployment_id: IDS.deployments.extVllm },
      ],
    ] as const) {
      const res = call(method, path, body);
      expect(res.status, `${method} ${path}`).toBe(409);
      expect(res.error?.code).toBe('EXTERNAL_DEPLOYMENT');
    }
  });

  it('shows processes and the real free VRAM on the GPU it uses', () => {
    const res = call('GET', `/devices/${IDS.devices.l40s1}`) as {
      data: Device;
    };
    const d = res.data;
    const ext = d.processes?.find((p) => p.origin === 'external');
    expect(ext?.deployment_id).toBe(IDS.deployments.extVllm);
    expect(d.processes?.some((p) => p.origin === null)).toBe(true);
    expect(d.vram_used_outside_bytes).toBeGreaterThan(18 * 1024 ** 3);
    const naive = d.vram_total_bytes * 0.9 - (d.vram_reserved_bytes ?? 0);
    expect(d.vram_free_bytes).toBeLessThan(naive);
  });
});

describe('GPU memory accounting in the UI', () => {
  const device = (over: Partial<Device>): Device =>
    ({
      id: 'd',
      node_id: 'n',
      vendor: 'nvidia',
      index: 0,
      name: 'NVIDIA H200',
      vram_total_bytes: 140 * GB,
      health: 'healthy',
      is_unified_memory: false,
      slices: [],
      ...over,
    }) as Device;

  it('uses the control plane free figure (headroom, reservations and outside use)', () => {
    // 120 GB used by an external server: 140 - 14 - 120 = 6 GB, not the 126 GB a reservation-only view shows.
    expect(freeVram(device({ vram_free_bytes: 6 * GB }))).toBe(6 * GB);
    expect(freeVram(device({}))).toBe(126 * GB);
  });

  it('draws memory used outside GPU Deck from the process list, else estimates it', () => {
    expect(
      outsideBytes(
        { vram_used_outside_bytes: 120 * GB, vram_used_bytes: 121 * GB },
        0,
      ),
    ).toEqual({ bytes: 120 * GB, estimated: false });
    expect(
      outsideBytes(
        { vram_used_outside_bytes: null, vram_used_bytes: 50 * GB },
        20 * GB,
      ),
    ).toEqual({ bytes: 30 * GB, estimated: true });
  });

  it('lists GPU processes with their owner', () => {
    render(
      <GpuProcessesTable
        total={140 * GB}
        vendor="nvidia"
        processes={[
          {
            pid: 31337,
            name: 'VLLM::EngineCor',
            used_memory_bytes: 119 * GB,
            instance_id: 'i',
            deployment_id: 'dep',
            deployment_name: 'main@droplet',
            origin: 'external',
          },
          {
            pid: 4100,
            name: 'python3',
            used_memory_bytes: 0.5 * GB,
            instance_id: null,
            deployment_id: null,
            deployment_name: null,
            origin: null,
          },
        ]}
      />,
    );
    expect(screen.getByText('main@droplet')).toBeTruthy();
    expect(screen.getByText('External')).toBeTruthy();
    expect(screen.getByText(/Not GPU Deck/)).toBeTruthy();
    expect(screen.getByText('31337')).toBeTruthy();
  });

  it('renders the External badge', () => {
    render(<ExternalBadge />);
    expect(
      screen.getByText('External').closest('span')?.getAttribute('title'),
    ).toMatch(/never starts, stops/);
  });
});

describe('GPU / node activity labels', () => {
  it('never says Idle while processes outside GPU Deck run, and is red when detached', async () => {
    const { activityLabel, ActivityBadge } = await import('@/components/fleet/activity-badge');
    expect(activityLabel('external', 4)).toBe('Running (external) · 4 processes');
    expect(activityLabel('mixed', 1)).toBe('Running · 1 external process');
    expect(activityLabel('external', 0)).toBe('Running (external)');
    expect(activityLabel('idle')).toBe('Idle');
    expect(activityLabel('detached', 0, new Date(Date.now() - 5 * 60_000).toISOString())).toMatch(/^Detached · last seen/);
    const { container } = render(<ActivityBadge activity="detached" />);
    expect(container.textContent).toBe('Detached');
    expect(container.firstElementChild?.className).toMatch(/error/);
  });

  it('mock API: the busy GPU and its node report external activity', () => {
    const res = call('GET', `/devices/${IDS.devices.l40s1}`) as { data: Device };
    expect(['mixed', 'external']).toContain(res.data.activity);
    expect(res.data.outside_processes).toBeGreaterThanOrEqual(2);
  });
});
