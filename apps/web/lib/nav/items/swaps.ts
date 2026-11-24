import { ArrowLeftRight } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: swap history. */
export const swapsNav: NavEntry[] = [
  {
    id: 'swaps',
    title: 'Swaps',
    href: '/swaps',
    icon: ArrowLeftRight,
    group: 'automation',
    order: 30,
    description: 'Drain, unload, load and health-check operations, with roll-backs.',
    keywords: ['swap', 'drain', 'unload', 'load', 'rollback'],
    detailLabel: 'Swap details',
  },
];
