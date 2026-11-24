/**
 * OpenTelemetry trace fixtures: API layer → node agent proxy → engine.
 * Root spans are generated per 20-second slot; a full trace is rebuilt
 * from its root deterministically.
 */

import { IDS, INSTANCES, NODES } from '@/lib/mocks/fixtures/fleet';
import { hash32, hexId, mulberry32, pick, round } from '@/lib/mocks/fixtures/random';
import { engineTtft, engineE2e } from '@/lib/mocks/fixtures/timeseries';
import type { Schemas } from '@/lib/mocks/types';

type TraceSpan = Schemas['TraceSpan'];

const SLOT_MS = 20_000;
const API_KEYS = [IDS.apiKeys.gateway, IDS.apiKeys.gateway, IDS.apiKeys.gateway, IDS.apiKeys.indexer, IDS.apiKeys.evals];

interface RootSeed {
  slot: number;
  index: number;
}

interface TraceCore {
  seed: RootSeed;
  traceId: string;
  ts: number;
  instance: Schemas['Instance'];
  node: Schemas['Node'];
  format: Schemas['usage_format'];
  endpoint: string;
  status: number;
  error: boolean;
  durationMs: number;
  ttftMs: number;
  promptTokens: number;
  outputTokens: number;
  cachedTokens: number;
  apiKeyId: string;
  requestId: string;
}

function coreFor(seed: RootSeed): TraceCore {
  const rnd = mulberry32(hash32(`trace:${seed.slot}:${seed.index}`));
  const ts = seed.slot + Math.floor(rnd() * SLOT_MS);
  const instance = INSTANCES[Math.floor(rnd() * INSTANCES.length)];
  const node = NODES.find((n) => n.id === instance.node_id) ?? NODES[0];
  const isEmbed = instance.deployment_id === IDS.deployments.nomic;
  const format: Schemas['usage_format'] = isEmbed ? pick(['openai', 'ollama'], `f:${ts}`) : pick(['openai', 'openai', 'anthropic', 'ollama'], `f:${ts}`);
  const endpoint = isEmbed
    ? format === 'ollama'
      ? '/api/embed'
      : '/v1/embeddings'
    : format === 'anthropic'
      ? '/anthropic/v1/messages'
      : format === 'ollama'
        ? '/api/chat'
        : rnd() > 0.8
          ? '/v1/responses'
          : '/v1/chat/completions';
  const r = rnd();
  const status = r > 0.975 ? 502 : r > 0.955 ? 429 : r > 0.945 ? 400 : 200;
  const ttftMs = engineTtft(instance.id, ts, 'p50') * (0.6 + rnd() * 1.2);
  const durationMs = status >= 500 ? ttftMs * 0.3 + 30_000 * (0.3 + rnd() * 0.2) : status === 429 || status === 400 ? 2 + rnd() * 4 : engineE2e(instance.id, ts, 'p50') * (0.5 + rnd() * 1.4);
  const promptTokens = Math.round(isEmbed ? 60 + rnd() * 500 : 120 + rnd() * 2600);
  return {
    seed,
    traceId: hexId(`trace:${seed.slot}:${seed.index}`, 32),
    ts,
    instance,
    node,
    format,
    endpoint,
    status,
    error: status >= 400,
    durationMs: round(durationMs, 1),
    ttftMs: round(ttftMs, 1),
    promptTokens,
    outputTokens: isEmbed ? 0 : Math.round(16 + rnd() * 700),
    cachedTokens: isEmbed ? 0 : Math.round(promptTokens * (rnd() > 0.4 ? rnd() * 0.85 : 0)),
    apiKeyId: API_KEYS[Math.floor(rnd() * API_KEYS.length)],
    requestId: `${hexId(`rid:${seed.slot}:${seed.index}`, 32).slice(0, 8)}-${hexId(`rid2:${seed.slot}`, 16).slice(0, 4)}-4f3a-8c1d-${hexId(`rid3:${seed.index}:${seed.slot}`, 16).slice(0, 12)}`,
  };
}

