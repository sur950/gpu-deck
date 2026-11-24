/**
 * Security mocks: API keys, the audit log and IP allow / block rules.
 *
 * `recordAudit()` is exported so the automation and usage mocks write audit
 * entries for the actions they perform (rule fired, layout applied, price
 * plan changed…), the same way the backend's `audit.record()` does.
 * IP rule checks follow the backend rules: block wins; any
 * enabled allow rule turns its scope into allow-list mode.
 */

import { IDS } from '@/lib/mocks/fixtures/fleet';
import { hash32, mulberry32, uuid } from '@/lib/mocks/fixtures/random';
import { bodyOf, created, fail, noContent, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { Schemas } from '@/lib/mocks/types';
import { cidrContains, parseCidr, parseIp } from '@/components/security/cidr';

type ApiKey = Schemas['ApiKey'];
type AuditLog = Schemas['AuditLog'];
type IpRule = Schemas['IpRule'];

const MIN = 60_000;
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const BOOT = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();

const ADMIN_IP = '192.168.1.24';
const ADMIN_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 15_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.1 Safari/605.1.15';

// ─── Audit log ────────────────────────────────────────────────────────────────

const audit: AuditLog[] = [];

export interface AuditInput {
  eventType: string;
  actorType?: AuditLog['actor_type'];
  actorId?: string | null;
  actorName?: string | null;
  resourceType?: string | null;
  resourceId?: string | null;
  resourceName?: string | null;
  outcome?: AuditLog['outcome'];
  message?: string | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  metadata?: Record<string, unknown>;
  at?: number;
  ip?: string | null;
}

/** Append an audit entry (newest first when listed). Never throws. */
export function recordAudit(input: AuditInput): void {
  const at = input.at ?? Date.now();
  const actorType = input.actorType ?? 'admin';
  audit.push({
    id: uuid(`audit:${input.eventType}:${input.resourceId ?? ''}:${at}:${audit.length}`),
    created_at: iso(at),
    event_type: input.eventType,
    actor_type: actorType,
    actor_id: input.actorId ?? (actorType === 'admin' ? IDS.admin : null),
    actor_name: input.actorName ?? (actorType === 'admin' ? 'admin' : null),
    resource_type: input.resourceType ?? null,
    resource_id: input.resourceId ?? null,
    resource_name: input.resourceName ?? null,
    outcome: input.outcome ?? 'success',
    ip_address: input.ip !== undefined ? input.ip : actorType === 'admin' ? ADMIN_IP : null,
    user_agent: actorType === 'admin' ? ADMIN_UA : null,
    request_id: actorType === 'admin' ? `req_${hash32(`${at}:${input.eventType}`).toString(36)}${audit.length.toString(36)}` : null,
    message: input.message ?? null,
    before_state: input.before ?? null,
    after_state: input.after ?? null,
    metadata: input.metadata ?? {},
  });
}

function seedAudit(): void {
  const rand = mulberry32(hash32('audit-seed'));
  const t = (daysAgo: number, h = 0, m = 0) => BOOT - daysAgo * DAY - h * HOUR - m * MIN;

  // Daily sign-ins (and a few failures from an unknown address).
  for (let d = 13; d >= 0; d -= 1) {
    const at = t(d, 0) - Math.floor(rand() * 9 * HOUR) - 2 * HOUR;
    if (at > BOOT) continue;
    recordAudit({ eventType: 'auth.login', resourceType: 'session', resourceName: 'admin', message: 'Signed in', at, metadata: { method: 'password', session_ttl_minutes: 60 } });
    if (d % 4 === 1) {
      for (let k = 0; k < 3; k += 1) {
        recordAudit({ eventType: 'auth.login', actorType: 'admin', actorName: 'admin', outcome: 'failure', resourceType: 'session', resourceName: 'admin', message: 'Invalid username or password', at: at - (k + 1) * 47_000, ip: '45.83.64.12', metadata: { attempt: k + 1, lockout_after: 5 } });
      }
    }
  }
  recordAudit({ eventType: 'auth.password_changed', resourceType: 'admin_user', resourceId: IDS.admin, resourceName: 'admin', message: 'Password changed; 2 other sessions revoked', at: t(9, 3), metadata: { revoked_sessions: 2 } });

  // API keys.
  recordAudit({ eventType: 'api_key.created', resourceType: 'api_key', resourceId: IDS.apiKeys.gateway, resourceName: 'gateway-prod', at: t(20, 2), after: { name: 'gateway-prod', key_prefix: 'gd-a6cc0cf71', expires_at: null, status: 'active' } });
  recordAudit({ eventType: 'api_key.created', resourceType: 'api_key', resourceId: IDS.apiKeys.indexer, resourceName: 'docs-indexer', at: t(16, 5), after: { name: 'docs-indexer', key_prefix: 'gd-ed21322df', expires_at: iso(BOOT + 74 * DAY), status: 'active' } });
  recordAudit({ eventType: 'api_key.created', resourceType: 'api_key', resourceId: IDS.apiKeys.evals, resourceName: 'evals', at: t(11, 1), after: { name: 'evals', key_prefix: 'gd-4c265aac1', expires_at: iso(BOOT + 19 * DAY), status: 'active' } });
  recordAudit({ eventType: 'api_key.revoked', resourceType: 'api_key', resourceId: uuid('api-key:laptop-test'), resourceName: 'alice-laptop-test', at: t(6, 4), before: { status: 'active' }, after: { status: 'revoked', revoked_at: iso(t(6, 4)) } });
  recordAudit({ eventType: 'api_key.rotated', resourceType: 'api_key', resourceId: IDS.apiKeys.gateway, resourceName: 'gateway-prod', at: t(3, 6), before: { key_prefix: 'gd-1b1cbf16b' }, after: { key_prefix: 'gd-a6cc0cf71', last_rotated_at: iso(t(3, 6)) }, message: 'Old secret invalidated immediately' });
  recordAudit({ eventType: 'api_key.auth_failed', actorType: 'api_key', actorName: 'gd-0000000ff…', outcome: 'failure', resourceType: 'api_key', message: 'Unknown key presented to the API layer', at: t(1, 7, 12), ip: '10.20.4.17', metadata: { endpoint: '/v1/chat/completions' } });

  // Fleet.
  recordAudit({ eventType: 'node.joined', actorType: 'node_agent', actorId: IDS.nodes.gpu, actorName: 'gpu-box-01', resourceType: 'node', resourceId: IDS.nodes.gpu, resourceName: 'gpu-box-01', at: t(14, 3), ip: '192.168.1.61', after: { status: 'online', agent_version: '1.1.0', devices: 2 }, metadata: { join_token: 'jt_…3f9a', cert_serial: '4E:21:…:9C' } });
  recordAudit({ eventType: 'node.joined', actorType: 'node_agent', actorId: IDS.nodes.mac, actorName: 'mac-m5-studio', resourceType: 'node', resourceId: IDS.nodes.mac, resourceName: 'mac-m5-studio', at: t(21, 1), ip: '192.168.1.40', after: { status: 'online', agent_version: '1.1.0', devices: 1 } });
  recordAudit({ eventType: 'node.labels_updated', resourceType: 'node', resourceId: IDS.nodes.gpu, resourceName: 'gpu-box-01', at: t(13, 2), before: { labels: { role: 'serving' } }, after: { labels: { role: 'serving', rack: 'r1', 'gpu-deck.io/vendor': 'nvidia' } } });
  recordAudit({ eventType: 'deployment.created', resourceType: 'deployment', resourceId: IDS.deployments.qwen, resourceName: 'qwen3-0.6b', at: t(6, 1), after: { model: 'Qwen3 0.6B', engine: 'llama_cpp', replicas: 1, priority_override: 'always_on', preset: 'low_latency' } });
  recordAudit({ eventType: 'deployment.updated', resourceType: 'deployment', resourceId: IDS.deployments.qwen, resourceName: 'qwen3-0.6b', at: t(4, 3), before: { replicas: 1, max_replicas: 2, launch_flags: { '--parallel': 4 } }, after: { replicas: 2, max_replicas: 3, launch_flags: { '--parallel': 8, '--flash-attn': true } } });
  recordAudit({ eventType: 'vault.credential_revealed', resourceType: 'credential', resourceId: uuid('credential:hf-token'), resourceName: 'huggingface-token', at: t(5, 2), message: 'Secret revealed in the UI', metadata: { reason: 'copy to node bootstrap' } });

  // Rules and swaps (system / rule actors).
  recordAudit({ eventType: 'rule.created', resourceType: 'rule', resourceId: IDS.rules.highLoad, resourceName: 'qwen3 grow on queue', at: t(4, 2), after: { type: 'high_load_grow', dry_run: true, params: { waiting_requests_above: 4, kv_cache_pct_above: 85, for_minutes: 3, max_replicas: 3 } } });
  recordAudit({ eventType: 'rule.updated', resourceType: 'rule', resourceId: IDS.rules.idleUnload, resourceName: 'Night idle unload', at: t(2, 8), before: { params: { idle_minutes: 45, respect_time_windows: [] } }, after: { params: { idle_minutes: 30, respect_time_windows: ['20:00-08:00'] } } });
  recordAudit({ eventType: 'rule.fired', actorType: 'rule', actorId: IDS.rules.autoHeal, actorName: 'Auto heal', resourceType: 'instance', resourceId: IDS.instances.qwenL40s, resourceName: 'qwen3-0.6b@gpu-box-01', at: BOOT - 26 * MIN, after: { actions: [{ action: 'restart', target: 'qwen3-0.6b@gpu-box-01' }] } });
  recordAudit({ eventType: 'instance.unloaded', actorType: 'rule', actorId: IDS.rules.idleUnload, actorName: 'Night idle unload', resourceType: 'instance', resourceId: uuid('instance:gemma-3-1b:mac:prev'), resourceName: 'gemma-3-1b@mac-m5-studio', at: BOOT - 126 * MIN, before: { state: 'ready', vram_bytes: 4294967296 }, after: { state: 'stopped' } });
  recordAudit({ eventType: 'instance.loaded', actorType: 'system', actorName: 'swap manager', resourceType: 'instance', resourceId: IDS.instances.gemmaMac, resourceName: 'gemma-3-1b@mac-m5-studio', at: BOOT - 6 * HOUR, after: { state: 'ready', port: 10241 } });
  recordAudit({ eventType: 'swap.rolled_back', actorType: 'system', actorName: 'swap manager', outcome: 'failure', resourceType: 'swap_operation', resourceId: uuid('swap:move-qwen-failed'), resourceName: 'move qwen3-0.6b', at: BOOT - 191 * MIN, message: 'health check failed on L40S #1 — source instance kept', before: { state: 'health_check' }, after: { state: 'rolled_back' } });
  recordAudit({ eventType: 'layout.applied', actorType: 'rule', actorId: IDS.rules.schedule, actorName: 'Day layout at 08:00', resourceType: 'layout', resourceId: IDS.layouts.day, resourceName: 'Day', at: BOOT - 6 * HOUR - 4 * MIN, after: { swaps: 2, unlisted_policy: 'keep' } });

  // Security settings.
  recordAudit({ eventType: 'ip_rule.created', resourceType: 'ip_rule', resourceId: uuid('ip:admin:office'), resourceName: '192.168.1.0/24', at: t(19, 4), after: { scope: 'admin', action: 'allow', cidr: '192.168.1.0/24' } });
  recordAudit({ eventType: 'ip_rule.created', resourceType: 'ip_rule', resourceId: uuid('ip:admin:block-scanner'), resourceName: '45.83.64.0/24', at: t(9, 1), after: { scope: 'admin', action: 'block', cidr: '45.83.64.0/24' }, message: 'Blocked after repeated failed sign-ins' });
  recordAudit({ eventType: 'price_plan.updated', resourceType: 'price_plan', resourceId: uuid('price-plan:l40s'), resourceName: 'L40S on-prem', at: t(8, 6), before: { usd_per_gpu_minute: 0.01 }, after: { usd_per_gpu_minute: 0.012 } });
  recordAudit({ eventType: 'alert_channel.updated', resourceType: 'alert_channel', resourceId: uuid('channel:email-oncall'), resourceName: 'Email on-call', at: t(9, 2), before: { min_severity: 'info' }, after: { min_severity: 'warning', event_filter: ['swap.*', 'heal.*', 'rule.error'] } });

  // Periodic system noise (partition jobs, cert rotation).
  for (let d = 0; d < 14; d += 1) {
    recordAudit({ eventType: 'system.partitions_created', actorType: 'system', actorName: 'jobs', resourceType: 'database', resourceName: 'metric_samples_*', at: t(d, 0) - (BOOT % DAY) + 15 * MIN, metadata: { tables: 4, days_ahead: 7 } });
  }
  recordAudit({ eventType: 'pki.certificate_rotated', actorType: 'system', actorName: 'pki', resourceType: 'node_certificate', resourceName: 'api-layer client cert', at: t(7, 9), before: { not_after: iso(BOOT + 2 * DAY) }, after: { not_after: iso(BOOT + 88 * DAY) } });
}

seedAudit();

function filterAudit(q: URLSearchParams): AuditLog[] {
  const search = (q.get('search') ?? '').toLowerCase();
  const actorType = q.get('actor_type');
  const actorId = q.get('actor_id');
  const eventType = q.get('event_type');
  const resourceType = q.get('resource_type');
  const resourceId = q.get('resource_id');
  const requestId = q.get('request_id');
  const outcome = q.get('outcome');
  const from = q.get('from');
  const to = q.get('to');
  const list = audit.filter(
    (a) =>
      (!search || [a.event_type, a.actor_name, a.resource_name, a.message, a.ip_address].some((v) => (v ?? '').toLowerCase().includes(search))) &&
      (!actorType || a.actor_type === actorType) &&
      (!actorId || a.actor_id === actorId) &&
      (!eventType || a.event_type === eventType || (eventType.endsWith('*') && a.event_type.startsWith(eventType.slice(0, -1))) || (!eventType.includes('.') && a.event_type.startsWith(`${eventType}.`))) &&
      (!resourceType || a.resource_type === resourceType) &&
      (!resourceId || a.resource_id === resourceId) &&
      (!requestId || a.request_id === requestId) &&
      (!outcome || a.outcome === outcome) &&
      (!from || a.created_at >= from) &&
      (!to || a.created_at <= to),
  );
  return sortBy(list as unknown as Record<string, unknown>[], q.get('sort'), '-created_at') as unknown as AuditLog[];
}

function csvCell(value: unknown): string {
  const s = value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function csvResponse(rows: unknown[][], filename: string) {
  return {
    raw: rows.map((r) => r.map(csvCell).join(',')).join('\n'),
    status: 200,
    headers: { 'content-type': 'text/csv; charset=utf-8', 'content-disposition': `attachment; filename="${filename}"` },
  };
}

// ─── API keys ─────────────────────────────────────────────────────────────────

const HEX = '0123456789abcdef';

function keyPrefix(seed: string): string {
  // Keys are `gd-` + 48 hex; `key_prefix` is the first 12 characters.
  const r = mulberry32(hash32(seed));
  return `gd-${Array.from({ length: 9 }, () => HEX[Math.floor(r() * 16)]).join('')}`;
}

function secretFor(prefix: string, seed: string): string {
  const r = mulberry32(hash32(`secret:${seed}`));
  return `${prefix}${Array.from({ length: 51 - prefix.length }, () => HEX[Math.floor(r() * 16)]).join('')}`;
}

const apiKeys: ApiKey[] = [
  { id: IDS.apiKeys.gateway, name: 'gateway-prod', description: 'The AI gateway in front of GPU Deck, calling the self-hosted models.', key_prefix: 'gd-a6cc0cf71', status: 'active', total_requests: 1_284_310, expires_at: null, last_used_at: iso(BOOT - 3_000), last_rotated_at: iso(BOOT - 3 * DAY - 6 * HOUR), revoked_at: null, metadata: { owner: 'platform', env: 'prod' }, created_at: iso(BOOT - 20 * DAY), updated_at: iso(BOOT - 3 * DAY) },
  { id: IDS.apiKeys.indexer, name: 'docs-indexer', description: 'Nightly embedding job for the docs search index.', key_prefix: 'gd-ed21322df', status: 'active', total_requests: 412_008, expires_at: iso(BOOT + 74 * DAY), last_used_at: iso(BOOT - 41 * MIN), last_rotated_at: null, revoked_at: null, metadata: { owner: 'search' }, created_at: iso(BOOT - 16 * DAY), updated_at: iso(BOOT - 16 * DAY) },
  { id: IDS.apiKeys.evals, name: 'evals', description: 'Evaluation jobs — Anthropic and OpenAI formats.', key_prefix: 'gd-4c265aac1', status: 'active', total_requests: 58_922, expires_at: iso(BOOT + 19 * DAY), last_used_at: iso(BOOT - 3 * HOUR), last_rotated_at: null, revoked_at: null, metadata: { owner: 'ml' }, created_at: iso(BOOT - 11 * DAY), updated_at: iso(BOOT - 11 * DAY) },
  { id: uuid('api-key:grafana-probe'), name: 'grafana-synthetic-probe', description: 'Synthetic chat probe from Grafana every minute.', key_prefix: 'gd-a35c0fde2', status: 'suspended', total_requests: 9_812, expires_at: null, last_used_at: iso(BOOT - 2 * DAY), last_rotated_at: null, revoked_at: null, metadata: {}, created_at: iso(BOOT - 9 * DAY), updated_at: iso(BOOT - 2 * DAY) },
  { id: uuid('api-key:hackathon'), name: 'hackathon-nov', description: 'Temporary key for the internal hackathon.', key_prefix: 'gd-687fdc50c', status: 'expired', total_requests: 22_150, expires_at: iso(BOOT - 4 * DAY), last_used_at: iso(BOOT - 4 * DAY - 2 * HOUR), last_rotated_at: null, revoked_at: null, metadata: { event: 'hackathon' }, created_at: iso(BOOT - 8 * DAY), updated_at: iso(BOOT - 4 * DAY) },
  { id: uuid('api-key:laptop-test'), name: 'alice-laptop-test', description: null, key_prefix: 'gd-99a4863a5', status: 'revoked', total_requests: 312, expires_at: null, last_used_at: iso(BOOT - 6 * DAY - 5 * HOUR), last_rotated_at: null, revoked_at: iso(BOOT - 6 * DAY - 4 * HOUR), metadata: {}, created_at: iso(BOOT - 7 * DAY), updated_at: iso(BOOT - 6 * DAY) },
];

/** Name lookup for the usage mocks (unknown ids fall back to the prefix). */
export function apiKeyName(id: string): string {
  return apiKeys.find((k) => k.id === id)?.name ?? id.slice(0, 8);
}

function withLiveStatus(k: ApiKey, now = Date.now()): ApiKey {
  if (k.status === 'active' && k.expires_at && Date.parse(k.expires_at) <= now) return { ...k, status: 'expired' };
  if (k.status === 'active' && k.id === IDS.apiKeys.gateway) return { ...k, last_used_at: iso(now - 1_000 - (now % 4_000)), total_requests: k.total_requests + Math.floor((now - BOOT) / 900) };
  return k;
}

function keyById(id: string): ApiKey | undefined {
  return apiKeys.find((k) => k.id === id);
}

function setKeyStatus(k: ApiKey, status: ApiKey['status'], event: string) {
  const before = { status: k.status };
  k.status = status;
  k.updated_at = iso(Date.now());
  if (status === 'revoked') k.revoked_at = iso(Date.now());
  recordAudit({ eventType: event, resourceType: 'api_key', resourceId: k.id, resourceName: k.name, before, after: { status } });
}

// ─── IP rules ─────────────────────────────────────────────────────────────────

function ipRule(id: string, scope: IpRule['scope'], action: IpRule['action'], cidr: string, description: string, daysAgo: number, extra: Partial<IpRule> = {}): IpRule {
  return { id, scope, action, cidr, description, enabled: true, expires_at: null, created_at: iso(BOOT - daysAgo * DAY), updated_at: iso(BOOT - daysAgo * DAY), ...extra };
}

const ipRules: IpRule[] = [
  ipRule(uuid('ip:admin:office'), 'admin', 'allow', '192.168.1.0/24', 'Bangalore office LAN', 19),
  ipRule(uuid('ip:admin:vpn'), 'admin', 'allow', '10.8.0.0/16', 'WireGuard VPN', 19),
  ipRule(uuid('ip:admin:block-scanner'), 'admin', 'block', '45.83.64.0/24', 'Repeated failed sign-ins (scanner)', 9),
  ipRule(uuid('ip:admin:ipv6'), 'admin', 'allow', '2001:db8:4b0::/48', 'Office IPv6 prefix', 6, { enabled: false }),
  ipRule(uuid('ip:api:gateway'), 'api_layer', 'allow', '192.168.1.10/32', 'AI gateway host', 18),
  ipRule(uuid('ip:api:k8s'), 'api_layer', 'allow', '10.20.0.0/16', 'Kubernetes pod network (indexer, evals)', 16),
  ipRule(uuid('ip:api:contractor'), 'api_layer', 'block', '10.20.9.0/24', 'Contractor namespace — no model access', 4, { expires_at: iso(BOOT + 10 * DAY) }),
];

function liveIpRules(now = Date.now()): IpRule[] {
  return ipRules.filter((r) => !r.expires_at || Date.parse(r.expires_at) > now);
}

function checkIp(ip: string, scope: IpRule['scope']): Schemas['IpCheckResult'] {
  const active = liveIpRules().filter((r) => r.enabled && r.scope === scope);
  const block = active.find((r) => r.action === 'block' && cidrContains(r.cidr, ip));
  if (block) return { ip, scope, allowed: false, reason: `Blocked by ${block.cidr}${block.description ? ` (${block.description})` : ''} — block rules always win`, matched_rule: block };
  const allows = active.filter((r) => r.action === 'allow');
  if (!allows.length) return { ip, scope, allowed: true, reason: 'No allow rules for this scope, so every address that is not blocked may connect', matched_rule: null };
  const allow = allows.find((r) => cidrContains(r.cidr, ip));
  if (allow) return { ip, scope, allowed: true, reason: `Allowed by ${allow.cidr}${allow.description ? ` (${allow.description})` : ''}`, matched_rule: allow };
  return { ip, scope, allowed: false, reason: `This scope is in allow-list mode (${allows.length} allow rule${allows.length === 1 ? '' : 's'}) and the address matches none of them`, matched_rule: null };
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

registerMockHandlers('security', [
  // ── API keys ──
  {
    method: 'GET',
    path: '/api-keys',
    handle: (req) => {
      const search = (req.query.get('search') ?? '').toLowerCase();
      const status = req.query.get('status');
      const list = apiKeys
        .map((k) => withLiveStatus(k))
        .filter((k) => !search || k.name.toLowerCase().includes(search) || k.key_prefix.toLowerCase().includes(search) || (k.description ?? '').toLowerCase().includes(search))
        .filter((k) => !status || k.status === status);
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), '-created_at') as unknown as ApiKey[], req.query, 25);
    },
  },
  {
    method: 'GET',
    path: '/api-keys/stats',
    handle: () => {
      const list = apiKeys.map((k) => withLiveStatus(k));
      const count = (s: ApiKey['status']) => list.filter((k) => k.status === s).length;
      return ok({ total: list.length, active: count('active'), suspended: count('suspended'), expired: count('expired'), revoked: count('revoked') });
    },
  },
  {
    method: 'POST',
    path: '/api-keys',
    handle: (req) => {
      const body = bodyOf<Schemas['ApiKeyCreate']>(req);
      if (!body.name?.trim()) return fail(422, 'VALIDATION_ERROR', 'name is required', [{ field: 'name', message: 'Required' }]);
      if (apiKeys.some((k) => k.name.toLowerCase() === body.name?.trim().toLowerCase() && k.status !== 'revoked')) return fail(409, 'CONFLICT', `A key named "${body.name}" already exists`);
      if (body.expires_at && Date.parse(body.expires_at) <= Date.now()) return fail(422, 'VALIDATION_ERROR', 'expires_at must be in the future');
      const now = Date.now();
      const prefix = keyPrefix(`${body.name}:${now}`);
      const key: ApiKey = { id: uuid(`api-key:new:${body.name}:${now}`), name: body.name.trim(), description: body.description ?? null, key_prefix: prefix, status: 'active', total_requests: 0, expires_at: body.expires_at ?? null, last_used_at: null, last_rotated_at: null, revoked_at: null, metadata: body.metadata ?? {}, created_at: iso(now), updated_at: iso(now) };
      apiKeys.push(key);
      recordAudit({ eventType: 'api_key.created', resourceType: 'api_key', resourceId: key.id, resourceName: key.name, after: { name: key.name, key_prefix: prefix, expires_at: key.expires_at, status: 'active' } });
      return created({ ...key, key: secretFor(prefix, key.id) });
    },
  },
  {
    method: 'GET',
    path: '/api-keys/:key_id',
    handle: (req) => {
      const k = keyById(req.params.key_id);
      return k ? ok(withLiveStatus(k)) : notFound('API key');
    },
  },
  {
    method: 'PATCH',
    path: '/api-keys/:key_id',
    handle: (req) => {
      const k = keyById(req.params.key_id);
      if (!k) return notFound('API key');
      const body = bodyOf<Schemas['ApiKeyUpdate']>(req);
      const before = { name: k.name, description: k.description, expires_at: k.expires_at };
      Object.assign(k, body, { updated_at: iso(Date.now()) });
      if (k.status === 'expired' && (!k.expires_at || Date.parse(k.expires_at) > Date.now())) k.status = 'active';
      recordAudit({ eventType: 'api_key.updated', resourceType: 'api_key', resourceId: k.id, resourceName: k.name, before, after: { name: k.name, description: k.description, expires_at: k.expires_at } });
      return ok(withLiveStatus(k));
    },
  },
  {
    method: 'DELETE',
    path: '/api-keys/:key_id',
    handle: (req) => {
      const idx = apiKeys.findIndex((k) => k.id === req.params.key_id);
      if (idx < 0) return notFound('API key');
      const [k] = apiKeys.splice(idx, 1);
      recordAudit({ eventType: 'api_key.deleted', resourceType: 'api_key', resourceId: k.id, resourceName: k.name, before: { status: k.status } });
      return noContent();
    },
  },
  {
    method: 'POST',
    path: '/api-keys/:key_id/revoke',
    handle: (req) => {
      const k = keyById(req.params.key_id);
      if (!k) return notFound('API key');
      if (k.status === 'revoked') return fail(409, 'CONFLICT', 'Key is already revoked');
      setKeyStatus(k, 'revoked', 'api_key.revoked');
      return ok(k);
    },
  },
  {
    method: 'POST',
    path: '/api-keys/:key_id/suspend',
    handle: (req) => {
      const k = keyById(req.params.key_id);
      if (!k) return notFound('API key');
      if (k.status !== 'active') return fail(409, 'CONFLICT', `Only active keys can be suspended (this one is ${k.status})`);
      setKeyStatus(k, 'suspended', 'api_key.suspended');
      return ok(k);
    },
  },
  {
    method: 'POST',
    path: '/api-keys/:key_id/activate',
    handle: (req) => {
      const k = keyById(req.params.key_id);
      if (!k) return notFound('API key');
      if (k.status === 'revoked') return fail(409, 'CONFLICT', 'Revoked keys cannot be re-activated — create a new key');
      if (k.expires_at && Date.parse(k.expires_at) <= Date.now()) return fail(409, 'CONFLICT', 'Key has expired — extend the expiry first');
      setKeyStatus(k, 'active', 'api_key.activated');
      return ok(k);
    },
  },
  {
    method: 'POST',
    path: '/api-keys/:key_id/rotate',
    handle: (req) => {
      const k = keyById(req.params.key_id);
      if (!k) return notFound('API key');
      if (k.status === 'revoked') return fail(409, 'CONFLICT', 'Revoked keys cannot be rotated');
      const now = Date.now();
      const before = { key_prefix: k.key_prefix };
      k.key_prefix = keyPrefix(`${k.id}:${now}`);
      k.last_rotated_at = iso(now);
      k.updated_at = iso(now);
      recordAudit({ eventType: 'api_key.rotated', resourceType: 'api_key', resourceId: k.id, resourceName: k.name, before, after: { key_prefix: k.key_prefix, last_rotated_at: k.last_rotated_at }, message: 'Old secret invalidated immediately' });
      return ok({ ...k, key: secretFor(k.key_prefix, `${k.id}:${now}`) });
    },
  },

  // ── Audit ──
  { method: 'GET', path: '/audit', handle: (req) => paginate(filterAudit(req.query), req.query, 50) },
  {
    method: 'GET',
    path: '/audit/export',
    handle: (req) => {
      const rows = filterAudit(req.query).map((a) => [a.created_at, a.event_type, a.outcome, a.actor_type, a.actor_name, a.resource_type, a.resource_name, a.resource_id, a.ip_address, a.request_id, a.message, a.before_state, a.after_state]);
      return csvResponse([['created_at', 'event_type', 'outcome', 'actor_type', 'actor_name', 'resource_type', 'resource_name', 'resource_id', 'ip_address', 'request_id', 'message', 'before_state', 'after_state'], ...rows], `gpu-deck-audit-${new Date().toISOString().slice(0, 10)}.csv`);
    },
  },
  {
    method: 'GET',
    path: '/audit/:log_id',
    handle: (req) => {
      const a = audit.find((x) => x.id === req.params.log_id);
      return a ? ok(a) : notFound('Audit entry');
    },
  },

  // ── IP rules ──
  {
    method: 'GET',
    path: '/ip-rules',
    handle: (req) => {
      const scope = req.query.get('scope');
      const action = req.query.get('action');
      const enabled = req.query.get('enabled');
      const list = liveIpRules().filter((r) => (!scope || r.scope === scope) && (!action || r.action === action) && (enabled === null || String(r.enabled) === enabled));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), 'created_at') as unknown as IpRule[], req.query, 500);
    },
  },
  {
    method: 'POST',
    path: '/ip-rules',
    handle: (req) => {
      const body = bodyOf<Schemas['IpRuleCreate']>(req);
      const parsed = body.cidr ? parseCidr(body.cidr) : null;
      if (!parsed) return fail(422, 'VALIDATION_ERROR', 'cidr: not a valid IPv4 / IPv6 address or range', [{ field: 'cidr', message: 'Invalid CIDR' }]);
      if (!body.scope || !body.action) return fail(422, 'VALIDATION_ERROR', 'scope and action are required');
      if (ipRules.some((r) => r.scope === body.scope && r.action === body.action && r.cidr === parsed.canonical)) return fail(409, 'CONFLICT', `${parsed.canonical} already has a ${body.action} rule for this scope`);
      const now = Date.now();
      const rule: IpRule = { id: uuid(`ip:new:${parsed.canonical}:${now}`), scope: body.scope, action: body.action, cidr: parsed.canonical, description: body.description ?? null, enabled: body.enabled ?? true, expires_at: body.expires_at ?? null, created_at: iso(now), updated_at: iso(now) };
      ipRules.push(rule);
      recordAudit({ eventType: 'ip_rule.created', resourceType: 'ip_rule', resourceId: rule.id, resourceName: rule.cidr, after: { scope: rule.scope, action: rule.action, cidr: rule.cidr, enabled: rule.enabled } });
      return created(rule);
    },
  },
  {
    method: 'POST',
    path: '/ip-rules/check',
    handle: (req) => {
      const body = bodyOf<{ ip: string; scope: IpRule['scope'] }>(req);
      if (!body.ip || !parseIp(body.ip)) return fail(422, 'VALIDATION_ERROR', 'ip: not a valid IPv4 / IPv6 address');
      return ok(checkIp(body.ip.trim(), body.scope ?? 'admin'));
    },
  },
  {
    method: 'PATCH',
    path: '/ip-rules/:rule_id',
    handle: (req) => {
      const r = ipRules.find((x) => x.id === req.params.rule_id);
      if (!r) return notFound('IP rule');
      const body = bodyOf<Schemas['IpRuleUpdate']>(req);
      if (body.cidr !== undefined) {
        const parsed = parseCidr(body.cidr);
        if (!parsed) return fail(422, 'VALIDATION_ERROR', 'cidr: not a valid IPv4 / IPv6 address or range');
        body.cidr = parsed.canonical;
      }
      const before = { scope: r.scope, action: r.action, cidr: r.cidr, enabled: r.enabled, expires_at: r.expires_at };
      Object.assign(r, body, { updated_at: iso(Date.now()) });
      recordAudit({ eventType: 'ip_rule.updated', resourceType: 'ip_rule', resourceId: r.id, resourceName: r.cidr, before, after: { scope: r.scope, action: r.action, cidr: r.cidr, enabled: r.enabled, expires_at: r.expires_at } });
      return ok(r);
    },
  },
  {
    method: 'DELETE',
    path: '/ip-rules/:rule_id',
    handle: (req) => {
      const idx = ipRules.findIndex((x) => x.id === req.params.rule_id);
      if (idx < 0) return notFound('IP rule');
      const [r] = ipRules.splice(idx, 1);
      recordAudit({ eventType: 'ip_rule.deleted', resourceType: 'ip_rule', resourceId: r.id, resourceName: r.cidr, before: { scope: r.scope, action: r.action, cidr: r.cidr } });
      return noContent();
    },
  },
]);
