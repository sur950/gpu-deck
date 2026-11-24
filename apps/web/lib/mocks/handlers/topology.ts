/**
 * Topology, health, system info and branding mocks, plus read-only
 * `fallback` views of the fixture fleet (clusters / nodes / devices) so the
 * dashboards work on their own. The fleet feature's handler file owns those
 * resources and wins over the stand-ins.
 */

import { CLUSTER, DEVICES, DEVICE_SLICES, INSTANCES, NODES, deviceById, nodeById } from '@/lib/mocks/fixtures/fleet';
import { engineActiveSessions, engineWaitingSessions, gpuMemoryUsed, gpuPower, gpuTemperature, gpuUtilization } from '@/lib/mocks/fixtures/timeseries';
import { asFallback, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { Schemas } from '@/lib/mocks/types';

export function deviceWithLive(device: Schemas['Device'], now = Date.now()): Schemas['Device'] {
  return {
    ...device,
    live: {
      ts: new Date(now).toISOString(),
      utilization_pct: gpuUtilization(device.id, now),
      memory_used_bytes: gpuMemoryUsed(device.id, now),
      power_watts: gpuPower(device.id, now),
      temperature_c: gpuTemperature(device.id, now),
    },
  };
}

function topologyInstance(instance: Schemas['Instance'], now: number): Schemas['TopologyInstance'] {
  return {
    id: instance.id,
    deployment_id: instance.deployment_id,
    deployment_name: instance.deployment_name,
    model_name: instance.model_name,
    engine: instance.engine_name,
    state: instance.state,
    health: instance.health,
    device_ids: instance.device_ids,
    traffic_weight: instance.traffic_weight,
    active_sessions: instance.state === 'ready' ? engineActiveSessions(instance.id, now) : null,
    waiting_sessions: instance.state === 'ready' ? engineWaitingSessions(instance.id, now) : null,
  };
}

export function buildTopology(query: URLSearchParams, now = Date.now()): Schemas['Topology'] {
  const clusterId = query.get('cluster_id');
  const includeStopped = query.get('include_stopped') === 'true';
  const clusters = [CLUSTER].filter((c) => !clusterId || c.id === clusterId);
  return {
    generated_at: new Date(now).toISOString(),
    clusters: clusters.map((cluster) => ({
      cluster,
      nodes: NODES.filter((n) => n.cluster_id === cluster.id).map((node) => ({
        node,
        devices: DEVICES.filter((d) => d.node_id === node.id).map((device) => ({
          device: deviceWithLive(device, now),
          instances: INSTANCES.filter((i) => i.device_ids.includes(device.id) && (includeStopped || i.state !== 'stopped')).map((i) => topologyInstance(i, now)),
        })),
      })),
    })),
    links: [],
  };
}

const HEALTH: Schemas['Health'] = {
  status: 'healthy',
  version: '0.1.0+mock',
  timestamp: new Date().toISOString(),
  checks: {
    database: { status: 'healthy', response_time_ms: 2.1, error: null },
    redis: { status: 'healthy', response_time_ms: 0.4, error: null },
    celery: { status: 'healthy', response_time_ms: 11, error: null },
    agent_hub: { status: 'healthy', response_time_ms: 1.2, error: null },
    jobs_leader: { status: 'healthy', response_time_ms: null, error: null },
    api_layer: { status: 'healthy', response_time_ms: 3.8, error: null },
  },
};

registerMockHandlers('topology', [
  { method: 'GET', path: '/topology', handle: (req) => ok(buildTopology(req.query)) },
  { method: 'GET', path: '/health', handle: () => ok({ ...HEALTH, timestamp: new Date().toISOString() }) },
  { method: 'GET', path: '/health/ready', handle: () => ok({ ...HEALTH, timestamp: new Date().toISOString() }) },
  { method: 'GET', path: '/health/live', handle: () => ok({ status: 'ok' }) },
  {
    method: 'GET',
    path: '/system/info',
    handle: () =>
      ok({
        version: '0.1.0+mock',
        admin_url: 'http://localhost:8180',
        api_layer_url: 'http://localhost:8181',
        agent_hub_url: 'wss://localhost:8443',
        metrics_interval_seconds: 2,
        metrics_raw_retention_hours: 48,
        metrics_rollup_retention_days: 90,
        log_retention_days: 14,
        trace_retention_days: 7,
      }),
  },
  {
    method: 'GET',
    path: '/branding',
    handle: () => ok({ app_name: 'GPU Deck', app_byline: 'GPU control plane', brand_color: '#B3122E', logo_url: null, favicon_url: '/favicon.ico', default_color_mode: 'dark' }),
  },
  // Read-only stand-ins: the fleet feature (lib/mocks/handlers/fleet.ts) owns these.
  ...asFallback([
  { method: 'GET', path: '/clusters', handle: (req) => paginate(sortBy([CLUSTER], req.query.get('sort')), req.query) },
  { method: 'GET', path: '/clusters/:cluster_id', handle: (req) => (req.params.cluster_id === CLUSTER.id ? ok(CLUSTER) : notFound('Cluster')) },
  { method: 'GET', path: '/clusters/:cluster_id/nodes', handle: (req) => paginate(NODES.filter((n) => n.cluster_id === req.params.cluster_id), req.query) },
  {
    method: 'GET',
    path: '/nodes',
    handle: (req) => {
      const clusterId = req.query.get('cluster_id');
      const status = req.query.get('status');
      const list = NODES.filter((n) => (!clusterId || n.cluster_id === clusterId) && (!status || n.status === status));
      return paginate(sortBy(list, req.query.get('sort'), 'name'), req.query);
    },
  },
  { method: 'GET', path: '/nodes/:node_id', handle: (req) => (nodeById(req.params.node_id) ? ok(nodeById(req.params.node_id)) : notFound('Node')) },
  { method: 'GET', path: '/nodes/:node_id/devices', handle: (req) => paginate(DEVICES.filter((d) => d.node_id === req.params.node_id).map((d) => deviceWithLive(d)), req.query) },
  { method: 'GET', path: '/nodes/:node_id/instances', handle: (req) => paginate(INSTANCES.filter((i) => i.node_id === req.params.node_id), req.query) },
  {
    method: 'GET',
    path: '/devices',
    handle: (req) => {
      const nodeId = req.query.get('node_id');
      const clusterId = req.query.get('cluster_id');
      const vendor = req.query.get('vendor');
      const health = req.query.get('health');
      const list = DEVICES.filter(
        (d) => (!nodeId || d.node_id === nodeId) && (!clusterId || d.cluster_id === clusterId) && (!vendor || d.vendor === vendor) && (!health || d.health === health),
      ).map((d) => deviceWithLive(d));
      return paginate(list, req.query);
    },
  },
  { method: 'GET', path: '/devices/:device_id', handle: (req) => (deviceById(req.params.device_id) ? ok(deviceWithLive(deviceById(req.params.device_id) as Schemas['Device'])) : notFound('Device')) },
  { method: 'GET', path: '/devices/:device_id/slices', handle: (req) => paginate(DEVICE_SLICES.filter((s) => s.device_id === req.params.device_id), req.query) },
  { method: 'GET', path: '/device-slices', handle: (req) => paginate(DEVICE_SLICES.filter((s) => s.status !== 'released'), req.query) },
  ]),
]);
