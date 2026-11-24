/**
 * Breadcrumbs navigation component
 * Shows current page location in the app hierarchy. Labels come from the nav
 * registry (page titles, per-feature nested labels and detail labels), so a
 * feature adds its own crumbs in lib/nav/items/<feature>.ts.
 */

'use client';

import { usePathname } from 'next/navigation';
import Link from 'next/link';
import { ChevronRight, Home } from 'lucide-react';
import { cn } from '@/lib/utils';
import { breadcrumbLabel } from '@/lib/nav/registry';

// Segments whose breadcrumb link should point somewhere other than their natural path
// (e.g. a grouping segment with no page of its own).
const SEGMENT_HREF_OVERRIDES: Record<string, string> = {
  observability: '/observability/inference',
};

export function Breadcrumbs() {
  const pathname = usePathname();

  // Parse pathname into breadcrumb segments
  const allSegments = pathname.split('/').filter(Boolean);

  // Build href for a breadcrumb at given index
  const buildHref = (index: number) => {
    const segment = allSegments[index];
    if (SEGMENT_HREF_OVERRIDES[segment]) {
      return SEGMENT_HREF_OVERRIDES[segment];
    }
    const pathSegments = allSegments.slice(0, index + 1);
    return `/${pathSegments.join('/')}`;
  };

  // Show just "Dashboard" if we're at root
  if (allSegments.length === 0) {
    return (
      <div className="flex items-center gap-2 text-[13px]">
        <Home className="size-4 text-muted-foreground" />
        <span className="font-semibold">Dashboard</span>
      </div>
    );
  }

  return (
    <nav className="flex items-center gap-1.5 overflow-hidden text-[13px]" aria-label="Breadcrumb">
      {/* Home link — phones show only the current page */}
      <Link
        href="/dashboard"
        className="hidden shrink-0 items-center rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground sm:flex"
        aria-label="Home"
      >
        <Home className="size-4" />
      </Link>

      {/* Breadcrumb trail */}
      {allSegments.map((segment, index) => {
        const isLast = index === allSegments.length - 1;
        const href = buildHref(index);
        const label = breadcrumbLabel(allSegments, index);

        // All segments except the last one should be clickable
        const shouldBeLink = !isLast;

        return (
          <div
            key={`breadcrumb-${index}-${segment}`}
            className={cn('min-w-0 items-center gap-1.5', isLast ? 'flex' : 'hidden sm:flex')}
          >
            <ChevronRight
              className={cn('size-3.5 shrink-0 text-muted-foreground/50', isLast && 'hidden sm:block')}
              aria-hidden="true"
            />
            {shouldBeLink ? (
              <Link
                href={href}
                className="truncate text-muted-foreground transition-colors hover:text-foreground"
              >
                {label}
              </Link>
            ) : (
              <span className={cn("truncate", isLast && "font-semibold text-foreground")}>
                {label}
              </span>
            )}
          </div>
        );
      })}
    </nav>
  );
}
