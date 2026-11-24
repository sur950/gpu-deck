/**
 * Observability mocks: GPU / inference / session metrics, live sessions,
 * logs, traces, metric catalog, and read-only instance / deployment lists
 * (overridden by the deployments feature's own handler file).
 */

import { DEPLOYMENTS, DEVICES, INSTANCES, instanceById } from '@/lib/mocks/fixtures/fleet';
import { listRootSpans, traceById } from '@/lib/mocks/fixtures/traces';
import { logChunksBetween, queryLogs } from '@/lib/mocks/fixtures/logs';
import { sessionSeries, sessionsSnapshot } from '@/lib/mocks/fixtures/sessions';
import {
  AVERAGED_ENGINE_METRICS,
  AVERAGED_GPU_METRICS,
  DEFAULT_ENGINE_METRICS,
  DEFAULT_GPU_METRICS,
  ENGINE_METRIC_UNITS,
  GPU_METRIC_UNITS,
  USAGE_METRIC_UNITS,
  aggregateSeries,
  buildSeries,
  engineMetricValue,
  engineSample,
  gpuMetricValue,
  parseRange,
  usageMetricValue,
} from '@/lib/mocks/fixtures/timeseries';
import { asFallback, notFound, ok, paginate, registerMockHandlers } from '@/lib/mocks/registry';
import type { Schemas } from '@/lib/mocks/types';

function metricsParam(query: URLSearchParams, fallback: string[]): string[] {
  const all = query.getAll('metrics').flatMap((m) => m.split(','));
  const list = all.map((m) => m.trim()).filter(Boolean);
  return list.length > 0 ? list : fallback;
}

function instanceWithLive(instance: Schemas['Instance'], now = Date.now()): Schemas['Instance'] {
  if (instance.state !== 'ready') return instance;
  const s = engineSample(instance.id, now);
  return {
    ...instance,
    live: {
      ts: s.ts as string,
      active_sessions: s.active_sessions as number,
      waiting_sessions: s.waiting_sessions as number,
      kv_cache_usage_pct: s.kv_cache_usage_pct as number,
      generation_tps: s.generation_tps as number,
      ttft_ms_p95: s.ttft_ms_p95 as number,
      requests_per_minute: engineMetricValue('requests_per_minute', instance.id, now, 60),
      error_rate_pct: engineMetricValue('error_rate_pct', instance.id, now, 60),
    },
  };
}

function deploymentWithLive(deployment: Schemas['Deployment'], now = Date.now()): Schemas['Deployment'] {
  const instances = deployment.instances?.map((i) => instanceWithLive(i, now)) ?? [];
  const ready = instances.filter((i) => i.state === 'ready');
  const sum = (k: keyof Schemas['InstanceLive']) => ready.reduce((s, i) => s + Number(i.live?.[k] ?? 0), 0);
  const avg = (k: keyof Schemas['InstanceLive']) => (ready.length ? sum(k) / ready.length : null);
  return {
    ...deployment,
    instances,
    live: {
      ts: new Date(now).toISOString(),
      active_sessions: sum('active_sessions'),
      waiting_sessions: sum('waiting_sessions'),
      kv_cache_usage_pct: avg('kv_cache_usage_pct'),
      generation_tps: sum('generation_tps'),
      ttft_ms_p95: ready.length ? Math.max(...ready.map((i) => Number(i.live?.ttft_ms_p95 ?? 0))) : null,
      requests_per_minute: sum('requests_per_minute'),
      error_rate_pct: avg('error_rate_pct'),
    },
  };
}

export { instanceWithLive, deploymentWithLive };

