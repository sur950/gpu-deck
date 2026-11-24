export type BackendStatus = 'unknown' | 'healthy' | 'degraded' | 'down';

export interface ServiceCheckResult {
  status: 'healthy' | 'degraded' | 'unhealthy' | string;
  response_time_ms?: number;
  error?: string | null;
}

export interface SystemResources {
  cpu_percent?: number;
  memory_percent?: number;
  disk_percent?: number;
  memory_available_mb?: number;
  disk_available_gb?: number;
}

export interface HealthChecks {
  database?: ServiceCheckResult;
  redis?: ServiceCheckResult;
  storage?: ServiceCheckResult;
  system?: SystemResources;
}

export interface HealthData {
  status: BackendStatus;
  version?: string;
  checks?: HealthChecks;
}
