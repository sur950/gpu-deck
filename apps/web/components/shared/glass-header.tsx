import type { ReactNode, Ref } from 'react';

interface GlassHeaderProps {
  /**
   * What the bar floats over, which sets its tint (see `.glass-bar` in globals.css):
   * - 'inverse': the black brand bands (landing hero) — dark glass in either mode.
   * - 'page': the page surface — follows light / dark mode.
   */
  surface: 'inverse' | 'page';
  /** Ref to the bar itself, for callers that track what is beneath it. */
  barRef?: Ref<HTMLDivElement>;
  /** Laid out at the two corners: the first child left, the last right. */
  children: ReactNode;
}

/**
 * The floating liquid-glass header shared by the landing page and the sign-in
 * pages: fixed to the top with small even gutters, spanning the page.
 * Pages under it need ~72 px of top padding (12–16 px inset + 56 px bar).
 */
export function GlassHeader({ surface, barRef, children }: GlassHeaderProps) {
  return (
    <header className="fixed inset-x-0 top-0 z-50 px-3 pt-3 sm:px-4 sm:pt-4">
      <div
        ref={barRef}
        data-surface={surface}
        className="glass-bar mx-auto flex h-14 w-full items-center justify-between rounded-2xl px-3 sm:px-4"
      >
        {children}
      </div>
    </header>
  );
}
