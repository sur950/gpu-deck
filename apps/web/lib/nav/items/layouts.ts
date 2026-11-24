import { LayoutTemplate } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: layouts editor. */
export const layoutsNav: NavEntry[] = [
  {
    id: 'layouts',
    title: 'Layouts',
    href: '/layouts',
    icon: LayoutTemplate,
    group: 'automation',
    order: 20,
    description: 'Named GPU layouts (for example Day and Night) that schedules switch between.',
    keywords: ['layout', 'day', 'night', 'schedule'],
    breadcrumbs: { new: 'New layout' },
    detailLabel: 'Layout details',
  },
];
