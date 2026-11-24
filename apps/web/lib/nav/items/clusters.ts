import { Boxes } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: clusters / nodes, join flow. */
export const clustersNav: NavEntry[] = [
  {
    id: 'clusters',
    title: 'Clusters',
    href: '/clusters',
    icon: Boxes,
    group: 'fleet',
    order: 10,
    description: 'Bare metal, VM, Kubernetes and cloud clusters managed by this control plane.',
    keywords: ['cluster', 'kubernetes', 'cloud', 'aws', 'azure', 'gcp', 'digitalocean'],
    breadcrumbs: { new: 'New cluster', edit: 'Edit' },
    detailLabel: 'Cluster details',
    actions: [{ label: 'New cluster', href: '/clusters/new', keywords: ['create', 'cluster'] }],
  },
];
