import { KeyRound } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: API keys. */
export const apiKeysNav: NavEntry[] = [
  {
    id: 'api-keys',
    title: 'API keys',
    href: '/api-keys',
    icon: KeyRound,
    group: 'security',
    order: 10,
    description: 'Keys your AI gateway or other clients use against the API layer. Shown once, revoked instantly.',
    keywords: ['api key', 'token', 'revoke'],
    breadcrumbs: { new: 'New API key' },
    detailLabel: 'API key details',
    actions: [{ label: 'New API key', href: '/api-keys/new', keywords: ['create', 'key'] }],
  },
];
