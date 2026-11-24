/**
 * Global search (⌘K / Ctrl K)
 * Command dialog over the nav registry: every page plus the ⌘K actions a
 * feature declares on its NavEntry (`actions`). Filters client-side.
 * Feature agents extend it from lib/nav/items/<feature>.ts, not here.
 */

'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { ArrowRight, CornerDownLeft, Search } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { HIDDEN_ENTRIES, NAV_SECTIONS } from '@/lib/nav/registry';

interface SearchResult {
  id: string;
  title: string;
  subtitle?: string;
  link: string;
  icon: LucideIcon;
  /** Group heading in the list. */
  section: string;
  /** Text the palette filters on. */
  value: string;
  badge?: string;
}

function buildResults(): { sections: string[]; results: SearchResult[] } {
  const results: SearchResult[] = [];
  const sections: string[] = [];

  for (const section of NAV_SECTIONS) {
    sections.push(section.title);
    for (const entry of section.items) {
      results.push({
        id: entry.id,
        title: entry.title,
        subtitle: entry.description,
        link: entry.href,
        icon: entry.icon,
        section: section.title,
        value: [entry.title, section.title, entry.description, ...(entry.keywords ?? [])].join(' '),
      });
      for (const action of entry.actions ?? []) {
        results.push({
          id: `${entry.id}:${action.href}`,
          title: action.label,
          subtitle: entry.title,
          link: action.href,
          icon: action.icon ?? ArrowRight,
          section: section.title,
          value: [action.label, entry.title, ...(action.keywords ?? [])].join(' '),
          badge: 'Action',
        });
      }
    }
  }

  const accountSection = 'Account';
  for (const entry of HIDDEN_ENTRIES) {
    if (!sections.includes(accountSection)) sections.push(accountSection);
    results.push({
      id: entry.id,
      title: entry.title,
      subtitle: entry.description,
      link: entry.href,
      icon: entry.icon,
      section: accountSection,
      value: [entry.title, entry.description, ...(entry.keywords ?? [])].join(' '),
    });
    for (const action of entry.actions ?? []) {
      results.push({
        id: `${entry.id}:${action.href}`,
        title: action.label,
        subtitle: entry.title,
        link: action.href,
        icon: action.icon ?? ArrowRight,
        section: accountSection,
        value: [action.label, entry.title, ...(action.keywords ?? [])].join(' '),
        badge: 'Action',
      });
    }
  }

  return { sections, results };
}

interface GlobalSearchProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function GlobalSearch({ open, onOpenChange }: GlobalSearchProps) {
  const [query, setQuery] = useState('');
  const router = useRouter();
  const { sections, results } = useMemo(() => buildResults(), []);

  // Keyboard shortcut
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    document.addEventListener('keydown', down);
    return () => document.removeEventListener('keydown', down);
  }, [open, onOpenChange]);

  const handleSelect = (link: string) => {
    onOpenChange(false);
    router.push(link);
    setQuery('');
  };

  // Enter opens the first match of the current query.
  const handleSubmit = () => {
    const q = query.trim().toLowerCase();
    if (!q) return;
    const first = results.find((r) => r.value.toLowerCase().includes(q));
    if (first) handleSelect(first.link);
  };

  const grouped = sections
    .map((section) => ({ section, items: results.filter((r) => r.section === section) }))
    .filter((g) => g.items.length > 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="p-0 sm:max-w-2xl" showCloseButton={false}>
        <DialogTitle className="sr-only">Search</DialogTitle>
        <Command className="rounded-lg border-none shadow-none">
          <CommandInput
            placeholder="Search pages and actions…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleSubmit();
              }
            }}
            autoFocus
          />
          <CommandList className="max-h-[420px]">
            <CommandEmpty>
              <div className="flex flex-col items-center justify-center py-8 text-center">
                <Search className="h-12 w-12 text-muted-foreground/50 mb-3" />
                <p className="text-sm font-medium">No results found</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Try a page name, like deployments or rules
                </p>
              </div>
            </CommandEmpty>
            {grouped.map(({ section, items }, index) => (
              <div key={section}>
                {index > 0 && <CommandSeparator />}
                <CommandGroup>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">{section}</div>
                  {items.map((result) => {
                    const Icon = result.icon;
                    return (
                      <CommandItem
                        key={result.id}
                        value={result.value}
                        onSelect={() => handleSelect(result.link)}
                        className="flex items-center gap-3 py-2.5"
                      >
                        <Icon className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium truncate">{result.title}</div>
                          {result.subtitle && <div className="text-xs text-muted-foreground truncate">{result.subtitle}</div>}
                        </div>
                        {result.badge && <Badge variant="secondary" className="text-xs flex-shrink-0">{result.badge}</Badge>}
                      </CommandItem>
                    );
                  })}
                </CommandGroup>
              </div>
            ))}
          </CommandList>
          <div className="flex items-center justify-center gap-3 border-t p-2 text-xs text-muted-foreground">
            <span>
              <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
                <CornerDownLeft className="size-3" />
              </kbd>{' '}
              opens the first match
            </span>
            <span>
              <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
                <span className="text-xs">⌘</span>K
              </kbd>{' '}
              to toggle search
            </span>
          </div>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
