import { HardDrive } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: model cache. */
export const modelCacheNav: NavEntry[] = [
  {
    id: 'model-cache',
    title: 'Model cache',
    href: '/model-cache',
    icon: HardDrive,
    group: 'models',
    order: 30,
    description: 'Model files kept on node disks: size, last use, prefetch and eviction.',
    keywords: ['cache', 'disk', 'weights', 'prefetch', 'evict'],
  },
];
