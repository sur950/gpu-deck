import type { NavGroup } from '@/lib/nav/types';

/**
 * Sidebar sections in display order. Adding a page never touches this file:
 * drop a `NavEntry` with one of these `group` ids into `lib/nav/items/`.
 */
export const NAV_GROUPS: NavGroup[] = [
  { id: 'overview', title: 'Overview', headless: true },
  { id: 'fleet', title: 'Fleet' },
  { id: 'models', title: 'Models' },
  { id: 'automation', title: 'Automation' },
  { id: 'observability', title: 'Observability' },
  { id: 'security', title: 'Security' },
];
