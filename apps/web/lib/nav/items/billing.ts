import { Receipt } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: billing / reports. */
export const billingNav: NavEntry[] = [
  {
    id: 'billing',
    title: 'Billing',
    href: '/billing',
    icon: Receipt,
    group: 'usage',
    order: 20,
    description: 'USD prices per GPU minute or token, cost per key and model, monthly reports.',
    keywords: ['billing', 'cost', 'price', 'report', 'invoice'],
    breadcrumbs: { reports: 'Reports', prices: 'Price plans' },
  },
];
