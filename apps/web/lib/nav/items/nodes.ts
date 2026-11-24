import { Server } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: clusters / nodes, join flow. */
export const nodesNav: NavEntry[] = [
  {
    id: 'nodes',
    title: 'Nodes',
    href: '/nodes',
    icon: Server,
    group: 'fleet',
    order: 20,
    description: 'GPU machines running the node agent, with join tokens, drain and removal.',
    keywords: ['node', 'worker', 'machine', 'agent', 'join'],
    breadcrumbs: { join: 'Add a node' },
    detailLabel: 'Node details',
    actions: [{ label: 'Add a node (join token)', href: '/nodes/join', keywords: ['join', 'token', 'install', 'agent'] }],
  },
];
