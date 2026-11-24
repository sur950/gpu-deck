import { Cpu } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: fleet. */
export const gpusNav: NavEntry[] = [
  {
    id: 'gpus',
    title: 'GPUs',
    href: '/gpus',
    icon: Cpu,
    group: 'fleet',
    order: 30,
    description: 'Every accelerator in the fleet with VRAM, driver, slices and health.',
    keywords: ['gpu', 'accelerator', 'device', 'vram', 'slice'],
    detailLabel: 'GPU details',
  },
];