function rootSpan(core: TraceCore): TraceSpan {
  return {
    ts: new Date(core.ts).toISOString(),
    trace_id: core.traceId,
    span_id: hexId(`span:root:${core.traceId}`, 16),
    parent_span_id: null,
    name: `POST ${core.endpoint}`,
    service: 'api_layer',
    request_id: core.requestId,
    api_key_id: core.apiKeyId,
    model_id: IDS.models[core.instance.deployment_id === IDS.deployments.qwen ? 'qwen' : core.instance.deployment_id === IDS.deployments.gemma ? 'gemma' : 'nomic'],
    deployment_id: core.instance.deployment_id,
    instance_id: core.instance.id,
    node_id: core.node.id,
    format: core.format,
    endpoint: core.endpoint,
    status_code: core.status,
    duration_ms: core.durationMs,
    error: core.error,
    attributes: {
      'http.method': 'POST',
      'http.route': core.endpoint,
      'gpudeck.model': core.instance.served_model_name,
      'gpudeck.deployment': core.instance.deployment_name,
      'gpudeck.stream': core.endpoint.includes('embed') ? false : true,
      'llm.usage.prompt_tokens': core.promptTokens,
      'llm.usage.completion_tokens': core.outputTokens,
      'llm.usage.cached_tokens': core.cachedTokens,
    },
  };
}

function rootsInRange(from: number, to: number): TraceCore[] {
  const out: TraceCore[] = [];
  const first = Math.floor(from / SLOT_MS) * SLOT_MS;
  for (let slot = first; slot < to; slot += SLOT_MS) {
    const count = hash32(`tcount:${slot}`) % 3; // 0–2 per 20 s
    for (let index = 0; index < count; index += 1) {
      const core = coreFor({ slot, index });
      if (core.ts >= from && core.ts < to) out.push(core);
    }
  }
  return out;
}

export interface TraceFilters {
  service?: string;
  apiKeyId?: string;
  modelId?: string;
  deploymentId?: string;
  instanceId?: string;
  nodeId?: string;
  format?: string;
  statusCode?: number;
  errorOnly?: boolean;
  minDurationMs?: number;
  requestId?: string;
}

export function listRootSpans(from: number, to: number, filters: TraceFilters): TraceSpan[] {
  return rootsInRange(from, to)
    .map(rootSpan)
    .filter((s) => {
      if (filters.service && s.service !== filters.service) return false;
      if (filters.apiKeyId && s.api_key_id !== filters.apiKeyId) return false;
      if (filters.modelId && s.model_id !== filters.modelId) return false;
      if (filters.deploymentId && s.deployment_id !== filters.deploymentId) return false;
      if (filters.instanceId && s.instance_id !== filters.instanceId) return false;
      if (filters.nodeId && s.node_id !== filters.nodeId) return false;
      if (filters.format && s.format !== filters.format) return false;
      if (filters.statusCode !== undefined && s.status_code !== filters.statusCode) return false;
      if (filters.errorOnly && !s.error) return false;
      if (filters.minDurationMs !== undefined && (s.duration_ms ?? 0) < filters.minDurationMs) return false;
      if (filters.requestId && s.request_id !== filters.requestId) return false;
      return true;
    })
    .sort((a, b) => b.ts.localeCompare(a.ts));
}

const traceIndex = new Map<string, RootSeed>();

function findCore(traceId: string, now = Date.now()): TraceCore | null {
  const cached = traceIndex.get(traceId);
  if (cached) return coreFor(cached);
  // Scan the last 48 h of slots (cheap: ~17k hashes).
  const first = Math.floor((now - 48 * 3_600_000) / SLOT_MS) * SLOT_MS;
  for (let slot = first; slot < now; slot += SLOT_MS) {
    const count = hash32(`tcount:${slot}`) % 3;
    for (let index = 0; index < count; index += 1) {
      const id = hexId(`trace:${slot}:${index}`, 32);
      traceIndex.set(id, { slot, index });
      if (id === traceId) return coreFor({ slot, index });
    }
  }
  return null;
}

