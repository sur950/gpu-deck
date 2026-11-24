/**
 * Security API layer — API keys, audit log and IP allow / block rules.
 * Backend prefix /api/v1 (prepended by lib/api/client); snake_case → camelCase.
 */

import { apiDelete, apiGet, apiPatch, apiPost, toQuery } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';
import { pagedGet, type Paged } from '@/hooks/api/automation-api';

type S = components['schemas'];

// ─── API keys ─────────────────────────────────────────────────────────────────

export type ApiKeyStatus = S['api_key_status'];

export interface ApiKey {
  id: string;
  name: string;
  description: string | null;
  keyPrefix: string;
  status: ApiKeyStatus;
  totalRequests: number;
  expiresAt: string | null;
  lastUsedAt: string | null;
  lastRotatedAt: string | null;
  revokedAt: string | null;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string | null;
}

/** The create / rotate response — `key` is the plaintext, shown exactly once. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface ApiKeyStats {
  total: number;
  active: number;
  suspended: number;
  expired: number;
  revoked: number;
}

export type ApiKeyInput = S['ApiKeyCreate'];
export type ApiKeyUpdateInput = S['ApiKeyUpdate'];

export interface ApiKeyListParams {
  page?: number;
  pageSize?: number;
  search?: string;
  sort?: string;
  status?: ApiKeyStatus | '';
}

export function mapApiKey(k: S['ApiKey']): ApiKey {
  return {
    id: k.id,
    name: k.name,
    description: k.description ?? null,
    keyPrefix: k.key_prefix,
    status: k.status,
    totalRequests: k.total_requests ?? 0,
    expiresAt: k.expires_at ?? null,
    lastUsedAt: k.last_used_at ?? null,
    lastRotatedAt: k.last_rotated_at ?? null,
    revokedAt: k.revoked_at ?? null,
    metadata: k.metadata ?? {},
    createdAt: k.created_at,
    updatedAt: k.updated_at ?? null,
  };
}

function mapCreated(k: S['ApiKeyCreated']): ApiKeyCreated {
  return { ...mapApiKey(k), key: k.key };
}

export function listApiKeys(params: ApiKeyListParams = {}): Promise<Paged<ApiKey>> {
  return pagedGet<S['ApiKey'], ApiKey>(
    `/api-keys${toQuery({ page: params.page, page_size: params.pageSize, search: params.search, sort: params.sort, status: params.status || undefined })}`,
    mapApiKey,
  );
}

export async function getApiKeyStats(): Promise<ApiKeyStats> {
  const s = await apiGet<S['ApiKeyStats']>('/api-keys/stats');
  return { total: s.total ?? 0, active: s.active ?? 0, suspended: s.suspended ?? 0, expired: s.expired ?? 0, revoked: s.revoked ?? 0 };
}

export async function getApiKey(id: string): Promise<ApiKey> {
  return mapApiKey(await apiGet<S['ApiKey']>(`/api-keys/${id}`));
}

export async function createApiKey(body: ApiKeyInput): Promise<ApiKeyCreated> {
  return mapCreated(await apiPost<S['ApiKeyCreated']>('/api-keys', body));
}

export async function updateApiKey(id: string, body: ApiKeyUpdateInput): Promise<ApiKey> {
  return mapApiKey(await apiPatch<S['ApiKey']>(`/api-keys/${id}`, body));
}

export function deleteApiKey(id: string): Promise<void> {
  return apiDelete<void>(`/api-keys/${id}`);
}

export async function revokeApiKey(id: string): Promise<ApiKey> {
  return mapApiKey(await apiPost<S['ApiKey']>(`/api-keys/${id}/revoke`));
}

export async function suspendApiKey(id: string): Promise<ApiKey> {
  return mapApiKey(await apiPost<S['ApiKey']>(`/api-keys/${id}/suspend`));
}

export async function activateApiKey(id: string): Promise<ApiKey> {
  return mapApiKey(await apiPost<S['ApiKey']>(`/api-keys/${id}/activate`));
}

export async function rotateApiKey(id: string): Promise<ApiKeyCreated> {
  return mapCreated(await apiPost<S['ApiKeyCreated']>(`/api-keys/${id}/rotate`));
}

// ─── Audit log ────────────────────────────────────────────────────────────────

export type AuditActorType = S['audit_actor_type'];
export type AuditOutcome = S['audit_outcome'];

export interface AuditLog {
  id: string;
  createdAt: string;
  eventType: string;
  actorType: AuditActorType;
  actorId: string | null;
  actorName: string | null;
  resourceType: string | null;
  resourceId: string | null;
  resourceName: string | null;
  outcome: AuditOutcome;
  ipAddress: string | null;
  userAgent: string | null;
  requestId: string | null;
  message: string | null;
  beforeState: Record<string, unknown> | null;
  afterState: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
}

export interface AuditListParams {
  page?: number;
  pageSize?: number;
  search?: string;
  sort?: string;
  actorType?: AuditActorType | '';
  actorId?: string;
  eventType?: string;
  resourceType?: string;
  resourceId?: string;
  requestId?: string;
  outcome?: AuditOutcome | '';
  from?: string;
  to?: string;
}

export function mapAuditLog(a: S['AuditLog']): AuditLog {
  return {
    id: a.id,
    createdAt: a.created_at,
    eventType: a.event_type,
    actorType: a.actor_type,
    actorId: a.actor_id ?? null,
    actorName: a.actor_name ?? null,
    resourceType: a.resource_type ?? null,
    resourceId: a.resource_id ?? null,
    resourceName: a.resource_name ?? null,
    outcome: a.outcome,
    ipAddress: a.ip_address ?? null,
    userAgent: a.user_agent ?? null,
    requestId: a.request_id ?? null,
    message: a.message ?? null,
    beforeState: a.before_state ?? null,
    afterState: a.after_state ?? null,
    metadata: a.metadata ?? {},
  };
}

function auditQuery(params: AuditListParams, extra: Record<string, string | number | boolean | null | undefined> = {}): string {
  return toQuery({
    page: params.page,
    page_size: params.pageSize,
    search: params.search,
    sort: params.sort,
    actor_type: params.actorType || undefined,
    actor_id: params.actorId,
    event_type: params.eventType,
    resource_type: params.resourceType,
    resource_id: params.resourceId,
    request_id: params.requestId,
    outcome: params.outcome || undefined,
    from: params.from,
    to: params.to,
    ...extra,
  });
}

export function listAuditLogs(params: AuditListParams = {}): Promise<Paged<AuditLog>> {
  return pagedGet<S['AuditLog'], AuditLog>(`/audit${auditQuery(params)}`, mapAuditLog);
}

export async function getAuditLog(id: string): Promise<AuditLog> {
  return mapAuditLog(await apiGet<S['AuditLog']>(`/audit/${id}`));
}

/** CSV export path for the current filter (server streams the attachment). */
export function auditExportPath(params: AuditListParams): string {
  return `/audit/export${toQuery({
    actor_type: params.actorType || undefined,
    event_type: params.eventType,
    resource_type: params.resourceType,
    from: params.from,
    to: params.to,
  })}`;
}

