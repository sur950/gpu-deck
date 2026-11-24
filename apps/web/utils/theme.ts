import type { Theme } from '@/types/org';
import { DEFAULT_PRIMARY_COLOR } from '@/utils/brand-defaults';

/**
 * Convert hex color to CSS RGB values
 */
export function hexToRgb(hex: string): string {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return '179, 18, 46'; // DEFAULT_PRIMARY_COLOR (crimson)

  const r = parseInt(result[1], 16);
  const g = parseInt(result[2], 16);
  const b = parseInt(result[3], 16);

  return `${r}, ${g}, ${b}`;
}

/** Convert hex to space-separated RGB (matches shadcn/ui CSS var convention). */
function hexToRgbSpace(hex: string): string {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return '179 18 46'; // DEFAULT_PRIMARY_COLOR (crimson)
  return `${parseInt(result[1], 16)} ${parseInt(result[2], 16)} ${parseInt(result[3], 16)}`;
}

/** True when near-black text has more contrast on `hex` than white (WCAG luminance). */
export function prefersDarkText(hex: string): boolean {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return false;
  const lin = (i: number) => {
    const c = parseInt(result[i], 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const lum = 0.2126 * lin(1) + 0.7152 * lin(2) + 0.0722 * lin(3);
  // Contrast with white (1.05 / (L + 0.05)) vs with #111215 (L ~= 0.0063).
  return (lum + 0.05) / 0.0563 > 1.05 / (lum + 0.05);
}

/**
 * Apply theme to document root
 */
export function applyTheme(theme: Theme): void {
  if (typeof document === 'undefined') return;

  const root = document.documentElement;

  // The brand colour is the one accent: primary buttons, links, the active nav
  // marker, focus rings, selection and the first chart series. The default
  // (crimson) has per-mode steps in globals.css, so it is not written inline;
  // a custom colour is used as-is in both modes, with black or white text on
  // its fills, whichever reads better.
  const accentVars = ['--brand', '--brand-strong', '--brand-text', '--primary', '--ring'];
  if (theme.primaryColor.trim().toLowerCase() === DEFAULT_PRIMARY_COLOR.toLowerCase()) {
    for (const v of [...accentVars, '--brand-foreground', '--primary-foreground']) root.style.removeProperty(v);
  } else {
    const rgb = hexToRgbSpace(theme.primaryColor);
    for (const v of accentVars) root.style.setProperty(v, rgb);
    const onAccent = prefersDarkText(theme.primaryColor) ? '17 18 21' : '255 255 255';
    root.style.setProperty('--brand-foreground', onAccent);
    root.style.setProperty('--primary-foreground', onAccent);
  }

  // NOTE: org secondary/tertiary colours are deliberately NOT written to
  // --secondary / --accent. Those are neutral surface tokens (badges, hover
  // and selected rows, menu highlights) defined per colour mode in
  // globals.css; an inline override beats both modes and turned every hover
  // and "secondary" badge near-black with unreadable text in light mode.

  // Set semantic colors (only if the DB has seeded theme.colors).
  // Set the RAW space-separated triplet vars (--success-rgb etc, matching
  // globals.css's `--color-success: rgb(var(--success-rgb, ...))` indirection
  // — same two-level pattern as --color-primary-base/--primary/--color-primary
  // above), NOT the final --color-success name directly: setting that
  // directly to hexToRgb()'s comma-separated triplet (no rgb() wrapper) is
  // invalid CSS, which silently makes `color`/`fill` fall back to inherited
  // black text everywhere these tokens are used.
  if (theme.colors) {
    root.style.setProperty('--success-light-rgb', hexToRgbSpace(theme.colors.success.light));
    root.style.setProperty('--success-rgb', hexToRgbSpace(theme.colors.success.default));
    root.style.setProperty('--success-dark-rgb', hexToRgbSpace(theme.colors.success.dark));

    root.style.setProperty('--warning-light-rgb', hexToRgbSpace(theme.colors.warning.light));
    root.style.setProperty('--warning-rgb', hexToRgbSpace(theme.colors.warning.default));
    root.style.setProperty('--warning-dark-rgb', hexToRgbSpace(theme.colors.warning.dark));

    root.style.setProperty('--error-light-rgb', hexToRgbSpace(theme.colors.error.light));
    root.style.setProperty('--error-rgb', hexToRgbSpace(theme.colors.error.default));
    root.style.setProperty('--error-dark-rgb', hexToRgbSpace(theme.colors.error.dark));

    root.style.setProperty('--info-light-rgb', hexToRgbSpace(theme.colors.info.light));
    root.style.setProperty('--info-rgb', hexToRgbSpace(theme.colors.info.default));
    root.style.setProperty('--info-dark-rgb', hexToRgbSpace(theme.colors.info.dark));

    if (theme.colors.neutral) {
      root.style.setProperty('--color-neutral-50', hexToRgb(theme.colors.neutral['50']));
      root.style.setProperty('--color-neutral-100', hexToRgb(theme.colors.neutral['100']));
      root.style.setProperty('--color-neutral-200', hexToRgb(theme.colors.neutral['200']));
      root.style.setProperty('--color-neutral-300', hexToRgb(theme.colors.neutral['300']));
      root.style.setProperty('--color-neutral-400', hexToRgb(theme.colors.neutral['400']));
      root.style.setProperty('--color-neutral-500', hexToRgb(theme.colors.neutral['500']));
      root.style.setProperty('--color-neutral-600', hexToRgb(theme.colors.neutral['600']));
      root.style.setProperty('--color-neutral-700', hexToRgb(theme.colors.neutral['700']));
      root.style.setProperty('--color-neutral-800', hexToRgb(theme.colors.neutral['800']));
      root.style.setProperty('--color-neutral-900', hexToRgb(theme.colors.neutral['900']));
    }
  }

  // Set typography (only if DB has seeded values)
  if (theme.typography) {
    root.style.setProperty('--font-size-xs',   theme.typography.fontSize.xs);
    root.style.setProperty('--font-size-sm',   theme.typography.fontSize.sm);
    root.style.setProperty('--font-size-base', theme.typography.fontSize.base);
    root.style.setProperty('--font-size-lg',   theme.typography.fontSize.lg);
    root.style.setProperty('--font-size-xl',   theme.typography.fontSize.xl);
    root.style.setProperty('--font-size-2xl',  theme.typography.fontSize['2xl']);
    root.style.setProperty('--font-size-3xl',  theme.typography.fontSize['3xl']);
    root.style.setProperty('--font-size-4xl',  theme.typography.fontSize['4xl']);

    root.style.setProperty('--font-weight-normal',   theme.typography.fontWeight.normal);
    root.style.setProperty('--font-weight-medium',   theme.typography.fontWeight.medium);
    root.style.setProperty('--font-weight-semibold', theme.typography.fontWeight.semibold);
    root.style.setProperty('--font-weight-bold',     theme.typography.fontWeight.bold);

    if (theme.typography.fontFamily?.sans)
      root.style.setProperty('--font-family-sans', theme.typography.fontFamily.sans);
    if (theme.typography.fontFamily?.mono)
      root.style.setProperty('--font-family-mono', theme.typography.fontFamily.mono);
  }

  // Set border radius — use shadcn's --radius-* vars (derived from --radius in CSS)
  if (theme.borderRadius) {
    root.style.setProperty('--radius',    theme.borderRadius.md);
    root.style.setProperty('--radius-sm', theme.borderRadius.sm);
    root.style.setProperty('--radius-md', theme.borderRadius.md);
    root.style.setProperty('--radius-lg', theme.borderRadius.lg);
    root.style.setProperty('--radius-xl', theme.borderRadius.xl);
  }

  // Set other theme properties
  root.setAttribute('data-sidebar-style', theme.sidebarStyle);
  root.setAttribute('data-white-label', theme.whiteLabel.toString());
}

/**
 * Get theme-aware logo URL
 */
export function getLogoUrl(theme: Theme): string {
  if (theme.whiteLabel && theme.logo) {
    return theme.logo;
  }
  return '/logo.svg'; // Default logo
}
