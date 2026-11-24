/**
 * The connect screen's only way to reach native code: the seven commands
 * granted to the local `connect` window (src-tauri/capabilities/connect.json)
 * and two events. Behind an interface so tests can supply a fake.
 */

import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import type {
  Prefill,
  ProbeResult,
  Server,
  ServerInput,
  ServersView,
} from './model';

export interface Backend {
  list(): Promise<ServersView>;
  save(input: ServerInput): Promise<Server>;
  remove(id: string): Promise<void>;
  probe(url: string, apiUrl?: string): Promise<ProbeResult>;
  connect(id: string, path?: string): Promise<void>;
  prefill(): Promise<Prefill | null>;
  back(): Promise<boolean>;
  onPrefill(cb: (p: Prefill) => void): void;
  onServersChanged(cb: () => void): void;
}

/** Tauri errors arrive as plain strings; keep their message. */
export function errorMessage(e: unknown): string {
  if (typeof e === 'string') return e;
  if (e instanceof Error) return e.message;
  return 'Something went wrong.';
}

export const tauriBackend: Backend = {
  list: () => invoke<ServersView>('servers_list'),
  save: (input) => invoke<Server>('servers_save', { input }),
  remove: (id) => invoke<void>('servers_remove', { id }),
  probe: (url, apiUrl) =>
    invoke<ProbeResult>('servers_probe', { url, apiUrl: apiUrl || null }),
  connect: (id, path) =>
    invoke<void>('servers_connect', { id, path: path ?? null }),
  prefill: () => invoke<Prefill | null>('connect_prefill'),
  back: () => invoke<boolean>('connect_back'),
  onPrefill: (cb) => {
    void listen<Prefill>('gpudeck://prefill', (e) => cb(e.payload));
  },
  onServersChanged: (cb) => {
    void listen('gpudeck://servers-changed', () => cb());
  },
};
