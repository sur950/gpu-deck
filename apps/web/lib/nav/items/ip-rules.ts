import { ShieldCheck } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: IP rules. */
export const ipRulesNav: NavEntry[] = [
  {
    id: 'ip-rules',
    title: 'IP rules',
    href: '/ip-rules',
    icon: ShieldCheck,
    group: 'security',
    order: 30,
    description: 'Allow and block lists for the admin UI and the API layer.',
    keywords: ['ip', 'allow list', 'block list', 'firewall'],
  },
];