function child(core: TraceCore, parent: TraceSpan, name: string, service: string, offsetMs: number, durationMs: number, attributes: Record<string, unknown> = {}, error = false): TraceSpan {
  return {
    ts: new Date(core.ts + offsetMs).toISOString(),
    trace_id: core.traceId,
    span_id: hexId(`span:${name}:${core.traceId}`, 16),
    parent_span_id: parent.span_id,
    name,
    service,
    request_id: core.requestId,
    api_key_id: core.apiKeyId,
    model_id: parent.model_id,
    deployment_id: core.instance.deployment_id,
    instance_id: core.instance.id,
    node_id: core.node.id,
    format: core.format,
    endpoint: core.endpoint,
    status_code: null,
    duration_ms: round(durationMs, 2),
    error,
    attributes,
  };
}

export function traceById(traceId: string): Schemas['Trace'] | null {
  const core = findCore(traceId);
  if (!core) return null;
  const root = rootSpan(core);
  const spans: TraceSpan[] = [root];
  const auth = child(core, root, 'auth.api_key', 'api_layer', 0.2, 1.4 + (hash32(core.traceId) % 10) / 10, { 'gpudeck.api_key.prefix': 'gd-…a81f', 'cache.hit': true });
  spans.push(auth);
  if (core.status === 429 || core.status === 400) {
    spans.push(child(core, root, core.status === 429 ? 'limits.check' : 'request.validate', 'api_layer', 1.8, core.durationMs - 2, { 'http.status_code': core.status }, true));
    return { trace_id: core.traceId, root, spans, duration_ms: core.durationMs, otlp_url: null };
  }
  const route = child(core, root, 'route.select', 'api_layer', 1.9, 0.6, { 'gpudeck.routing.candidates': 2, 'gpudeck.routing.chosen': `${core.instance.deployment_name}@${core.node.name}`, 'gpudeck.routing.weight': core.instance.traffic_weight });
  spans.push(route);
  const proxyStart = 2.8;
  const proxyDur = core.durationMs - proxyStart - 1.2;
  const forward = child(core, root, 'upstream.forward', 'api_layer', proxyStart, proxyDur, { 'net.peer.name': `${core.node.name}:9443`, tls: 'mtls' }, core.error);
  spans.push(forward);
  const agentProxy = child(core, forward, 'proxy.forward', 'node_agent', proxyStart + 1.1, proxyDur - 2.1, { 'net.peer.name': `127.0.0.1:${core.instance.port}`, 'gpudeck.proxy.bytes_in': 1800 + core.promptTokens * 4, 'gpudeck.proxy.bytes_out': 300 + core.outputTokens * 5 }, core.error);
  spans.push(agentProxy);
  const engineService = core.instance.engine_name === 'mlx' ? 'mlx_lm' : 'llama_cpp';
  const genDur = proxyDur - 3.4;
  const generate = child(core, agentProxy, core.endpoint.includes('embed') ? 'engine.embed' : 'engine.generate', engineService, proxyStart + 1.9, genDur, { 'llm.slot': hash32(core.traceId) % 8, 'llm.prompt_tokens': core.promptTokens, 'llm.cached_tokens': core.cachedTokens, 'llm.completion_tokens': core.outputTokens }, core.error);
  spans.push(generate);
  if (!core.endpoint.includes('embed')) {
    const prefillDur = Math.min(genDur * 0.8, core.ttftMs);
    spans.push(child(core, generate, 'prefill', engineService, proxyStart + 2.0, prefillDur, { 'llm.prompt_tokens': core.promptTokens, 'llm.cached_tokens': core.cachedTokens, 'llm.computed_tokens': core.promptTokens - core.cachedTokens }));
    if (!core.error) {
      spans.push(child(core, generate, 'decode', engineService, proxyStart + 2.0 + prefillDur, genDur - prefillDur - 0.5, { 'llm.completion_tokens': core.outputTokens, 'llm.tokens_per_second': round(core.outputTokens / Math.max(0.05, (genDur - prefillDur) / 1000), 1) }));
    }
  }
  spans.push(child(core, root, 'usage.enqueue', 'api_layer', core.durationMs - 1.0, 0.7, { 'celery.queue': 'metering' }));
  return { trace_id: core.traceId, root, spans, duration_ms: core.durationMs, otlp_url: null };
}
