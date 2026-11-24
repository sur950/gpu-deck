/**
 * Navigation registry — the single list the sidebar, mobile drawer,
 * breadcrumbs and ⌘K palette are built from.
 *
 * Extension rule for feature work: declare pages in your own
 * `lib/nav/items/<feature>.ts` (one file per feature, already pre-created for
 * every planned screen). This file only imports those lists; it does not need
 * editing to add nested routes, breadcrumb labels or ⌘K actions — put them on
 * the feature's `NavEntry` (`breadcrumbs`, `detailLabel`, `actions`).
 */

import { NAV_GROUPS } from '@/lib/nav/groups';
import type { NavEntry, NavSection } from '@/lib/nav/types';

import { dashboardNav } from '@/lib/nav/items/dashboard';
import { clustersNav } from '@/lib/nav/items/clusters';
import { nodesNav } from '@/lib/nav/items/nodes';
import { gpusNav } from '@/lib/nav/items/gpus';
import { gpuInstancesNav } from '@/lib/nav/items/gpu-instances';
import { cloudNav } from '@/lib/nav/items/cloud';
import { topologyNav } from '@/lib/nav/items/topology';
import { catalogNav } from '@/lib/nav/items/catalog';
import { deploymentsNav } from '@/lib/nav/items/deployments';
import { modelCacheNav } from '@/lib/nav/items/model-cache';
import { benchmarksNav } from '@/lib/nav/items/benchmarks';
import { rulesNav } from '@/lib/nav/items/rules';
import { layoutsNav } from '@/lib/nav/items/layouts';
import { swapsNav } from '@/lib/nav/items/swaps';
import { alertsNav } from '@/lib/nav/items/alerts';
import { observabilityNav } from '@/lib/nav/items/observability';
import { meteringNav } from '@/lib/nav/items/metering';
import { billingNav } from '@/lib/nav/items/billing';
import { apiKeysNav } from '@/lib/nav/items/api-keys';
import { credentialsNav } from '@/lib/nav/items/credentials';
import { auditLogNav } from '@/lib/nav/items/audit-log';
import { ipRulesNav } from '@/lib/nav/items/ip-rules';
import { accountNav } from '@/lib/nav/items/account';

/** Every registered page, in registration order (grouping/sorting happens below). */
export const NAV_ENTRIES: NavEntry[] = [
  ...dashboardNav,
  ...clustersNav,
  ...nodesNav,
  ...gpusNav,
  ...gpuInstancesNav,
  ...cloudNav,
  ...topologyNav,
  ...catalogNav,
  ...deploymentsNav,
  ...modelCacheNav,
  ...benchmarksNav,
  ...rulesNav,
  ...layoutsNav,
  ...swapsNav,
  ...alertsNav,
  ...observabilityNav,
  ...meteringNav,
  ...billingNav,
  ...apiKeysNav,
  ...credentialsNav,
  ...auditLogNav,
  ...ipRulesNav,
];

/**
 * Pages that are reachable (breadcrumbs, ⌘K) but have no sidebar row,
 * e.g. the account page behind the profile menu.
 */
export const HIDDEN_ENTRIES: NavEntry[] = [...accountNav];

const ALL_ENTRIES: NavEntry[] = [...NAV_ENTRIES, ...HIDDEN_ENTRIES];

if (process.env.NODE_ENV !== 'production') {
  const seen = new Set<string>();
  for (const entry of ALL_ENTRIES) {
    if (seen.has(entry.href)) {
      console.error(`[nav] duplicate href registered: ${entry.href} (${entry.id})`);
    }
    seen.add(entry.href);
  }
}

/** Sidebar sections: groups in NAV_GROUPS order, items sorted by `order` then title. */
export const NAV_SECTIONS: NavSection[] = NAV_GROUPS.map((group) => ({
  ...group,
  items: NAV_ENTRIES.filter((e) => e.group === group.id).sort(
    (a, b) => (a.order ?? 100) - (b.order ?? 100) || a.title.localeCompare(b.title),
  ),
})).filter((section) => section.items.length > 0);

/** Every sidebar href — used by the active-route rule. */
export const ALL_NAV_HREFS: string[] = NAV_ENTRIES.map((e) => e.href);

/** The entry whose href is a prefix of `pathname` (longest match wins). */
export function findNavEntry(pathname: string): NavEntry | undefined {
  let best: NavEntry | undefined;
  for (const entry of ALL_ENTRIES) {
    const matches = pathname === entry.href || pathname.startsWith(`${entry.href}/`);
    if (matches && (!best || entry.href.length > best.href.length)) best = entry;
  }
  return best;
}

/** Page titles by full href, e.g. `/observability/logs` → "Logs". */
const TITLE_BY_HREF: ReadonlyMap<string, string> = new Map(ALL_ENTRIES.map((e) => [e.href, e.title]));

/**
 * Dynamic route parameter: UUID, long hex id (OTel trace ids), slug-number or
 * number. Rendered with the owning page's `detailLabel`.
 */
export function isDynamicSegment(segment: string): boolean {
  return /^[a-f0-9-]{36}$|^[a-f0-9]{16,64}$|^[a-z]+-\d+$|^\d+$/i.test(segment);
}

function titleCase(segment: string): string {
  return segment
    .split('-')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/**
 * Breadcrumb label for `segments[index]` of a pathname.
 *
 * Labels are route-scoped: a registered href shows its page title; a nested
 * static segment (`new`, `edit`, `reports`…) is looked up only in the
 * `breadcrumbs` of the page that owns the parent path, so `/rules/new` and
 * `/api-keys/new` never borrow each other's label; an id shows the owner's
 * `detailLabel`. Anything else is title-cased.
 */
export function breadcrumbLabel(segments: string[], index: number): string {
  const segment = segments[index] ?? '';
  const parentPath = `/${segments.slice(0, index).join('/')}`;
  if (isDynamicSegment(segment)) return findNavEntry(parentPath)?.detailLabel ?? 'Details';

  const title = TITLE_BY_HREF.get(`/${segments.slice(0, index + 1).join('/')}`);
  if (title) return title;

  const nested = index > 0 ? findNavEntry(parentPath)?.breadcrumbs?.[segment] : undefined;
  return nested ?? titleCase(segment);
}

/**
 * Active-route rule: a link is active when the
 * path starts with its href, unless a longer sibling href matches too (so
 * /observability/logs lights "Logs", not a parent).
 */
export function isNavItemActive(href: string, pathname: string, allHrefs: string[] = ALL_NAV_HREFS): boolean {
  if (href === '/dashboard') return pathname === '/dashboard';
  const hasActiveDescendant = allHrefs.some(
    (h) => h !== href && h.startsWith(href + '/') && pathname.startsWith(h),
  );
  if (hasActiveDescendant) return false;
  return pathname === href || pathname.startsWith(href + '/');
}
