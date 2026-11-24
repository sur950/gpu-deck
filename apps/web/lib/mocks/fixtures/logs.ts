/**
 * Log chunk fixtures: engine, agent, control-plane, rule, swap and API-layer
 * lines in the formats those processes actually print. Chunks are generated
 * per 5-second slot from a hash, so paging backwards and tailing forwards
 * see the same stream.
 */

import { IDS, INSTANCES, NODES } from '@/lib/mocks/fixtures/fleet';
import { hash32, mulberry32, pick } from '@/lib/mocks/fixtures/random';
import { engineActiveSessions, engineTtft } from '@/lib/mocks/fixtures/timeseries';
import type { Schemas } from '@/lib/mocks/types';

type LogChunk = Schemas['LogChunk'];
type LogSource = Schemas['log_source'];
type LogLevel = Schemas['log_level'];

const SLOT_MS = 5_000;

interface Ctx {
  rnd: () => number;
  ts: number;
  instance: Schemas['Instance'];
  node: Schemas['Node'];
}

interface Template {
  source: LogSource;
  weight: number;
  stream: Schemas['log_stream'];
  level: LogLevel | ((c: Ctx) => LogLevel);
  logger: string | null;
  lines: (c: Ctx) => string[];
  nodeBound?: boolean;
  instanceBound?: boolean;
  withRequest?: boolean;
}

function fmtTs(ts: number): string {
  return new Date(ts).toISOString().replace('T', ' ').slice(0, 23);
}

function slotId(c: Ctx): number {
  return Math.floor(c.rnd() * Number(c.instance.resources?.slots ?? 1));
}

function task(c: Ctx): number {
  return 8000 + Math.floor(c.rnd() * 9000);
}

function key(c: Ctx): string {
  return pick(['gd-…a81f', 'gd-…3c2e', 'gd-…97d0'], `key:${c.ts}`);
}

