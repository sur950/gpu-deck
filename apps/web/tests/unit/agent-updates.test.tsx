import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AgentUpdateBadge, UpdateSteps } from '@/components/fleet/agent-update';
import {
  UPDATE_STATUS,
  describeWindow,
  isRunActive,
  needsAgentAttention,
  runStepIndex,
  stepTime,
  type AgentUpdateRun,
  type NodeAgentUpdate,
} from '@/types/agent-updates';

function run(state: AgentUpdateRun['state'], phases: string[] = []): AgentUpdateRun {
  return {
    id: 'r1',
    node_id: 'n1',
    to_version: '1.1.0',
    from_version: '1.0.0',
    state,
    trigger: 'manual',
    steps: phases.map((phase, i) => ({ phase, at: `2026-11-24T10:0${i}:00Z`, message: null })),
    health_timeout_seconds: 120,
    started_at: '2026-11-24T10:00:00Z',
    created_at: '2026-11-24T10:00:00Z',
  } as AgentUpdateRun;
}

function info(status: NodeAgentUpdate['status'], extra: Partial<NodeAgentUpdate> = {}): NodeAgentUpdate {
  return { status, mode: 'helper', update_available: status !== 'up_to_date', can_update: true, current_version: '1.0.0', target_version: '1.1.0', ...extra } as NodeAgentUpdate;
}

describe('agent update steps', () => {
  it('maps run states onto staged → swapping → restarting → re-adopting → health check → done', () => {
    expect(runStepIndex(run('staging'))).toBe(0);
    expect(runStepIndex(run('swapping'))).toBe(1);
    expect(runStepIndex(run('restarting'))).toBe(2);
    expect(runStepIndex(run('readopting'))).toBe(3);
    expect(runStepIndex(run('health_check'))).toBe(4);
    expect(runStepIndex(run('succeeded'))).toBe(6);
    // A rollback stops where it got to.
    expect(runStepIndex(run('rolled_back', ['pending', 'swapping', 'restarting', 'health_check', 'rolled_back']))).toBe(4);
    expect(runStepIndex(run('failed', ['pending', 'staging']))).toBe(0);
    expect(stepTime(run('succeeded', ['pending', 'swapping', 'succeeded']), 'done')).toBe('2026-11-24T10:02:00Z');
    expect(isRunActive('health_check')).toBe(true);
    expect(isRunActive('rolled_back')).toBe(false);
  });

  it('renders done, in-progress and rolled-back steps', () => {
    const { rerender } = render(<UpdateSteps run={run('restarting', ['pending', 'swapping', 'restarting'])} />);
    const current = screen.getByText('Restarting').closest('li');
    expect(current?.getAttribute('aria-current')).toBe('step');
    expect(screen.getByText('Re-adopting engines')).toBeTruthy();
    rerender(<UpdateSteps run={{ ...run('rolled_back', ['pending', 'swapping', 'restarting', 'health_check', 'rolled_back']), error_message: 'agent 1.1.0 did not report healthy' }} />);
    expect(screen.getByText('Rolled back')).toBeTruthy();
    expect(document.querySelector('[aria-current="step"]')).toBeNull();
  });
});

describe('agent update badges', () => {
  it('shows what needs attention and hides up-to-date nodes', () => {
    const { container, rerender } = render(<AgentUpdateBadge info={info('up_to_date', { update_available: false })} />);
    expect(container.textContent).toBe('');
    rerender(<AgentUpdateBadge info={info('staged')} />);
    expect(container.textContent).toContain('Update ready 1.1.0');
    rerender(<AgentUpdateBadge info={info('managed', { mode: 'managed', reason: 'Managed by Helm' })} />);
    expect(container.textContent).toContain('Managed by Helm');
    rerender(<AgentUpdateBadge info={info('manual', { mode: 'manual' })} />);
    expect(container.textContent).toContain('Update manually');
    expect(UPDATE_STATUS.updating.label).toBe('Updating');
    expect(needsAgentAttention(info('manual', { mode: 'manual' }))).toBe(true);
    expect(needsAgentAttention(info('up_to_date', { update_available: false }))).toBe(false);
  });

  it('describes maintenance windows', () => {
    expect(describeWindow({ days: ['sun', 'sat'], start: '02:00', end: '05:00', timezone: 'UTC' })).toBe('Sat, Sun 02:00-05:00 UTC');
    expect(describeWindow({ days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], start: '22:00', end: '02:00', timezone: 'Asia/Tokyo' })).toBe('Every day 22:00-02:00 next day Asia/Tokyo');
    expect(describeWindow(null)).toBe('No window');
  });
});
