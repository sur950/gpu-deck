import { LayoutDashboard } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: dashboards / observability. */
export const dashboardNav: NavEntry[] = [
  {
    id: 'dashboard',
    title: 'Dashboard',
    href: '/dashboard',
    icon: LayoutDashboard,
    group: 'overview',
    order: 0,
    description: 'Fleet health, inference traffic and automation at a glance.',
    keywords: ['overview', 'home'],
  },
];
