'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useSidebarPreferences } from '@/hooks/use-sidebar-preferences';
import { ALL_NAV_HREFS, NAV_SECTIONS, isNavItemActive } from '@/lib/nav/registry';
import type { NavEntry } from '@/lib/nav/types';

interface SidebarNavProps {
  /** Icon-only rail (desktop collapsed state). */
  collapsed: boolean;
  /** Called after a link is chosen — the mobile drawer closes itself with it. */
  onNavigate?: () => void;
}

/**
 * Navigation list shared by the desktop sidebar and the mobile drawer. Built
 * from the nav registry: headless groups (Overview) render as top-level rows,
 * every other group as a foldable section.
 */
export function SidebarNav({ collapsed, onNavigate }: SidebarNavProps) {
  const pathname = usePathname();
  const { collapsedSections, toggleSection } = useSidebarPreferences();

  const isActive = (href: string) => isNavItemActive(href, pathname, ALL_NAV_HREFS);
  const headless = NAV_SECTIONS.filter((s) => s.headless);
  const sections = NAV_SECTIONS.filter((s) => !s.headless);

  return (
    <TooltipProvider>
      <nav
        aria-label="Main"
        className={cn('flex-1 overflow-y-auto overflow-x-hidden py-3', collapsed ? 'px-2' : 'px-3')}
      >
        {headless.flatMap((section) =>
          section.items.map((item) => (
            <NavLink
              key={item.href}
              item={item}
              active={isActive(item.href)}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          )),
        )}

        {sections.map((section) => {
          const sectionId = `nav-section-${section.id}`;
          // Icon-only rail shows every item; folding is a full-width affordance.
          const open = collapsed || !collapsedSections.has(section.title);

          return (
            <div
              key={section.id}
              className={cn(collapsed ? 'mt-3 border-t border-border/70 pt-3' : 'mt-5')}
            >
              {!collapsed && (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-expanded={open}
                  aria-controls={sectionId}
                  onClick={() => toggleSection(section.title)}
                  className="group/section mb-1 h-7 w-full justify-between px-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80 hover:bg-transparent hover:text-foreground aria-expanded:bg-transparent aria-expanded:text-muted-foreground/80 aria-expanded:hover:text-foreground dark:hover:bg-transparent"
                >
                  {section.title}
                  <ChevronDown
                    className={cn(
                      'size-3.5 opacity-0 transition-all duration-200 group-hover/section:opacity-100 group-focus-visible/section:opacity-100',
                      !open && '-rotate-90 opacity-100',
                    )}
                  />
                </Button>
              )}

              <div
                id={sectionId}
                inert={!open}
                className={cn(
                  'grid transition-[grid-template-rows,opacity] duration-200 ease-out',
                  open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
                )}
              >
                {/* overflow-hidden drives the fold animation; the list reaches
                    into the nav gutter so the active marker (drawn just
                    outside each row) is not clipped by it. */}
                <ul
                  className={cn(
                    'min-h-0 space-y-0.5 overflow-hidden',
                    collapsed ? '-ml-2 pl-2' : '-ml-3 pl-3',
                  )}
                >
                  {section.items.map((item) => (
                    <li key={item.href}>
                      <NavLink
                        item={item}
                        active={isActive(item.href)}
                        collapsed={collapsed}
                        onNavigate={onNavigate}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          );
        })}
      </nav>
    </TooltipProvider>
  );
}

interface NavLinkProps {
  item: NavEntry;
  active: boolean;
  collapsed: boolean;
  onNavigate?: () => void;
}

function NavLink({ item, active, collapsed, onNavigate }: NavLinkProps) {
  const Icon = item.icon;

  const link = (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      aria-label={collapsed ? item.title : undefined}
      className={cn(
        'group/nav relative flex h-9 items-center gap-3 rounded-md px-2.5 text-[13px] font-medium outline-none transition-colors duration-150',
        'focus-visible:ring-2 focus-visible:ring-ring/30',
        collapsed && 'justify-center px-0',
        active
          ? 'bg-brand/[0.08] text-foreground dark:bg-brand/[0.14]'
          : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
      )}
    >
      {/* Accent marker on the active row (the crimson accent, docs/BRAND.md). */}
      <span
        aria-hidden
        className={cn(
          'absolute top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-brand transition-opacity duration-150',
          collapsed ? '-left-2' : '-left-3',
          active ? 'opacity-100' : 'opacity-0',
        )}
      />
      <Icon
        className={cn(
          'size-4 shrink-0 transition-colors',
          active ? 'text-brand-text' : 'text-muted-foreground group-hover/nav:text-foreground',
        )}
      />
      {!collapsed && <span className="truncate">{item.title}</span>}
    </Link>
  );

  if (!collapsed) return link;

  return (
    <Tooltip>
      <TooltipTrigger render={link} />
      <TooltipContent side="right">{item.title}</TooltipContent>
    </Tooltip>
  );
}
