/**
 * Logs API: cursor-paginated history (`GET /logs`), the export URL and the
 * SSE tail URL (`GET /logs/stream`, consumed by hooks/use-log-stream.ts).
 */

import { apiGet, toQuery } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';
import { getGatewayUrl } from '@/utils/gateway-url';
import type { LogChunk, LogPage, LogQuery } from '@/types/logs';

type Schemas = components['schemas'];

export function mapLogChunk(row: Schemas['LogChunk']): LogChunk {
  return {
    id: row.id,
    ts: row.ts,
    tsEnd: row.ts_end ?? null,
    nodeId: row.node_id ?? null,
    nodeName: row.node_name ?? null,
    instanceId: row.instance_id ?? null,
    deploymentId: row.deployment_id ?? null,
    source: row.source,
    stream: row.stream,
    level: row.level,
    logger: row.logger ?? null,
    requestId: row.request_id ?? null,
    lineCount: row.line_count,
    content: row.content,
    attributes: row.attributes ?? null,
  };
}

function logQueryString(query: LogQuery): string {
  return toQuery({
    from: query.from,
    to: query.to,
    node_id: query.nodeId,
    instance_id: query.instanceId,
    deployment_id: query.deploymentId,
    source: query.sources && query.sources.length > 0 ? query.sources.join(',') : undefined,
    level: query.level,
    stream: query.stream,
    request_id: query.requestId,
    search: query.search,
    cursor: query.cursor,
    limit: query.limit,
  });
}

export async function getLogs(query: LogQuery): Promise<LogPage> {
  const row = await apiGet<Schemas['LogPage']>(`/logs${logQueryString(query)}`);
  return { items: row.items.map(mapLogChunk), nextCursor: row.next_cursor ?? null, hasMore: row.has_more };
}

/** Absolute URL of the SSE tail for the given filters. */
export function logStreamUrl(query: Pick<LogQuery, 'nodeId' | 'instanceId' | 'deploymentId' | 'sources' | 'level'>): string {
  return `${getGatewayUrl()}/logs/stream${logQueryString(query)}`;
}

/** Absolute URL of the plain-text export (opened in a new tab). */
export function logExportUrl(query: Pick<LogQuery, 'from' | 'to' | 'nodeId' | 'instanceId' | 'sources' | 'level'>): string {
  return `${getGatewayUrl()}/logs/export${logQueryString(query)}`;
}
