import { History } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: audit log. */
export const auditLogNav: NavEntry[] = [
  {
    id: 'audit-log',
    title: 'Audit log',
    href: '/audit-log',
    icon: History,
    group: 'security',
    order: 20,
    description: 'Every login, key change, rule action and load or unload, with time and source IP.',
    keywords: ['audit', 'history', 'who did what'],
  },
];
