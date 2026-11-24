'use client';

import { useSyncExternalStore } from 'react';
import { Search } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface SearchTriggerProps {
  onOpen: () => void;
}

const noopSubscribe = () => () => {};
const isApplePlatform = () => /Mac|iPhone|iPad/.test(navigator.userAgent);

/**
 * Opens the global search palette. The shortcut itself is registered inside
 * GlobalSearch (⌘K / Ctrl+K); this only shows the right hint for the platform.
 */
export function SearchTrigger({ onOpen }: SearchTriggerProps) {
  const isMac = useSyncExternalStore(noopSubscribe, isApplePlatform, () => true);

  return (
    <>
      <Button
        variant="outline"
        onClick={onOpen}
        className="hidden h-9 w-56 justify-between gap-2 bg-background/60 px-3 text-[13px] font-normal text-muted-foreground shadow-none hover:bg-background hover:text-foreground md:inline-flex lg:w-64"
      >
        <span className="flex items-center gap-2">
          <Search className="size-4" />
          Search…
        </span>
        <kbd className="pointer-events-none rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium text-muted-foreground">
          {isMac ? '⌘K' : 'Ctrl K'}
        </kbd>
      </Button>
      <Button
        variant="ghost"
        size="icon"
        onClick={onOpen}
        aria-label="Search"
        className="md:hidden"
      >
        <Search className="size-4" />
      </Button>
    </>
  );
}
