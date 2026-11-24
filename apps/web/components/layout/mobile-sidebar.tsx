'use client';

import { useState } from 'react';
import { Menu } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Logo } from '@/components/shared/Logo';
import { SidebarNav } from '@/components/layout/sidebar-nav';

/** Navigation drawer for phones and tablets (below lg), opened from the top bar. */
export function MobileSidebar() {
  const [open, setOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon" className="-ml-1.5 lg:hidden" aria-label="Open navigation" />
        }
      >
        <Menu className="size-5" />
      </SheetTrigger>
      <SheetContent side="left" className="w-[280px] gap-0 bg-surface p-0">
        <SheetHeader className="flex h-14 flex-row items-center border-b border-border px-4 py-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Logo onClick={() => setOpen(false)} />
        </SheetHeader>
        <SidebarNav collapsed={false} onNavigate={() => setOpen(false)} />
      </SheetContent>
    </Sheet>
  );
}