const TEMPLATES: Template[] = [
  {
    source: 'engine',
    weight: 9,
    stream: 'stderr',
    level: 'info',
    logger: null,
    instanceBound: true,
    lines: (c) => {
      if (c.instance.engine_name === 'mlx') {
        const ms = Math.round(engineTtft(c.instance.id, c.ts, 'p50') * 6);
        return [`127.0.0.1 - - [${fmtTs(c.ts)}] "POST /v1/chat/completions HTTP/1.1" 200 - (${ms} ms, ${Math.round(80 + c.rnd() * 500)} tokens)`];
      }
      const s = slotId(c);
      const t = task(c);
      const nPast = Math.round(200 + c.rnd() * 2800);
      const variant = c.rnd();
      if (variant < 0.35) return [`slot launch_slot_: id  ${s} | task ${t} | processing task`];
      if (variant < 0.6) return [`slot update_slots: id  ${s} | task ${t} | kv cache rm [${Math.round(nPast * 0.4)}, end)`, `slot update_slots: id  ${s} | task ${t} | prompt processing progress, n_past = ${nPast}, n_tokens = ${Math.round(nPast * 0.6)}, progress = 1.000000`];
      if (variant < 0.85) return [`slot release: id  ${s} | task ${t} | stop processing: n_past = ${nPast}, truncated = 0`, `srv  update_slots: all slots are idle`.replace('all slots are idle', engineActiveSessions(c.instance.id, c.ts) > 0 ? `${engineActiveSessions(c.instance.id, c.ts)} slot(s) busy` : 'all slots are idle')];
      return [`srv  log_server_r: request: POST /v1/chat/completions 127.0.0.1 200`];
    },
  },
  {
    source: 'engine',
    weight: 1,
    stream: 'stderr',
    level: 'warn',
    logger: null,
    instanceBound: true,
    lines: (c) => [`srv  update_slots: id  ${slotId(c)} | task ${task(c)} | context shift: n_keep = 4, n_left = ${Math.round(2000 + c.rnd() * 4000)}, n_discard = 1024`],
  },
  {
    source: 'agent',
    weight: 5,
    stream: 'app',
    level: 'info',
    logger: 'gpudeck_agent::proxy',
    instanceBound: true,
    withRequest: true,
    lines: (c) => {
      const e2e = Math.round(engineTtft(c.instance.id, c.ts, 'p50') * (4 + c.rnd() * 9));
      const status = c.rnd() > 0.985 ? 502 : 200;
      return [`forwarded POST /v1/chat/completions → 127.0.0.1:${c.instance.port} (${status}, ${e2e} ms, ttfb ${Math.round(engineTtft(c.instance.id, c.ts, 'p50'))} ms)`];
    },
  },
  {
    source: 'agent',
    weight: 3,
    stream: 'app',
    level: 'debug',
    logger: 'gpudeck_agent::scrape',
    instanceBound: true,
    lines: (c) => [`${c.instance.engine_name} ${c.instance.served_model_name}: /metrics ok (${Math.round(2 + c.rnd() * 6)} ms), /slots ok (${Math.round(1 + c.rnd() * 3)} ms)`],
  },
  {
    source: 'agent',
    weight: 2,
    stream: 'app',
    level: 'info',
    logger: 'gpudeck_agent::devices',
    nodeBound: true,
    lines: (c) => (c.node.os === 'macos' ? [`apple: Metal device "Apple M5" wired ${(10.8 + c.rnd() * 1.6).toFixed(1)} GiB / 24.0 GiB, util ${Math.round(30 + c.rnd() * 40)}%`] : [`nvml: 2 devices sampled in ${(1.1 + c.rnd()).toFixed(1)} ms (L40S#0 ${Math.round(190 + c.rnd() * 90)} W, L40S#1 ${Math.round(80 + c.rnd() * 40)} W)`]),
  },
  {
    source: 'control_plane',
    weight: 3,
    stream: 'app',
    level: 'info',
    logger: 'app.services.agent_hub',
    nodeBound: true,
    lines: (c) => [`metrics_batch seq=${Math.floor(c.ts / 2000) % 100000} from ${c.node.name} (${c.node.device_count} gpu, ${c.node.instance_count} engine, ${Math.round(c.rnd() * 12)} sessions) ingested in ${(3 + c.rnd() * 9).toFixed(1)} ms`],
  },
  {
    source: 'control_plane',
    weight: 1,
    stream: 'app',
    level: 'info',
    logger: 'app.services.metrics',
    lines: (c) => [`rollup 1m: ${Math.round(80 + c.rnd() * 60)} gpu rows, ${Math.round(100 + c.rnd() * 80)} engine rows, ${Math.round(600 + c.rnd() * 400)} session rows (${(40 + c.rnd() * 30).toFixed(0)} ms)`],
  },
  {
    source: 'api_layer',
    weight: 6,
    stream: 'app',
    // Upgraded to error by chunkAt when the line reports a 5xx.
    level: 'info',
    logger: 'app.api.serving',
    instanceBound: true,
    withRequest: true,
    lines: (c) => {
      const fmt = pick(['openai', 'anthropic', 'ollama'], `fmt:${c.ts}`);
      const path = fmt === 'anthropic' ? '/anthropic/v1/messages' : fmt === 'ollama' ? '/api/chat' : c.instance.deployment_name === 'nomic-embed' ? '/v1/embeddings' : '/v1/chat/completions';
      const e2e = Math.round(engineTtft(c.instance.id, c.ts, 'p50') * (4 + c.rnd() * 9));
      const err = c.rnd() > 0.97;
      return err
        ? [`POST ${path} key=${key(c)} model=${c.instance.served_model_name} → ${c.instance.deployment_name}@${c.node.name} 502 upstream reset after ${e2e} ms`]
        : [`POST ${path} key=${key(c)} model=${c.instance.served_model_name} → ${c.instance.deployment_name}@${c.node.name} 200 ${e2e.toLocaleString()} ms in=${Math.round(100 + c.rnd() * 1500)} out=${Math.round(20 + c.rnd() * 600)} cached=${Math.round(c.rnd() * 800)}`];
    },
  },
  {
    source: 'rule',
    weight: 1,
    stream: 'app',
    level: 'info',
    logger: 'app.services.rules',
    lines: (c) => {
      const v = c.rnd();
      if (v < 0.4) return [`idle_unload "Night idle unload": gemma-3-1b last request ${Math.round(1 + c.rnd() * 25)}m ago (threshold 30m) → no_match`];
      if (v < 0.7) return [`high_load_grow "qwen3 grow on queue": waiting=${Math.round(c.rnd() * 3)} (threshold 4 for 3m), kv=${Math.round(40 + c.rnd() * 30)}% (threshold 85%) → no_match`];
      if (v < 0.9) return [`auto_heal "Auto heal": qwen3-0.6b@gpu-box-01 healthy, error_rate=${(c.rnd() * 0.6).toFixed(2)}% → no_match`];
      return [`low_traffic_shrink "Shrink qwen3 at night": ${Math.round(20 + c.rnd() * 60)} req/min over 10m (threshold 15) → no_match`];
    },
  },
  {
    source: 'swap',
    weight: 0.4,
    stream: 'app',
    level: 'info',
    logger: 'app.services.swap',
    lines: (c) => [`swap ${hash32(`swap:${c.ts}`).toString(16).slice(0, 4)}: health_check qwen3-0.6b@gpu-box-01 → GET /health 200 in ${Math.round(5 + c.rnd() * 20)} ms, marking ready`],
  },
];

const TOTAL_WEIGHT = TEMPLATES.reduce((s, t) => s + t.weight, 0);

function chooseTemplate(r: number): Template {
  let acc = 0;
  for (const t of TEMPLATES) {
    acc += t.weight / TOTAL_WEIGHT;
    if (r < acc) return t;
  }
  return TEMPLATES[TEMPLATES.length - 1];
}

