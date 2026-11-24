import { BellRing } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: alerts. */
export const alertsNav: NavEntry[] = [
  {
    id: 'alerts',
    title: 'Alerts',
    href: '/alerts',
    icon: BellRing,
    group: 'automation',
    order: 40,
    description: 'Slack, email and webhook channels for rule actions, failures and fleet health.',
    keywords: ['alert', 'slack', 'email', 'webhook', 'notification'],
    breadcrumbs: { channels: 'Channels', new: 'New channel' },
    detailLabel: 'Alert details',
  },
];
