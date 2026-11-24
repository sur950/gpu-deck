/**
 * Desktop sidebar (lg and up). Below lg the same navigation opens as a
 * drawer from the top bar — see mobile-sidebar.tsx.
 *
 * Two collapse levels, both remembered per browser:
 *  1. Sidebar-level: icon-only rail vs icon + label
 *  2. Section-level: each section header folds its items
 */

'use client';

import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Logo } from '@/components/shared/Logo';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { SidebarNav } from '@/components/layout/sidebar-nav';
import { useSidebarPreferences } from '@/hooks/use-sidebar-preferences';

export function Sidebar() {
  const { isCollapsed, toggleCollapsed } = useSidebarPreferences();
  const toggleLabel = isCollapsed ? 'Expand sidebar' : 'Collapse sidebar';
  const ToggleIcon = isCollapsed ? PanelLeftOpen : PanelLeftClose;

  const toggle = (
    <Button
      variant="ghost"
      onClick={toggleCollapsed}
      aria-label={toggleLabel}
      className={cn(
        'h-9 w-full gap-3 text-[13px] font-medium text-muted-foreground hover:text-foreground',
        isCollapsed ? 'justify-center px-0' : 'justify-start px-2.5',
      )}
    >
      <ToggleIcon className="size-4 shrink-0" />
      {!isCollapsed && <span>Collapse</span>}
    </Button>
  );

  return (
    <aside
      className={cn(
        'hidden shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-200 ease-out lg:flex',
        isCollapsed ? 'w-[68px]' : 'w-[260px]',
      )}
    >
      <div
        className={cn(
          'flex h-14 shrink-0 items-center border-b border-border',
          isCollapsed ? 'justify-center px-0' : 'px-4',
        )}
      >
        <Logo iconOnly={isCollapsed} />
      </div>

      <SidebarNav collapsed={isCollapsed} />

      <div className={cn('shrink-0 border-t border-border py-2', isCollapsed ? 'px-2' : 'px-3')}>
        {isCollapsed ? (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger render={toggle} />
              <TooltipContent side="right">{toggleLabel}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : (
          toggle
        )}
      </div>
    </aside>
  );
}
