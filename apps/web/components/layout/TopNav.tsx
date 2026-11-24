/**
 * Top navigation bar
 * Mobile nav trigger, breadcrumbs, global search (⌘K) and the admin profile
 * menu (name + role).
 */

'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks/use-auth';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { GlobalSearch } from '@/components/layout/GlobalSearch';
import { UserProfileMenu } from '@/components/layout/UserProfileMenu';
import { MobileSidebar } from '@/components/layout/mobile-sidebar';
import { SearchTrigger } from '@/components/layout/search-trigger';

export function TopNav() {
  const { user } = useAuth();
  const [searchOpen, setSearchOpen] = useState(false);

  return (
    <header className="z-30 flex h-14 shrink-0 items-center gap-2 border-b border-border bg-surface/85 px-3 backdrop-blur-md sm:gap-3 sm:px-6">
      <MobileSidebar />

      <div className="min-w-0 flex-1">
        <Breadcrumbs />
      </div>

      <div className="flex items-center gap-1 sm:gap-2">
        <SearchTrigger onOpen={() => setSearchOpen(true)} />

        <div className="mx-1 hidden h-6 w-px bg-border sm:block" aria-hidden />

        <UserProfileMenu user={user} />
      </div>

      <GlobalSearch open={searchOpen} onOpenChange={setSearchOpen} />
    </header>
  );
}
