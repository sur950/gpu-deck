import { MemoryStick, Network } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: topology map. */
export const topologyNav: NavEntry[] = [
  {
    id: 'topology',
    title: 'Topology',
    href: '/topology',
    icon: Network,
    group: 'fleet',
    order: 40,
    description: 'Live map of clusters, nodes, GPUs and the model instances placed on them.',
    keywords: ['map', 'graph', 'placement'],
    actions: [{ label: 'Topology by VRAM in use', href: '/topology?color=vram', icon: MemoryStick, keywords: ['vram', 'memory', 'map'] }],
  },
];
