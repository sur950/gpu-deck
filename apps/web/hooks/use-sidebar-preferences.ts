'use client';

import { useCallback, useMemo } from 'react';
import { usePersistedString } from '@/hooks/use-persisted-string';

const COLLAPSED_KEY = 'gpu-deck:sidebar-collapsed';
const SECTIONS_KEY = 'gpu-deck:sidebar-collapsed-sections';

function parseSections(raw: string | null): Set<string> {
  if (!raw) return new Set();
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? new Set(parsed.filter((v): v is string => typeof v === 'string'))
      : new Set();
  } catch {
    return new Set();
  }
}

/** Sidebar width (icon-only vs full) and which nav sections are folded, remembered per browser. */
export function useSidebarPreferences() {
  const [collapsedRaw, setCollapsedRaw] = usePersistedString(COLLAPSED_KEY);
  const [sectionsRaw, setSectionsRaw] = usePersistedString(SECTIONS_KEY);

  const isCollapsed = collapsedRaw === '1';
  const collapsedSections = useMemo(() => parseSections(sectionsRaw), [sectionsRaw]);

  const toggleCollapsed = useCallback(() => {
    setCollapsedRaw(isCollapsed ? '0' : '1');
  }, [isCollapsed, setCollapsedRaw]);

  const toggleSection = useCallback(
    (title: string) => {
      const next = new Set(collapsedSections);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      setSectionsRaw(JSON.stringify([...next]));
    },
    [collapsedSections, setSectionsRaw],
  );

  return { isCollapsed, toggleCollapsed, collapsedSections, toggleSection };
}
