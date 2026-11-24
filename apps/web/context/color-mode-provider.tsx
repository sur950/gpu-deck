'use client';

import { ThemeProvider as NextThemesProvider } from 'next-themes';
import { DEFAULT_COLOR_MODE } from '@/utils/brand-env';

/**
 * Light / dark colour mode.
 *
 * Separate from ThemeProvider (context/theme-provider.tsx), which applies the
 * env branding. next-themes toggles the `.dark` class on <html> that
 * globals.css keys the dark palette on, persists the choice, and sets it
 * from an inline script before paint so a reload doesn't flash the other mode.
 *
 * The signed-in app and the sign-in page open in the mode set by
 * NEXT_PUBLIC_DEFAULT_COLOR_MODE (dark unless changed); the profile menu's
 * switch and the top-right toggle on the sign-in page change it, and that
 * choice is remembered per browser (localStorage, not the admin's account).
 */
export function ColorModeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme={DEFAULT_COLOR_MODE}
      enableSystem={false}
      storageKey="gpu-deck:color-mode:v1"
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}
