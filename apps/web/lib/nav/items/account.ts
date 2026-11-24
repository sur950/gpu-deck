import { KeyRound, LogOut, UserRound } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/**
 * Account pages. Not in the sidebar — reached from the profile
 * menu; registered so breadcrumbs and ⌘K know it.
 */
export const accountNav: NavEntry[] = [
  {
    id: 'account',
    title: 'Account',
    href: '/account',
    icon: UserRound,
    group: 'security',
    description: 'The seeded admin account: password and signed-in sessions.',
    keywords: ['account', 'password', 'profile', 'sessions'],
    breadcrumbs: { password: 'Change password' },
    actions: [
      { label: 'Change password', href: '/account#password', icon: KeyRound, keywords: ['password', 'rotate'] },
      { label: 'Sign out other sessions', href: '/account#sessions', icon: LogOut, keywords: ['sessions', 'revoke', 'logout'] },
    ],
  },
];
