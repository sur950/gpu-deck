/**
 * Agent update API (agent protocol §14): node update status, the Update button, rolling updates
 * per cluster, the cluster policy and the run history. Paths follow
 * libs/contracts/openapi/admin.yaml (Agent updates tag).
 */

import { apiGet, apiPost, apiPut } from '@/lib/api/client';
import { apiList } from '@/lib/api/fleet';
import type {
  AgentRollout,
  AgentRolloutRequest,
  AgentStage,
  AgentUpdateOverview,
  AgentUpdatePolicy,
  AgentUpdateRequest,
  AgentUpdateRun,
  ClusterAgentUpdate,
  NodeAgentUpdate,
} from '@/types/agent-updates';

export const getAgentUpdateOverview = () => apiGet<AgentUpdateOverview>('/agent-updates');

export const listAgentUpdateRuns = (params: { node_id?: string; cluster_id?: string; rollout_id?: string; page_size?: number } = {}) =>
  apiList<AgentUpdateRun>('/agent-updates/runs', params);

export const getNodeAgentUpdate = (nodeId: string) => apiGet<NodeAgentUpdate>(`/nodes/${nodeId}/agent-update`);

export const updateNodeAgent = (nodeId: string, body: AgentUpdateRequest = {}) => apiPost<AgentUpdateRun>(`/nodes/${nodeId}/agent-update`, body);

export const stageNodeAgentUpdate = (nodeId: string, body: AgentUpdateRequest = {}) => apiPost<AgentStage>(`/nodes/${nodeId}/agent-update/stage`, body);

export const getClusterAgentUpdate = (clusterId: string) => apiGet<ClusterAgentUpdate>(`/clusters/${clusterId}/agent-update`);

export const startClusterAgentRollout = (clusterId: string, body: AgentRolloutRequest = {}) =>
  apiPost<AgentRollout>(`/clusters/${clusterId}/agent-update`, body);

export const setClusterAgentUpdatePolicy = (clusterId: string, body: AgentUpdatePolicy) =>
  apiPut<AgentUpdatePolicy>(`/clusters/${clusterId}/agent-update/policy`, body);

export const cancelAgentRollout = (rolloutId: string) => apiPost<AgentRollout>(`/agent-updates/rollouts/${rolloutId}/cancel`);

export const getAgentRollout = (rolloutId: string) => apiGet<AgentRollout>(`/agent-updates/rollouts/${rolloutId}`);