// ─── IP rules ─────────────────────────────────────────────────────────────────

export type IpRuleAction = S['ip_rule_action'];
export type IpRuleScope = S['ip_rule_scope'];

export interface IpRule {
  id: string;
  scope: IpRuleScope;
  action: IpRuleAction;
  cidr: string;
  description: string | null;
  enabled: boolean;
  expiresAt: string | null;
  createdAt: string;
  updatedAt: string | null;
}

export type IpRuleInput = S['IpRuleCreate'];
export type IpRuleUpdateInput = S['IpRuleUpdate'];

export interface IpCheckResult {
  ip: string;
  scope: IpRuleScope;
  allowed: boolean;
  reason: string;
  matchedRule: IpRule | null;
}

export function mapIpRule(r: S['IpRule']): IpRule {
  return {
    id: r.id,
    scope: r.scope,
    action: r.action,
    cidr: r.cidr,
    description: r.description ?? null,
    enabled: r.enabled,
    expiresAt: r.expires_at ?? null,
    createdAt: r.created_at,
    updatedAt: r.updated_at ?? null,
  };
}

export function listIpRules(params: { scope?: IpRuleScope | ''; action?: IpRuleAction | ''; enabled?: boolean | null; page?: number; pageSize?: number; sort?: string } = {}): Promise<Paged<IpRule>> {
  return pagedGet<S['IpRule'], IpRule>(
    `/ip-rules${toQuery({
      scope: params.scope || undefined,
      action: params.action || undefined,
      enabled: params.enabled === null || params.enabled === undefined ? undefined : params.enabled,
      page: params.page,
      page_size: params.pageSize ?? 500,
      sort: params.sort,
    })}`,
    mapIpRule,
  );
}

export async function createIpRule(body: IpRuleInput): Promise<IpRule> {
  return mapIpRule(await apiPost<S['IpRule']>('/ip-rules', body));
}

export async function updateIpRule(id: string, body: IpRuleUpdateInput): Promise<IpRule> {
  return mapIpRule(await apiPatch<S['IpRule']>(`/ip-rules/${id}`, body));
}

export function deleteIpRule(id: string): Promise<void> {
  return apiDelete<void>(`/ip-rules/${id}`);
}

export async function checkIp(body: { ip: string; scope: IpRuleScope }): Promise<IpCheckResult> {
  const r = await apiPost<S['IpCheckResult']>('/ip-rules/check', body);
  return { ip: r.ip, scope: r.scope, allowed: r.allowed, reason: r.reason, matchedRule: r.matched_rule ? mapIpRule(r.matched_rule) : null };
}
