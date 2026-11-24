/**
 * One reading of a node's state for every screen (nodes list / detail, clusters, topology,
 * dashboard): the agent connection first, then the scheduling status.
 *
 * - agent gone (`detached`: offline / removed / heartbeat stale) → red "Detached" ("Detached ·
 *   drained" when a drain is recorded: a drained node that disconnects becomes `offline` and keeps
 *   its drain in drained_at / drain_requested_at);
 * - connected and `drained` → neutral "Drained" (monitor-only nodes are healthy, not down);
 * - connected and `online` → green "Online"; `draining` → amber; `unhealthy` → red.
 *
 * "Connected" (agent attached and reporting) is what the fleet counts as up: a drained node that
 * still reports metrics is connected, so "1/1 nodes connected" rather than "0/1 online".
 */

import { StatusBadge, type BadgeVariant } from '@/components/shared/StatusBadge';
import { formatRelativeTime } from '@/utils/format';
import type { components } from '@/lib/api/generated/admin';

type NodeStatus = components['schemas']['node_status'];

export interface NodeStateInput {
  status: NodeStatus;
  detached?: boolean | null;
  lastHeartbeatAt?: string | null;
  disconnectedAt?: string | null;
  /** A drain is recorded (drained_at / drain_requested_at): a disconnected drained node is `offline` with the drain kept. */
  drainRecorded?: boolean;
}

export interface NodeStateView {
  label: string;
  variant: BadgeVariant;
  /** Tailwind class for a status dot. */
  dot: string;
  /** Agent attached and reporting. */
  connected: boolean;
  detached: boolean;
  /** Longer text for tooltips. */
  title: string;
}

const STATUS_LABEL: Record<NodeStatus, string> = {
  pending: 'Pending',
  online: 'Online',
  offline: 'Offline',
  unhealthy: 'Unhealthy',
  draining: 'Draining',
  drained: 'Drained',
  removed: 'Removed',
};

/** Agent gone: the backend flag, or a status that implies it (older API responses). */
export function isNodeDetached(node: NodeStateInput): boolean {
  return Boolean(node.detached) || node.status === 'offline';
}

export function isNodeConnected(node: NodeStateInput): boolean {
  return !isNodeDetached(node) && node.status !== 'pending' && node.status !== 'removed';
}

export function nodeStateView(node: NodeStateInput): NodeStateView {
  const detached = isNodeDetached(node) && node.status !== 'removed';
  const seen = node.lastHeartbeatAt ?? node.disconnectedAt ?? null;
  if (detached) {
    const suffix = node.status === 'drained' || node.status === 'draining' ? ` · ${STATUS_LABEL[node.status].toLowerCase()}` : node.drainRecorded ? ' · drained' : '';
    return {
      label: `Detached${suffix}`,
      variant: 'error',
      dot: 'bg-error',
      connected: false,
      detached: true,
      title: `Agent disconnected${seen ? ` · last heartbeat ${formatRelativeTime(seen)}` : ''}. Values shown are the last known ones.`,
    };
  }
  switch (node.status) {
    case 'online':
      return { label: 'Online', variant: 'online', dot: 'bg-success', connected: true, detached: false, title: 'Agent connected; the scheduler can place work here.' };
    case 'drained':
      return { label: 'Drained', variant: 'inactive', dot: 'bg-muted-foreground/60', connected: true, detached: false, title: 'Agent connected and reporting; the scheduler places nothing here.' };
    case 'draining':
      return { label: 'Draining', variant: 'draining', dot: 'bg-warning', connected: true, detached: false, title: 'Agent connected; instances are being moved or unloaded.' };
    case 'unhealthy':
      return { label: 'Unhealthy', variant: 'error', dot: 'bg-error', connected: true, detached: false, title: 'The agent reports problems; new placements avoid this node.' };
    case 'pending':
      return { label: 'Pending', variant: 'pending', dot: 'bg-muted-foreground/40', connected: false, detached: false, title: 'Joined; waiting for the first inventory report.' };
    case 'removed':
      return { label: 'Removed', variant: 'expired', dot: 'bg-muted-foreground/40', connected: false, detached: false, title: 'Removed from the fleet.' };
    default:
      return { label: STATUS_LABEL[node.status] ?? node.status, variant: 'unknown', dot: 'bg-muted-foreground/40', connected: false, detached: false, title: '' };
  }
}

/** Snake-case API rows (`Node`) → the input shape. */
export function nodeStateInput(node: {
  status: NodeStatus;
  detached?: boolean | null;
  last_heartbeat_at?: string | null;
  disconnected_at?: string | null;
  drained_at?: string | null;
  drain_requested_at?: string | null;
}): NodeStateInput {
  return {
    status: node.status,
    detached: node.detached,
    lastHeartbeatAt: node.last_heartbeat_at,
    disconnectedAt: node.disconnected_at,
    drainRecorded: Boolean(node.drained_at ?? node.drain_requested_at),
  };
}

export function NodeStatusBadge({ node, className }: { node: NodeStateInput; className?: string }) {
  const view = nodeStateView(node);
  return (
    <span title={view.title} className="inline-flex">
      <StatusBadge variant={view.variant} className={className}>
        {view.label}
      </StatusBadge>
    </span>
  );
}
