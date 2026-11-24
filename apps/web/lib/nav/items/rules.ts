import { Workflow } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: rules builder — 6 rule types + dry run. */
export const rulesNav: NavEntry[] = [
  {
    id: 'rules',
    title: 'Rules',
    href: '/rules',
    icon: Workflow,
    group: 'automation',
    order: 10,
    description: 'Idle unload, time schedule, shrink, grow, make room by priority and auto heal rules.',
    keywords: ['rule', 'automation', 'idle', 'schedule', 'dry run'],
    breadcrumbs: { new: 'New rule', edit: 'Edit rule', history: 'Run history' },
    detailLabel: 'Rule details',
    actions: [
      { label: 'New rule', href: '/rules/new', keywords: ['create', 'automation'] },
      { label: 'New idle-unload rule', href: '/rules/new?type=idle_unload', keywords: ['free gpu', 'unload'] },
      { label: 'New time-schedule rule', href: '/rules/new?type=time_schedule', keywords: ['night', 'day', 'cron'] },
      { label: 'New auto-heal rule', href: '/rules/new?type=auto_heal', keywords: ['restart', 'health'] },
    ],
  },
];
