import { SquareTerminal } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: SSH GPU instances on demand. */
export const gpuInstancesNav: NavEntry[] = [
  {
    id: 'gpu-instances',
    title: 'SSH instances',
    href: '/gpu-instances',
    icon: SquareTerminal,
    group: 'fleet',
    order: 35,
    description: 'Containers with sshd and a GPU (or a slice of one) attached, on demand, with a TTL.',
    keywords: ['ssh', 'gpu instance', 'container', 'shell', 'notebook', 'dev box', 'ttl'],
    breadcrumbs: { new: 'Request an instance' },
    detailLabel: 'Instance details',
    actions: [{ label: 'Request an SSH GPU instance', href: '/gpu-instances/new', icon: SquareTerminal, keywords: ['ssh', 'gpu', 'container', 'shell'] }],
  },
];
