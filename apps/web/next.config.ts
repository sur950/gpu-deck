import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { NextConfig } from 'next';

// Content hash of the default icons, appended as ?v= to their URLs so browsers refetch the icon
// after a brand change (favicons are cached very aggressively).
const iconVersion = createHash('sha256')
  .update(
    Buffer.concat(
      ['favicon.ico', 'favicon.svg', 'apple-touch-icon.png'].map((name) =>
        readFileSync(path.join(__dirname, 'public', name)),
      ),
    ),
  )
  .digest('hex')
  .slice(0, 10);

const nextConfig: NextConfig = {
  // The control plane ships as a Docker container: `standalone` emits
  // .next/standalone/server.js plus the minimal node_modules it needs.
  output: 'standalone',
  env: { NEXT_PUBLIC_ICON_VERSION: iconVersion },
  reactCompiler: false,
  // Local dev can also be served over HTTPS at gpu-deck.localhost:58443 (the
  // optional Caddy proxy in infra/docker/docker-compose.deps.yml → 3180). Next
  // only trusts localhost for dev resources, so without this it refuses HMR.
  allowedDevOrigins: ['gpu-deck.localhost'],
  images: {
    // A custom NEXT_PUBLIC_LOGO_URL renders with `unoptimized`, so no remote
    // patterns are needed for branding.
    remotePatterns: [],
  },
};

export default nextConfig;
