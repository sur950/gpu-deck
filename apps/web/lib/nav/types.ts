import type { LucideIcon } from 'lucide-react';

/** Sidebar sections, in display order (see lib/nav/groups.ts). */
export type NavGroupId =
  | 'overview'
  | 'fleet'
  | 'models'
  | 'automation'
  | 'observability'
  | 'security';

/** A ⌘K command that jumps somewhere, e.g. "Create deployment". */
export interface NavAction {
  label: string;
  href: string;
  icon?: LucideIcon;
  /** Extra search terms for the command palette. */
  keywords?: string[];
}

/**
 * One sidebar entry. A feature declares its entries in
 * `lib/nav/items/<feature>.ts`; the registry stitches them into the sidebar,
 * breadcrumbs and the ⌘K palette, so no shared file changes per feature.
 */
export interface NavEntry {
  /** Stable kebab-case id, e.g. 'deployments'. */
  id: string;
  /** Sidebar label. */
  title: string;
  href: string;
  icon: LucideIcon;
  group: NavGroupId;
  /** Position inside the group; lower first. Defaults to 100. */
  order?: number;
  /** Default page subtitle and the ⌘K hint. */
  description?: string;
  /** Extra ⌘K search terms. */
  keywords?: string[];
  /**
   * Breadcrumb labels for static segments nested under this page,
   * e.g. `{ new: 'New deployment', edit: 'Edit' }`.
   */
  breadcrumbs?: Record<string, string>;
  /** Breadcrumb label for a dynamic `[id]` segment under this page, e.g. 'Deployment details'. */
  detailLabel?: string;
  /** Extra ⌘K commands offered by this feature. */
  actions?: NavAction[];
}

export interface NavGroup {
  id: NavGroupId;
  title: string;
  /** Render items at the top of the sidebar with no section header (Overview). */
  headless?: boolean;
}

export interface NavSection extends NavGroup {
  items: NavEntry[];
}
