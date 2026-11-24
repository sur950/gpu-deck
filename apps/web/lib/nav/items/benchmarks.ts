import { FlaskConical } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: benchmarks. */
export const benchmarksNav: NavEntry[] = [
  {
    id: 'benchmarks',
    title: 'Benchmarks',
    href: '/benchmarks',
    icon: FlaskConical,
    group: 'models',
    order: 40,
    description: 'Load tests against a deployment and the best concurrency for a target latency.',
    keywords: ['benchmark', 'load test', 'auto tune', 'slo'],
    breadcrumbs: { new: 'New benchmark', edit: 'Edit' },
    detailLabel: 'Benchmark',
    actions: [{ label: 'New benchmark', href: '/benchmarks/new', keywords: ['load test', 'auto tune', 'slo'] }],
  },
];
