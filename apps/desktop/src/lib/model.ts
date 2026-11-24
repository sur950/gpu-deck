/**
 * Connect-screen types (mirroring the Rust command payloads) and the pure
 * helpers behind the server list and form: sorting, relative time, health
 * tone, form validation.
 */

import { normalizeServerUrl } from './url';

export interface Server {
  id: string;
  name: string;
  url: string;
  apiUrl?: string;
  createdAt: number;
  lastUsedAt?: number;
  insecure: boolean;
}

export interface ServersView {
  servers: Server[];
  lastServerId: string | null;
  currentServerId: string | null;
  appVersion: string;
  platform: string;
}

export interface ServerInput {
  id?: string;
  name: string;
  url: string;
  apiUrl?: string;
}

export interface ProbeResult {
  origin: string;
  apiBase: string;
  insecure: boolean;
  isGpuDeck: boolean;
  branding: { appName: string; appByline: string; brandColor: string } | null;
  health: {
    status: string;
    version: string | null;
    checks: { name: string; status: string }[];
  } | null;
  error: string | null;
  latencyMs: number;
}

export interface Prefill {
  name?: string;
  url?: string;
  path?: string;
  reason: string;
}

export type Tone = 'ok' | 'warn' | 'down' | 'unknown' | 'checking';

export function sortServers(servers: Server[]): Server[] {
  return [...servers].sort(
    (a, b) =>
      (b.lastUsedAt ?? 0) - (a.lastUsedAt ?? 0) ||
      a.name.toLowerCase().localeCompare(b.name.toLowerCase()),
  );
}

export function relativeTime(ms: number | undefined, now = Date.now()): string {
  if (!ms) return 'Never used';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 45) return 'Used just now';
  const m = Math.round(s / 60);
  if (m < 60) return `Used ${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `Used ${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `Used ${d} day${d === 1 ? '' : 's'} ago`;
  return `Used ${new Date(ms).toLocaleDateString()}`;
}

export function toneOf(probe: ProbeResult | undefined | null): Tone {
  if (!probe) return 'unknown';
  if (!probe.isGpuDeck) return 'down';
  switch (probe.health?.status) {
    case 'healthy':
      return 'ok';
    case 'degraded':
      return 'warn';
    case 'unhealthy':
      return 'down';
    default:
      return 'warn';
  }
}

export function toneLabel(tone: Tone): string {
  return {
    ok: 'Healthy',
    warn: 'Degraded',
    down: 'Unreachable',
    unknown: 'Not checked',
    checking: 'Checking…',
  }[tone];
}

export function checkTone(status: string): Tone {
  if (status === 'healthy' || status === 'ok' || status === 'up') return 'ok';
  if (status === 'unhealthy' || status === 'down' || status === 'error')
    return 'down';
  if (status === 'disabled' || status === 'skipped' || status === 'unknown')
    return 'unknown';
  return 'warn';
}

export function checkLabel(name: string): string {
  const known: Record<string, string> = {
    database: 'Database',
    redis: 'Redis',
    celery: 'Workers',
    agent_hub: 'Agent hub',
    jobs_leader: 'Scheduler',
    api_layer: 'Serving API',
  };
  return (
    known[name] ??
    name.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
  );
}

export interface FormErrors {
  name?: string;
  url?: string;
  apiUrl?: string;
}

export interface FormCheck {
  errors: FormErrors;
  insecure: boolean;
  origin?: string;
}

/** Client-side validation of the add / edit form (the backend re-validates). */
export function validateForm(
  input: ServerInput,
  existing: Server[],
): FormCheck {
  const errors: FormErrors = {};
  const name = input.name.trim();
  if (!name) errors.name = 'Give the server a name.';
  else if ([...name].length > 64)
    errors.name = 'Names are limited to 64 characters.';
  else if (/[\u0000-\u001f\u007f]/.test(name))
    errors.name = 'Names cannot contain control characters.';
  const url = normalizeServerUrl(input.url);
  let insecure = false;
  let origin: string | undefined;
  if (!url.ok) errors.url = url.error;
  else {
    insecure = url.insecure;
    origin = url.origin;
  }
  if (input.apiUrl?.trim()) {
    const api = normalizeServerUrl(input.apiUrl);
    if (!api.ok) errors.apiUrl = api.error;
  }
  for (const other of existing) {
    if (other.id === input.id) continue;
    if (!errors.name && other.name.toLowerCase() === name.toLowerCase()) {
      errors.name = `A server named "${other.name}" already exists.`;
    }
    if (!errors.url && origin && other.url === origin) {
      errors.url = `This URL is already saved as "${other.name}".`;
    }
  }
  return { errors, insecure, origin };
}

export function hasErrors(e: FormErrors): boolean {
  return Boolean(e.name || e.url || e.apiUrl);
}

/** Dev convenience: the Next dev server (3180) talks to the admin API on 8180. */
export function suggestApiUrl(origin: string | undefined): string | null {
  if (!origin) return null;
  const m = /^http:\/\/(localhost|127\.0\.0\.1):3180$/.exec(origin);
  return m ? `http://${m[1]}:8180` : null;
}
