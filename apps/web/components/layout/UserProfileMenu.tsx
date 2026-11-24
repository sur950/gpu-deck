/**
 * User profile menu component
 * Displays the admin's avatar, name and role with a dropdown for account
 * actions: change password, dark mode, logout, and the project credit.
 */

'use client';

import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DropdownMenuGroup,
} from '@/components/ui/dropdown-menu';
import { ChevronDown, KeyRound, LogOut, Moon } from 'lucide-react';
import { useTheme } from 'next-themes';
import { cn } from '@/lib/utils';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/hooks/use-auth';
import { LOGO_URL } from '@/utils/brand-env';

interface UserProfileMenuProps {
  user: {
    id: string;
    username: string;
    fullName: string;
    role: string;
  } | null;
}

export function UserProfileMenu({ user }: UserProfileMenuProps) {
  const router = useRouter();
  const { logout } = useAuth();
  const queryClient = useQueryClient();
  const { resolvedTheme, setTheme } = useTheme();
  const isDark = resolvedTheme === 'dark';

  if (!user) {
    return null;
  }

  const handleLogout = async () => {
    await logout();
    // Clear all cached query data so a subsequent login starts fresh
    queryClient.clear();
    // Replace instead of push so the back button doesn't return to authenticated pages
    router.replace('/login');
  };

  const getInitials = () => {
    if (user.fullName) {
      const parts = user.fullName.split(' ');
      if (parts.length >= 2) {
        return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
      }
      return user.fullName.substring(0, 2).toUpperCase();
    }
    return user.username.substring(0, 2).toUpperCase();
  };

  const formatRole = (role: string) => {
    return role
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Account menu"
        className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-full py-1 pl-1 pr-1 outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/30 data-[popup-open]:bg-accent md:pr-2.5"
      >
        <Avatar className="size-8 bg-muted ring-1 ring-border">
          <AvatarFallback className="text-[11px] font-semibold">{getInitials()}</AvatarFallback>
        </Avatar>
        <div className="hidden max-w-[160px] text-left md:block">
          <div className="truncate text-[13px] font-semibold leading-tight">{user.fullName}</div>
          <div className="truncate text-[11px] leading-tight text-muted-foreground">
            {formatRole(user.role)}
          </div>
        </div>
        <ChevronDown className="hidden size-3.5 text-muted-foreground md:block" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64" onClick={(e) => e.stopPropagation()}>
        <DropdownMenuGroup>
          <DropdownMenuLabel>
            <div className="flex items-center gap-3">
              <Avatar className="size-10 bg-muted ring-1 ring-border">
                <AvatarFallback>{getInitials()}</AvatarFallback>
              </Avatar>
              <div className="flex flex-col space-y-0.5 overflow-hidden">
                <p className="text-sm font-medium truncate">{user.fullName}</p>
                <p className="text-xs text-muted-foreground truncate">@{user.username}</p>
                <p className="mt-1 w-fit rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] font-medium text-foreground">
                  {formatRole(user.role)}
                </p>
              </div>
            </div>
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={(e) => {
          e.stopPropagation();
          router.push('/account');
        }}>
          <KeyRound className="mr-2 h-4 w-4" />
          <span>Change password</span>
        </DropdownMenuItem>
        {/* Stays open on toggle so the switch visibly flips. The built-in
            check indicator is hidden in favour of the switch. */}
        <DropdownMenuCheckboxItem
          checked={isDark}
          onCheckedChange={(checked) => setTheme(checked ? 'dark' : 'light')}
          className="pr-2 [&_[data-slot=dropdown-menu-checkbox-item-indicator]]:hidden"
        >
          <Moon className="mr-2 h-4 w-4" />
          <span className="flex-1">Dark mode</span>
          <span
            aria-hidden
            className={cn(
              'relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors',
              isDark ? 'bg-primary' : 'bg-muted-foreground/30',
            )}
          >
            <span
              className={cn(
                'absolute left-0.5 size-3 rounded-full bg-background shadow-sm transition-transform',
                isDark && 'translate-x-3',
              )}
            />
          </span>
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={(e) => {
          e.stopPropagation();
          handleLogout();
        }} className="text-destructive focus:text-destructive">
          <LogOut className="mr-2 h-4 w-4" />
          <span>Logout</span>
        </DropdownMenuItem>
        {/* Project credit (docs/BRAND.md): here and in the desktop About window,
            never under the product name. Hidden for a white-label logo. */}
        {!LOGO_URL && (
          <>
            <DropdownMenuSeparator />
            <p className="px-2 pb-1 pt-1.5 text-[11px] text-muted-foreground">
              Made by{' '}
              <a
                href="https://suresh-konakanchi.in"
                target="_blank"
                rel="noreferrer"
                className="font-medium text-foreground underline-offset-2 hover:text-brand-text hover:underline"
              >
                Suresh Konakanchi
              </a>
            </p>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
