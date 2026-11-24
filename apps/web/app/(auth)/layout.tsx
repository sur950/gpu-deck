/**
 * Auth layout
 * Branded layout for the sign-in page: floating glass bar with the lockup
 * and colour-mode toggle, grid-pattern background, footer.
 */

import type { Metadata } from 'next';
import { Logo } from '@/components/shared/Logo';
import { ColorModeToggle } from '@/components/shared/color-mode-toggle';
import { GlassHeader } from '@/components/shared/glass-header';
import { APP_NAME } from '@/utils/brand-env';

export const metadata: Metadata = {
  title: 'Sign in',
  description: `Sign in to ${APP_NAME}`,
};

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen flex flex-col">
      {/* Floating glass bar: logo and mode toggle in the corners. Tinted
          from the page, so it follows light / dark mode. */}
      <GlassHeader surface="page">
        <Logo />
        <ColorModeToggle />
      </GlassHeader>

      {/* Main content centered with gradient background */}
      <main className="flex-1 flex items-center justify-center pt-24 pb-12 px-4 relative">
        <div className="absolute inset-0 bg-grid-pattern"></div>
        <div className="absolute inset-0 bg-gradient-radial"></div>
        <div className="w-full relative z-10 animate-fade-in-up">{children}</div>
      </main>

      {/* Footer */}
      <footer className="border-t border-border/40 py-8 section-white relative z-10">
        <div className="container mx-auto px-4 text-center text-sm text-muted-foreground">
          <p className="font-medium">
            GPU control plane for the models you run on your own hardware
          </p>
          <div className="mt-3 flex items-center justify-center gap-6">
            <a
              href="#"
              className="hover:text-foreground transition-colors"
            >
              Terms
            </a>
            <a
              href="#"
              className="hover:text-foreground transition-colors"
            >
              Privacy
            </a>
            <a
              href="#"
              className="hover:text-foreground transition-colors"
            >
              Support
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
