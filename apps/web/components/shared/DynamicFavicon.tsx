'use client';

import { useEffect } from 'react';
import { DEFAULT_FAVICON, FAVICON_URL } from '@/utils/brand-env';

function setFaviconHref(href: string) {
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = href;
}

/**
 * Applies NEXT_PUBLIC_FAVICON_URL, probing it first so a broken URL falls
 * back to the GPU Deck mark instead of a blank tab icon.
 */
export function DynamicFavicon() {
  useEffect(() => {
    const url = FAVICON_URL;

    if (url === DEFAULT_FAVICON) {
      setFaviconHref(DEFAULT_FAVICON);
      return;
    }

    // Probe the URL — fall back to default if it fails to load
    const img = new window.Image();
    img.onload  = () => setFaviconHref(url);
    img.onerror = () => setFaviconHref(DEFAULT_FAVICON);
    img.src = url;
  }, []);

  return null;
}
