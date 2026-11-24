/**
 * ThemeProvider
 * Builds the runtime Theme from NEXT_PUBLIC_* branding (utils/brand-env.ts)
 * and applies it to the document with applyTheme(). GPU Deck has no settings
 * UI, so the theme is static for the lifetime of the build.
 */

'use client';

import { createContext, useContext, useEffect, useMemo } from 'react';
import { applyTheme } from '@/utils/theme';
import { buildThemeFromEnv } from '@/utils/brand-env';
import type { Theme } from '@/types/org';

interface ThemeContextValue {
  theme: Theme;
  loading: boolean;
  updateTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used within ThemeProvider');
  return context;
}

// ── Provider ──────────────────────────────────────────────────────────────────

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const theme = useMemo<Theme>(() => buildThemeFromEnv(), []);

  // Side-effect: apply the theme to the DOM once mounted.
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Imperative override for callers that need it (e.g. a preview).
  const updateTheme = (newTheme: Theme) => {
    applyTheme(newTheme);
  };

  return (
    <ThemeContext.Provider value={{ theme, loading: false, updateTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
