#!/usr/bin/env node
/**
 * Writes src-tauri/tauri.updater.conf.json, a config overlay that turns the
 * auto-updater on for a release build:
 *
 *   GPUDECK_UPDATER_ENDPOINTS  comma-separated https URLs of the update manifest
 *                              (may use {{target}}, {{arch}}, {{current_version}})
 *   GPUDECK_UPDATER_PUBKEY     minisign public key (contents of the .key.pub file)
 *
 * Then: pnpm --filter desktop tauri build --config src-tauri/tauri.updater.conf.json
 * with TAURI_SIGNING_PRIVATE_KEY (+ _PASSWORD) set so the bundler signs the
 * update artifacts. Without this overlay the app ships with the updater off.
 */
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const endpoints = (process.env.GPUDECK_UPDATER_ENDPOINTS ?? '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);
const pubkey = (process.env.GPUDECK_UPDATER_PUBKEY ?? '').trim();

if (!endpoints.length || !pubkey) {
  console.error(
    'GPUDECK_UPDATER_ENDPOINTS and GPUDECK_UPDATER_PUBKEY must both be set; updater stays disabled.',
  );
  process.exit(1);
}
for (const e of endpoints) {
  if (!e.startsWith('https://')) {
    console.error(`Update endpoints must be https: ${e}`);
    process.exit(1);
  }
}
if (!process.env.TAURI_SIGNING_PRIVATE_KEY) {
  console.warn(
    'warning: TAURI_SIGNING_PRIVATE_KEY is not set; `tauri build` will fail to sign update artifacts.',
  );
}

const overlay = {
  bundle: { createUpdaterArtifacts: true },
  plugins: {
    updater: { pubkey, endpoints, windows: { installMode: 'passive' } },
  },
};
const out = fileURLToPath(
  new URL('../src-tauri/tauri.updater.conf.json', import.meta.url),
);
writeFileSync(out, `${JSON.stringify(overlay, null, 2)}\n`);
console.log(`wrote ${out} (${endpoints.length} endpoint(s))`);