function chunkAt(slotStart: number, index: number): LogChunk {
  const seed = hash32(`log:${slotStart}:${index}`);
  const rnd = mulberry32(seed);
  const template = chooseTemplate(rnd());
  const instance = INSTANCES[Math.floor(rnd() * INSTANCES.length)];
  const node = NODES.find((n) => n.id === instance.node_id) ?? NODES[0];
  const ts = slotStart + Math.floor(rnd() * SLOT_MS);
  const ctx: Ctx = { rnd, ts, instance, node };
  const lines = template.lines(ctx);
  const level = typeof template.level === 'function' ? template.level(ctx) : template.level;
  const bound = template.instanceBound || template.nodeBound;
  const requestId = template.withRequest
    ? `${hash32(`rid:${seed}`).toString(16).padStart(8, '0')}-${hash32(`r1:${seed}`).toString(16).slice(0, 4)}-4b1c-9e2d-${hash32(`r2:${seed}`).toString(16).padStart(12, '0')}`
    : null;
  return {
    id: seed,
    ts: new Date(ts).toISOString(),
    ts_end: lines.length > 1 ? new Date(ts + 40).toISOString() : null,
    node_id: bound ? node.id : null,
    node_name: bound ? node.name : null,
    instance_id: template.instanceBound ? instance.id : null,
    deployment_id: template.instanceBound ? instance.deployment_id : null,
    source: template.source,
    stream: template.stream,
    level: level === 'info' && /[ (]50[234][ ,)]/.test(lines[0]) ? 'error' : level,
    logger: template.logger,
    request_id: requestId,
    line_count: lines.length,
    content: lines.join('\n'),
    attributes: template.instanceBound ? { engine: instance.engine_name, model: instance.served_model_name } : null,
  };
}

export interface LogScope {
  nodeId?: string;
  instanceId?: string;
  deploymentId?: string;
  sources?: string[];
  minLevel?: string;
  stream?: string;
  requestId?: string;
  search?: string;
}

const LEVEL_RANK: Record<string, number> = { debug: 0, info: 1, warn: 2, error: 3 };

export function matchesScope(chunk: LogChunk, scope: LogScope): boolean {
  if (scope.nodeId && chunk.node_id !== scope.nodeId) return false;
  if (scope.instanceId && chunk.instance_id !== scope.instanceId) return false;
  if (scope.deploymentId && chunk.deployment_id !== scope.deploymentId) return false;
  if (scope.sources && scope.sources.length > 0 && !scope.sources.includes(chunk.source)) return false;
  if (scope.minLevel && (LEVEL_RANK[chunk.level] ?? 0) < (LEVEL_RANK[scope.minLevel] ?? 0)) return false;
  if (scope.stream && chunk.stream !== scope.stream) return false;
  if (scope.requestId && chunk.request_id !== scope.requestId) return false;
  if (scope.search && !chunk.content.toLowerCase().includes(scope.search.toLowerCase())) return false;
  return true;
}

/** All chunks with `from <= ts < to`, oldest first, filtered by scope. */
export function logChunksBetween(from: number, to: number, scope: LogScope = {}): LogChunk[] {
  const out: LogChunk[] = [];
  const first = Math.floor(from / SLOT_MS) * SLOT_MS;
  for (let slot = first; slot < to; slot += SLOT_MS) {
    const count = hash32(`count:${slot}`) % 4; // 0–3 chunks per 5 s
    for (let i = 0; i < count; i += 1) {
      const chunk = chunkAt(slot, i);
      const ts = new Date(chunk.ts).getTime();
      if (ts < from || ts >= to) continue;
      if (matchesScope(chunk, scope)) out.push(chunk);
    }
  }
  return out.sort((a, b) => a.ts.localeCompare(b.ts));
}

/**
 * Cursor-paginated query, newest first. The cursor is the ISO timestamp to
 * continue backwards from (exclusive).
 */
export function queryLogs(
  params: { from: number; to: number; cursor: string | null; limit: number } & LogScope,
): Schemas['LogPage'] {
  const to = params.cursor ? Math.min(params.to, new Date(params.cursor).getTime()) : params.to;
  const items: LogChunk[] = [];
  // Walk backwards in 10-minute windows until the page is full.
  let windowEnd = to;
  const minFrom = params.from;
  while (items.length < params.limit + 1 && windowEnd > minFrom) {
    const windowStart = Math.max(minFrom, windowEnd - 600_000);
    const chunks = logChunksBetween(windowStart, windowEnd, params).reverse();
    items.push(...chunks);
    windowEnd = windowStart;
  }
  const page = items.slice(0, params.limit);
  const hasMore = items.length > params.limit || windowEnd > minFrom;
  return {
    items: page,
    next_cursor: hasMore && page.length > 0 ? page[page.length - 1].ts : null,
    has_more: hasMore,
  };
}

export const LOG_NODE_IDS = { mac: IDS.nodes.mac, gpu: IDS.nodes.gpu };