registerMockHandlers('observability', [
  // ── GPU metrics ───────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/metrics/gpu',
    handle: (req) => {
      const range = parseRange(req.query);
      const metrics = metricsParam(req.query, DEFAULT_GPU_METRICS);
      const nodeId = req.query.get('node_id');
      const clusterId = req.query.get('cluster_id');
      const deviceIds = req.query.getAll('device_id').flatMap((d) => d.split(',')).filter(Boolean);
      const aggregate = req.query.get('aggregate') ?? 'none';
      const devices = DEVICES.filter(
        (d) => (!nodeId || d.node_id === nodeId) && (!clusterId || d.cluster_id === clusterId) && (deviceIds.length === 0 || deviceIds.includes(d.id)),
      );
      const series: Schemas['TimeSeries'][] = [];
      for (const metric of metrics) {
        const unit = GPU_METRIC_UNITS[metric] ?? '';
        if (aggregate === 'none') {
          for (const d of devices) {
            series.push(
              buildSeries(metric, unit, { device_id: d.id, node_id: d.node_id, device_name: d.name, index: String(d.index), vendor: d.vendor }, range, (t) =>
                gpuMetricValue(metric, d.id, t, range.stepSeconds),
              ),
            );
          }
        } else {
          const mode = aggregate === 'sum' || aggregate === 'max' ? aggregate : AVERAGED_GPU_METRICS.has(metric) ? 'avg' : 'sum';
          series.push(aggregateSeries(metric, unit, { aggregate: mode, devices: String(devices.length) }, range, devices.map((d) => d.id), (id, t) => gpuMetricValue(metric, id, t, range.stepSeconds), mode));
        }
      }
      const out: Schemas['TimeSeriesResponse'] = { from: range.from.toISOString(), to: range.to.toISOString(), step: `${range.stepSeconds}s`, series };
      return ok(out);
    },
  },

  // ── Inference metrics ─────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/metrics/inference',
    handle: (req) => {
      const range = parseRange(req.query);
      const metrics = metricsParam(req.query, DEFAULT_ENGINE_METRICS);
      const modelId = req.query.get('model_id');
      const deploymentId = req.query.get('deployment_id');
      const nodeId = req.query.get('node_id');
      const instanceIds = req.query.getAll('instance_id').flatMap((d) => d.split(',')).filter(Boolean);
      const aggregate = req.query.get('aggregate') ?? 'none';
      const deploymentForModel = modelId ? DEPLOYMENTS.find((d) => d.model_id === modelId)?.id : null;
      const instances = INSTANCES.filter(
        (i) =>
          i.state === 'ready' &&
          (!deploymentId || i.deployment_id === deploymentId) &&
          (!deploymentForModel || i.deployment_id === deploymentForModel) &&
          (!nodeId || i.node_id === nodeId) &&
          (instanceIds.length === 0 || instanceIds.includes(i.id)),
      );
      const series: Schemas['TimeSeries'][] = [];
      for (const metric of metrics) {
        const unit = ENGINE_METRIC_UNITS[metric] ?? '';
        if (aggregate === 'none') {
          for (const i of instances) {
            series.push(
              buildSeries(metric, unit, { instance_id: i.id, deployment_id: i.deployment_id, deployment: i.deployment_name ?? '', node_id: i.node_id, node: i.node_name ?? '', engine: i.engine_name ?? '' }, range, (t) =>
                engineMetricValue(metric, i.id, t, range.stepSeconds),
              ),
            );
          }
        } else {
          const mode = aggregate === 'sum' || aggregate === 'max' ? aggregate : AVERAGED_ENGINE_METRICS.has(metric) ? 'avg' : 'sum';
          series.push(aggregateSeries(metric, unit, { aggregate: mode, instances: String(instances.length) }, range, instances.map((i) => i.id), (id, t) => engineMetricValue(metric, id, t, range.stepSeconds), mode));
        }
      }
      const out: Schemas['TimeSeriesResponse'] = { from: range.from.toISOString(), to: range.to.toISOString(), step: `${range.stepSeconds}s`, series };
      return ok(out);
    },
  },

  // ── Session metrics ───────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/metrics/sessions',
    handle: (req) => {
      const instanceId = req.query.get('instance_id');
      if (!instanceId || !instanceById(instanceId)) return notFound('Instance');
      const range = parseRange(req.query);
      const out: Schemas['TimeSeriesResponse'] = {
        from: range.from.toISOString(),
        to: range.to.toISOString(),
        step: `${range.stepSeconds}s`,
        series: sessionSeries(instanceId, range, req.query.get('session_key')),
      };
      return ok(out);
    },
  },
  {
    method: 'GET',
    path: '/metrics/sessions/live',
    handle: (req) => {
      const deploymentId = req.query.get('deployment_id');
      const nodeId = req.query.get('node_id');
      const instanceId = req.query.get('instance_id');
      const now = Date.now();
      const list = INSTANCES.filter(
        (i) => i.state === 'ready' && (!deploymentId || i.deployment_id === deploymentId) && (!nodeId || i.node_id === nodeId) && (!instanceId || i.id === instanceId),
      ).map((i) => sessionsSnapshot(i.id, now));
      return paginate(list, req.query);
    },
  },

  // ── Usage metrics ─────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/metrics/usage',
    handle: (req) => {
      const stepParam = req.query.get('step') === '1d' ? '1d' : '1h';
      const query = new URLSearchParams(req.query);
      query.set('step', stepParam);
      const range = parseRange(query);
      const metrics = metricsParam(req.query, ['requests', 'input_tokens', 'output_tokens', 'cost_usd']);
      const groupBy = req.query.get('group_by') ?? 'none';
      const deploymentId = req.query.get('deployment_id');
      const series: Schemas['TimeSeries'][] = [];
      for (const metric of metrics) {
        const unit = USAGE_METRIC_UNITS[metric] ?? '';
        if (groupBy === 'model' || groupBy === 'deployment') {
          for (const d of DEPLOYMENTS) {
            series.push(buildSeries(metric, unit, { deployment_id: d.id, deployment: d.name, model: d.model_name ?? d.name, model_id: d.model_id ?? '' }, range, (t) => usageMetricValue(metric, t, range.stepSeconds, d.id)));
          }
        } else {
          series.push(buildSeries(metric, unit, { scope: deploymentId ? 'deployment' : 'all' }, range, (t) => usageMetricValue(metric, t, range.stepSeconds, deploymentId ?? 'all')));
        }
      }
      const out: Schemas['TimeSeriesResponse'] = { from: range.from.toISOString(), to: range.to.toISOString(), step: stepParam, series };
      return ok(out);
    },
  },

  {
    method: 'GET',
    path: '/metrics/catalog',
    handle: () =>
      ok([
        ...Object.entries(GPU_METRIC_UNITS).map(([name, unit]) => ({ name, unit, type: name.endsWith('_count') ? 'counter' : 'gauge', scope: 'gpu', description: '' })),
        ...Object.entries(ENGINE_METRIC_UNITS).map(([name, unit]) => ({ name, unit, type: name.endsWith('_delta') ? 'counter' : 'gauge', scope: 'engine', description: '' })),
        ...Object.entries(USAGE_METRIC_UNITS).map(([name, unit]) => ({ name, unit, type: 'counter', scope: 'usage', description: '' })),
      ]),
  },
  {
    method: 'GET',
    path: '/metrics/prometheus',
    handle: () => ({ raw: '# mock exposition\ngpudeck_gpu_utilization_ratio{node="gpu-box-01",index="0"} 0.61\n', headers: { 'content-type': 'text/plain; version=0.0.4' } }),
  },

  // ── Instances / deployments (read-only stand-ins, provided by models.ts) ─────
  ...asFallback([
  {
    method: 'GET',
    path: '/instances',
    handle: (req) => {
      const deploymentId = req.query.get('deployment_id');
      const nodeId = req.query.get('node_id');
      const state = req.query.get('state');
      const now = Date.now();
      const list = INSTANCES.filter((i) => (!deploymentId || i.deployment_id === deploymentId) && (!nodeId || i.node_id === nodeId) && (!state || i.state === state)).map((i) => instanceWithLive(i, now));
      return paginate(list, req.query);
    },
  },
  { method: 'GET', path: '/instances/:instance_id', handle: (req) => (instanceById(req.params.instance_id) ? ok(instanceWithLive(instanceById(req.params.instance_id) as Schemas['Instance'])) : notFound('Instance')) },
  {
    method: 'GET',
    path: '/instances/:instance_id/sessions',
    handle: (req) => (instanceById(req.params.instance_id) ? ok(sessionsSnapshot(req.params.instance_id, Date.now())) : notFound('Instance')),
  },
  {
    method: 'GET',
    path: '/deployments',
    handle: (req) => {
      const now = Date.now();
      const status = req.query.get('status');
      const origin = req.query.get('origin');
      const list = DEPLOYMENTS.filter((d) => (!status || d.status === status) && (!origin || (d.origin ?? 'managed') === origin)).map((d) => deploymentWithLive(d, now));
      return paginate(list, req.query);
    },
  },
  {
    method: 'GET',
    path: '/deployments/:deployment_id',
    handle: (req) => {
      const d = DEPLOYMENTS.find((x) => x.id === req.params.deployment_id);
      return d ? ok(deploymentWithLive(d)) : notFound('Deployment');
    },
  },
  { method: 'GET', path: '/deployments/:deployment_id/instances', handle: (req) => paginate(INSTANCES.filter((i) => i.deployment_id === req.params.deployment_id).map((i) => instanceWithLive(i)), req.query) },
  ]),

  // ── Logs ──────────────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/logs',
    handle: (req) => {
      const q = req.query;
      const to = q.get('to') ? new Date(q.get('to') as string).getTime() : Date.now();
      const from = q.get('from') ? new Date(q.get('from') as string).getTime() : to - 3_600_000;
      const page = queryLogs({
        from,
        to,
        cursor: q.get('cursor'),
        limit: Math.min(1000, Math.max(1, Number(q.get('limit') ?? 200) || 200)),
        nodeId: q.get('node_id') ?? undefined,
        instanceId: q.get('instance_id') ?? undefined,
        deploymentId: q.get('deployment_id') ?? undefined,
        sources: q.getAll('source').flatMap((s) => s.split(',')).filter(Boolean),
        minLevel: q.get('level') ?? undefined,
        stream: q.get('stream') ?? undefined,
        requestId: q.get('request_id') ?? undefined,
        search: q.get('search') ?? undefined,
      });
      return ok(page);
    },
  },
  {
    method: 'GET',
    path: '/logs/export',
    handle: (req) => {
      const to = Date.now();
      const from = req.query.get('from') ? new Date(req.query.get('from') as string).getTime() : to - 3_600_000;
      const lines = logChunksBetween(from, to, { minLevel: req.query.get('level') ?? undefined }).map((c) => `${c.ts} ${c.level.toUpperCase().padEnd(5)} ${c.source} ${c.node_name ?? '-'} ${c.content}`);
      return { raw: lines.join('\n'), headers: { 'content-type': 'text/plain; charset=utf-8', 'content-disposition': 'attachment; filename="gpu-deck-logs.txt"' } };
    },
  },

  // ── Traces ────────────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/traces',
    handle: (req) => {
      const q = req.query;
      const to = q.get('to') ? new Date(q.get('to') as string).getTime() : Date.now();
      const from = q.get('from') ? new Date(q.get('from') as string).getTime() : to - 3_600_000;
      const spans = listRootSpans(from, to, {
        service: q.get('service') ?? undefined,
        apiKeyId: q.get('api_key_id') ?? undefined,
        modelId: q.get('model_id') ?? undefined,
        deploymentId: q.get('deployment_id') ?? undefined,
        instanceId: q.get('instance_id') ?? undefined,
        nodeId: q.get('node_id') ?? undefined,
        format: q.get('format') ?? undefined,
        statusCode: q.get('status_code') ? Number(q.get('status_code')) : undefined,
        errorOnly: q.get('error') === 'true',
        minDurationMs: q.get('min_duration_ms') ? Number(q.get('min_duration_ms')) : undefined,
        requestId: q.get('request_id') ?? undefined,
      });
      return paginate(spans, req.query, 50);
    },
  },
  {
    method: 'GET',
    path: '/traces/:trace_id',
    handle: (req) => {
      const trace = traceById(req.params.trace_id);
      return trace ? ok(trace) : notFound('Trace');
    },
  },
]);
