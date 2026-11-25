import type { MetadataRoute } from 'next';
import { APP_NAME, APP_TITLE } from '@/utils/brand-env';

/**
 * Web app manifest (/manifest.webmanifest): name and icons for "Install app"
 * and home-screen shortcuts. Icons are the GPU Deck mark (assets/brand).
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: APP_TITLE,
    short_name: APP_NAME,
    description: 'GPU control plane: clusters, models, rules and observability for your own hardware',
    start_url: '/dashboard',
    display: 'standalone',
    background_color: '#0E0F12',
    theme_color: '#0E0F12',
    icons: [
      { src: '/brand/gpudeck-icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/brand/gpudeck-icon-512.png', sizes: '512x512', type: 'image/png' },
      { src: '/brand/gpudeck-icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
}
