/**
 * E2E environment: where the stack is and how to sign in.
 *
 * Credentials come from E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD, falling back
 * to ADMIN_USERNAME / ADMIN_PASSWORD in apps/fastapi/.env (the bootstrap
 * admin of a local dev stack). Nothing is written back.
 */

import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

function readDotenv(path: string): Record<string, string> {
  if (!existsSync(path)) return {};
  const out: Record<string, string> = {};
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
    if (!m) continue;
    out[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
  }
  return out;
}

const backendEnv = readDotenv(resolve(__dirname, '../../../fastapi/.env'));

export const E2E = {
  baseUrl: process.env.E2E_BASE_URL ?? 'http://localhost:3180',
  apiUrl: (process.env.E2E_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8180/api/v1').replace(/\/$/, ''),
  username: process.env.E2E_ADMIN_USERNAME ?? backendEnv.ADMIN_USERNAME ?? 'admin',
  password: process.env.E2E_ADMIN_PASSWORD ?? backendEnv.ADMIN_PASSWORD ?? '',
  csrfCookie: process.env.NEXT_PUBLIC_CSRF_COOKIE ?? 'gpudeck_csrf',
  sessionCookie: process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'gpudeck_session',
  authFile: resolve(__dirname, '../.auth/admin.json'),
  screenshotDir: resolve(__dirname, '../screenshots'),
};

/** Unique, recognisable names for records the suite creates. */
export function e2eName(kind: string): string {
  return `e2e-${kind}-${Date.now().toString(36)}`;
}
