import { Gauge } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: metering. */
export const meteringNav: NavEntry[] = [
  {
    id: 'metering',
    title: 'Metering',
    href: '/metering',
    icon: Gauge,
    group: 'usage',
    order: 10,
    description: 'Tokens, cached tokens, requests and GPU minutes per API key and model.',
    keywords: ['metering', 'usage', 'tokens', 'gpu minutes'],
  },
];
