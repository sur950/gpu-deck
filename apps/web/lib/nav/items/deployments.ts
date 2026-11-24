import { Rocket } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: deployments wizard. */
export const deploymentsNav: NavEntry[] = [
  {
    id: 'deployments',
    title: 'Deployments',
    href: '/deployments',
    icon: Rocket,
    group: 'models',
    order: 20,
    description: 'Model deployments: engine, replicas, GPU placement, launch flags and instances.',
    keywords: ['deploy', 'instance', 'replica', 'engine', 'llama.cpp', 'vllm', 'mlx'],
    breadcrumbs: { new: 'New deployment', edit: 'Edit' },
    detailLabel: 'Deployment details',
    actions: [{ label: 'New deployment', href: '/deployments/new', keywords: ['create', 'deploy'] }],
  },
];
