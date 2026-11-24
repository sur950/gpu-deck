/**
 * UI types for the engine / control-plane log viewer.
 */

import type { components } from '@/lib/api/generated/admin';

export type LogSource = components['schemas']['log_source'];
export type LogLevel = components['schemas']['log_level'];
export type LogStream = components['schemas']['log_stream'];

export const LOG_SOURCES: LogSource[] = ['engine', 'agent', 'control_plane', 'rule', 'swap', 'api_layer'];
export const LOG_LEVELS: LogLevel[] = ['debug', 'info', 'warn', 'error'];

export const LOG_SOURCE_LABELS: Record<LogSource, string> = {
  engine: 'Engine',
  agent: 'Agent',
  control_plane: 'Control plane',
  rule: 'Rules',
  swap: 'Swaps',
  api_layer: 'API layer',
};

export interface LogChunk {
  id: number;
  ts: string;
  tsEnd: string | null;
  nodeId: string | null;
  nodeName: string | null;
  instanceId: string | null;
  deploymentId: string | null;
  source: LogSource;
  stream: LogStream;
  level: LogLevel;
  logger: string | null;
  requestId: string | null;
  lineCount: number;
  content: string;
  attributes: Record<string, unknown> | null;
}

export interface LogPage {
  items: LogChunk[];
  nextCursor: string | null;
  hasMore: boolean;
}

export interface LogQuery {
  from?: string;
  to?: string;
  nodeId?: string;
  instanceId?: string;
  deploymentId?: string;
  sources?: LogSource[];
  level?: LogLevel;
  stream?: LogStream;
  requestId?: string;
  search?: string;
  cursor?: string;
  limit?: number;
}
