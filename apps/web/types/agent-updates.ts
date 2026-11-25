/**
 * Agent self-update types (agent protocol §14): contract aliases plus the UI helpers that turn a
 * node's update status and a run's steps into labels, tones and progress.
 */

import type { components } from '@/lib/api/generated/admin';

type S = components['schemas'];

export type NodeAgentUpdate = S['NodeAgentUpdate'];
export type NodeAgentUpdateStatus = NodeAgentUpdate['status'];
export type NodeAgentUpdateMode = NodeAgentUpdate['mode'];
export type AgentUpdateRun = S['AgentUpdateRun'];
export type AgentUpdateRunState = S['agent_update_state'];
export type AgentUpdateStep = S['AgentUpdateStep'];
export type AgentRollout = S['AgentRollout'];
export type AgentUpdatePolicy = S['AgentUpdatePolicy'];
export type AgentUpdateWindow = S['AgentUpdateWindow'];
export type ClusterAgentUpdate = S['ClusterAgentUpdate'];
export type AgentUpdateOverview = S['AgentUpdateOverview'];
export type AgentUpdateCounts = S['AgentUpdateCounts'];
export type AgentUpdateRequest = S['AgentUpdateRequest'];
export type AgentRolloutRequest = S['AgentRolloutRequest'];
export type AgentStage = S['AgentStage'];

export type Tone = 'success' | 'warning' | 'error' | 'info' | 'default';

export const UPDATE_STATUS: Record<NodeAgentUpdateStatus, { label: string; tone: Tone }> = {
  disabled: { label: 'Updates off', tone: 'default' },
  up_to_date: { label: 'Up to date', tone: 'success' },
  update_available: { label: 'Update available', tone: 'info' },
  staging: { label: 'Downloading update', tone: 'info' },
  staged: { label: 'Update ready', tone: 'info' },
  updating: { label: 'Updating', tone: 'warning' },
  managed: { label: 'Managed by Helm', tone: 'default' },
  manual: { label: 'Update manually', tone: 'warning' },
  unsupported: { label: 'Update manually', tone: 'warning' },
};

export const UPDATE_MODE_LABELS: Record<NodeAgentUpdateMode, string> = {
  helper: 'Update helper (root-owned binary)',
  self: 'Agent-owned binary',
  managed: 'Kubernetes DaemonSet (Helm)',
  unsupported: 'Cannot replace its binary',
  manual: 'Agent predates self-update',
};

/** The steps every update goes through, in order (the outcome is the last one). */
export const UPDATE_STEPS = [
  { id: 'staged', title: 'Staged', description: 'Verified binary on the node' },
  { id: 'swapping', title: 'Swapping', description: 'Atomic swap, previous kept' },
  { id: 'restarting', title: 'Restarting', description: 'Engines keep serving' },
  { id: 'readopting', title: 'Re-adopting engines', description: 'Same processes and ports' },
  { id: 'health_check', title: 'Health check', description: 'Reconnected and healthy' },
  { id: 'done', title: 'Done', description: 'Running the new version' },
] as const;

const STEP_INDEX: Record<string, number> = {
  pending: 0,
  staging: 0,
  staged: 1,
  swapping: 1,
  restarting: 2,
  readopting: 3,
  health_check: 4,
  succeeded: 6,
};

export const ACTIVE_RUN_STATES: AgentUpdateRunState[] = ['pending', 'staging', 'staged', 'swapping', 'restarting', 'readopting', 'health_check'];

export function isRunActive(state: AgentUpdateRunState | null | undefined): boolean {
  return !!state && ACTIVE_RUN_STATES.includes(state);
}

/**
 * Index of the step in progress for `UPDATE_STEPS` (steps before it are done). A finished run
 * points past the last step; a rolled back / failed run points at the step it stopped in.
 */
export function runStepIndex(run: Pick<AgentUpdateRun, 'state' | 'steps'>): number {
  if (run.state in STEP_INDEX) return STEP_INDEX[run.state];
  // rolled_back / failed / cancelled: the furthest phase it reached.
  let furthest = 0;
  for (const s of run.steps ?? []) {
    const i = STEP_INDEX[s.phase];
    if (i !== undefined && i < 6) furthest = Math.max(furthest, i);
  }
  return furthest;
}

/** When a phase was first reached (for the step timestamps). */
export function stepTime(run: Pick<AgentUpdateRun, 'steps'>, phase: string): string | null {
  const map: Record<string, string[]> = {
    staged: ['staged'],
    swapping: ['swapping'],
    restarting: ['restarting'],
    readopting: ['readopting'],
    health_check: ['health_check'],
    done: ['succeeded'],
  };
  const want = map[phase] ?? [phase];
  return run.steps?.find((s) => want.includes(s.phase))?.at ?? null;
}

export const RUN_OUTCOME: Partial<Record<AgentUpdateRunState, { label: string; tone: Tone }>> = {
  succeeded: { label: 'Updated', tone: 'success' },
  rolled_back: { label: 'Rolled back', tone: 'warning' },
  failed: { label: 'Failed', tone: 'error' },
  cancelled: { label: 'Cancelled', tone: 'default' },
};

export const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
export type Weekday = (typeof WEEKDAYS)[number];
export const WEEKDAY_LABELS: Record<Weekday, string> = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };

/** "Sat, Sun 02:00-05:00 UTC" (overnight windows read "22:00-02:00 next day"). */
export function describeWindow(w: AgentUpdateWindow | null | undefined): string {
  if (!w) return 'No window';
  const days = w.days.length === 7 ? 'Every day' : WEEKDAYS.filter((d) => w.days.includes(d)).map((d) => WEEKDAY_LABELS[d]).join(', ');
  const overnight = w.end < w.start ? ' next day' : '';
  return `${days} ${w.start}-${w.end}${overnight} ${w.timezone ?? 'UTC'}`;
}

/** A node needs attention for its agent version (shown in lists). */
export function needsAgentAttention(info: NodeAgentUpdate | null | undefined): boolean {
  return !!info && ['update_available', 'staged', 'manual', 'unsupported'].includes(info.status) && info.update_available;
}
