import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // The control plane ships as a Docker container: `standalone` emits
  // .next/standalone/server.js plus the minimal node_modules it needs.
  output: 'standalone',
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
