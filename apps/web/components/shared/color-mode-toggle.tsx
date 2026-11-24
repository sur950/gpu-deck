'use client';

import { useSyncExternalStore } from 'react';
import { Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const noopSubscribe = () => () => {};

interface ColorModeToggleProps {
  className?: string;
}

/**
 * Sun / moon button that flips between dark and light mode. Writes the same
 * per-browser preference as the profile menu's "Dark mode" switch.
 *
 * The saved mode is only known in the browser, so until hydration it renders a
 * same-size placeholder — the server-rendered HTML can't disagree with it and
 * nothing shifts when the real icon appears.
 */
export function ColorModeToggle({ className }: ColorModeToggleProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(noopSubscribe, () => true, () => false);

  if (!mounted) {
    return <span aria-hidden className={cn('inline-block size-9', className)} />;
  }

  const isDark = resolvedTheme === 'dark';
  const label = isDark ? 'Switch to light mode' : 'Switch to dark mode';

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
      className={cn('rounded-full text-muted-foreground hover:text-foreground', className)}
    >
      {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  );
}
