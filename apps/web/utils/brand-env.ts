/**
 * Branding from the environment.
 *
 * GPU Deck has no branding settings UI: the consumers (Logo,
 * ThemeProvider → applyTheme, DynamicFavicon, root metadata, manifest) read
 * these NEXT_PUBLIC_* values. They are baked in at build time. Defaults are
 * the GPU Deck brand (docs/BRAND.md).
 */

import type { Theme } from '@/types/org';
import { DEFAULT_PRIMARY_COLOR, DEFAULT_THEME_COLORS } from '@/utils/brand-defaults';

function env(value: string | undefined, fallback: string): string {
  const trimmed = value?.trim();
  return trimmed ? trimmed : fallback;
}

/** Product name shown in the logo lockup, page titles and the sign-in card. */
export const APP_NAME = env(process.env.NEXT_PUBLIC_APP_NAME, 'GPU Deck');

/** Descriptor line under the product name in the lockup. */
export const APP_BYLINE = env(process.env.NEXT_PUBLIC_APP_BYLINE, 'GPU control plane');

/** Brand accent as a hex colour — written to `--brand` by applyTheme(). */
export const BRAND_COLOR = env(process.env.NEXT_PUBLIC_BRAND_COLOR, DEFAULT_PRIMARY_COLOR);

/** Optional custom logo URL; empty means the GPU Deck lockup. */
export const LOGO_URL = env(process.env.NEXT_PUBLIC_LOGO_URL, '');

/** Favicon URL; defaults to the GPU Deck mark (16/32/48 px, public/favicon.ico). */
export const DEFAULT_FAVICON = '/favicon.ico';
export const FAVICON_URL = env(process.env.NEXT_PUBLIC_FAVICON_URL, DEFAULT_FAVICON);

/** Colour mode a browser opens in before the user toggles it. */
export const DEFAULT_COLOR_MODE: 'dark' | 'light' =
  process.env.NEXT_PUBLIC_DEFAULT_COLOR_MODE?.trim().toLowerCase() === 'light' ? 'light' : 'dark';

/** Document title, e.g. "GPU Deck · GPU control plane". */
export const APP_TITLE = APP_BYLINE ? `${APP_NAME} · ${APP_BYLINE}` : APP_NAME;

/**
 * The runtime theme. Typography and radius are left undefined so the CSS
 * fallbacks in globals.css (Satoshi, JetBrains Mono, 0.5rem) apply.
 */
export function buildThemeFromEnv(): Theme {
  return {
    orgName: APP_NAME,
    logo: LOGO_URL || '/logo.svg',
    primaryColor: BRAND_COLOR,
    sidebarStyle: 'dark',
    whiteLabel: LOGO_URL !== '',
    colors: DEFAULT_THEME_COLORS,
  };
}
