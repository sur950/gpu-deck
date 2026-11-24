import type { Theme } from '@/types/org';

/**
 * Shipped brand defaults — GPU Deck's crimson family, shared with Container
 * Deck (docs/BRAND.md).
 *
 * Used whenever no branding is configured. NEXT_PUBLIC_BRAND_COLOR still wins
 * at runtime via applyTheme(). Keep in sync with the CSS values in
 * app/globals.css.
 */

/**
 * The crimson accent on light surfaces and the canonical brand hex (the
 * default of BRAND_COLOR / NEXT_PUBLIC_BRAND_COLOR). With the default the web
 * app uses the per-mode steps in globals.css (this on light, the dark step
 * below on dark); a custom colour is used as-is in both modes.
 */
export const DEFAULT_PRIMARY_COLOR = '#B3122E';

/** The crimson's dark-mode step (fills, markers); text and icons use #F2687B. */
export const DEFAULT_PRIMARY_COLOR_DARK = '#D3263F';

/**
 * Status colours — deliberately not the accent, so they stay readable as
 * states: the error is a vermilion (hue ~37°), clearly apart from the crimson
 * (~21°), and the warning a golden amber, apart from the error.
 */
export const DEFAULT_SEMANTIC_COLORS: Omit<Theme['colors'], 'neutral'> = {
  success: { light: '#34a873', default: '#1f8a5b', dark: '#146b45' },
  warning: { light: '#e8b83a', default: '#c29100', dark: '#8f6a00' },
  error:   { light: '#f5713a', default: '#dc4e22', dark: '#a8381a' },
  info:    { light: '#7b84dc', default: '#5560d0', dark: '#3f4aa8' },
};

/** Neutral ramp — Container Deck's warm greys, off-white to near-black. */
export const DEFAULT_NEUTRAL_COLORS: Theme['colors']['neutral'] = {
  50: '#f4f3f0', 100: '#eceae5', 200: '#e2e0da', 300: '#d5d2cb',
  400: '#a1a3ab', 500: '#5b5e66', 600: '#45474e', 700: '#2a2b30',
  800: '#1b1c20', 900: '#111215',
};

export const DEFAULT_THEME_COLORS: Theme['colors'] = {
  ...DEFAULT_SEMANTIC_COLORS,
  neutral: DEFAULT_NEUTRAL_COLORS,
};
