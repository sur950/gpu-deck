/**
 * Models API — catalog search / registration, model versions and files,
 * engines and compatibility checks, deployments and instances, the per-node
 * model cache and benchmarks. Paths follow libs/contracts/openapi/admin.yaml.
 */

import { apiDelete, apiGet, apiPatch, apiPost, apiPut, toQuery } from '@/lib/api/client';
import { apiList } from '@/lib/api/fleet';
import { getGatewayUrl } from '@/utils/gateway-url';
import type {
  ApplyRecommendationRequest,
  AutoPickRequest,
  AutoPickResult,
  Benchmark,
  BenchmarkCreate,
  BenchmarkListParams,
  BenchmarkRun,
  BenchmarkStartRequest,
  BenchmarkUpdate,
  CacheItem,
  CacheListParams,
  CacheSummary,
  CatalogRemoteFiles,
  CatalogSearchParams,
  CatalogSearchResult,
  CommandRefs,
  CompatCheck,
  CompatCheckListParams,
  CompatCheckRequest,
  Deployment,
  DeploymentCreate,
  DeploymentListParams,
  DeploymentUpdate,
  Engine,
  EngineListParams,
  EngineUpdate,
  EngineVersion,
  EngineVersionCreate,
  EngineVersionUpdate,
  EvictRequest,
  Instance,
  InstanceDrainRequest,
  InstanceListParams,
  LocalScanCandidate,
  LocalScanRequest,
  Model,
  ModelCreate,
  ModelFile,
  ModelListParams,
  ModelUpdate,
  ModelVersion,
  ModelVersionCreate,
  PlacementPreview,
  PlacementPreviewRequest,
  PrefetchRequest,
  RemoteSource,
  ScaleRequest,
  SessionsSnapshot,
  SwapOperation,
  SystemInfo,
  TrafficWeightsRequest,
  VerifyResult,
} from '@/types/models';

type Query = Record<string, string | number | boolean | null | undefined>;

function clean(params: object): Query {
  const out: Query = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === '' || v === undefined || v === null) continue;
    out[k] = v as string | number | boolean;
  }
  return out;
}

// ─── Catalog (remote search, remote files, local scan) ────────────────────────

export const searchCatalog = (params: CatalogSearchParams) => apiList<CatalogSearchResult>('/catalog/search', clean(params));
export const listRemoteFiles = (source: RemoteSource, repoId: string, revision?: string, credentialId?: string) =>
  apiGet<CatalogRemoteFiles>(`/catalog/remote-files${toQuery({ source, repo_id: repoId, revision, credential_id: credentialId || undefined })}`);
export const scanLocalPath = (body: LocalScanRequest) => apiList<LocalScanCandidate>('/catalog/local/scan', {}, body);

// ─── Models ───────────────────────────────────────────────────────────────────

export const listModels = (params: ModelListParams = {}) => apiList<Model>('/models', clean(params));
export const getModel = (id: string) => apiGet<Model>(`/models/${id}`);
export const registerModel = (body: ModelCreate) => apiPost<Model>('/models', body);
export const updateModel = (id: string, body: ModelUpdate) => apiPatch<Model>(`/models/${id}`, body);
export const deleteModel = (id: string, evictCache = false) =>
  apiDelete<null>(`/models/${id}${toQuery({ evict_cache: evictCache || undefined })}`);

export const listModelVersions = (modelId: string) => apiList<ModelVersion>(`/models/${modelId}/versions`);
export const getModelVersion = (modelId: string, versionId: string) =>
  apiGet<ModelVersion>(`/models/${modelId}/versions/${versionId}`);
export const addModelVersion = (modelId: string, body: ModelVersionCreate) =>
  apiPost<ModelVersion>(`/models/${modelId}/versions`, body);
export const updateModelVersion = (
  modelId: string,
  versionId: string,
  body: { is_default?: boolean; quantization?: string | null; version?: string },
) =>
  apiPatch<ModelVersion>(`/models/${modelId}/versions/${versionId}`, body);
export const deleteModelVersion = (modelId: string, versionId: string) =>
  apiDelete<null>(`/models/${modelId}/versions/${versionId}`);
export const listModelFiles = (modelId: string, versionId: string) =>
  apiList<ModelFile>(`/models/${modelId}/versions/${versionId}/files`);
export const verifyModelVersion = (modelId: string, versionId: string) =>
  apiPost<VerifyResult>(`/models/${modelId}/versions/${versionId}/verify`);
export const listModelVersionCache = (modelId: string, versionId: string) =>
  apiList<CacheItem>(`/models/${modelId}/versions/${versionId}/cache`);

// ─── Engines and compatibility ────────────────────────────────────────────────

export const listEngines = (params: EngineListParams = {}) => apiList<Engine>('/engines', clean(params));
export const getEngine = (id: string) => apiGet<Engine>(`/engines/${id}`);
export const updateEngine = (id: string, body: EngineUpdate) => apiPatch<Engine>(`/engines/${id}`, body);
export const listEngineVersions = (engineId: string) => apiList<EngineVersion>(`/engines/${engineId}/versions`);
export const getEngineVersion = (engineId: string, versionId: string) =>
  apiGet<EngineVersion>(`/engines/${engineId}/versions/${versionId}`);
export const createEngineVersion = (engineId: string, body: EngineVersionCreate) =>
  apiPost<EngineVersion>(`/engines/${engineId}/versions`, body);
export const updateEngineVersion = (engineId: string, versionId: string, body: EngineVersionUpdate) =>
  apiPatch<EngineVersion>(`/engines/${engineId}/versions/${versionId}`, body);
export const deleteEngineVersion = (engineId: string, versionId: string) =>
  apiDelete<null>(`/engines/${engineId}/versions/${versionId}`);
export const runCompatCheck = (body: CompatCheckRequest) => apiList<CompatCheck>('/engines/compat-check', {}, body);
export const listCompatChecks = (params: CompatCheckListParams = {}) => apiList<CompatCheck>('/engines/compat-checks', clean(params));
export const autoPickEngine = (body: AutoPickRequest) => apiPost<AutoPickResult>('/engines/auto-pick', body);

// ─── Deployments ──────────────────────────────────────────────────────────────

export const listDeployments = (params: DeploymentListParams = {}) => apiList<Deployment>('/deployments', clean(params));
export const getDeployment = (id: string) => apiGet<Deployment>(`/deployments/${id}`);
export const createDeployment = (body: DeploymentCreate) => apiPost<Deployment>('/deployments', body);
export const updateDeployment = (id: string, body: DeploymentUpdate) => apiPatch<Deployment>(`/deployments/${id}`, body);
export const deleteDeployment = (id: string, force = false) =>
  apiDelete<CommandRefs | null>(`/deployments/${id}${toQuery({ force: force || undefined })}`);
export const previewPlacement = (body: PlacementPreviewRequest) => apiPost<PlacementPreview>('/deployments/preview', body);
export const scaleDeployment = (id: string, body: ScaleRequest) => apiPost<CommandRefs>(`/deployments/${id}/scale`, body);
export const restartDeployment = (id: string, body: InstanceDrainRequest = { timeout_seconds: 120, then: 'restart' }) =>
  apiPost<CommandRefs>(`/deployments/${id}/restart`, body);
export const stopDeployment = (id: string) => apiPost<CommandRefs>(`/deployments/${id}/stop`);
export const startDeployment = (id: string) => apiPost<CommandRefs>(`/deployments/${id}/start`);
export const setTrafficWeights = (id: string, body: TrafficWeightsRequest) =>
  apiPut<Deployment>(`/deployments/${id}/traffic`, body);
export const listDeploymentInstances = (id: string, includeStopped = false) =>
  apiList<Instance>(`/deployments/${id}/instances`, { include_stopped: includeStopped || undefined });
export const listDeploymentSwaps = (id: string, params: { page?: number; page_size?: number } = {}) =>
  apiList<SwapOperation>(`/deployments/${id}/swaps`, clean(params));

// ─── Instances ────────────────────────────────────────────────────────────────

export const listInstances = (params: InstanceListParams = {}) => apiList<Instance>('/instances', clean(params));
export const getInstance = (id: string) => apiGet<Instance>(`/instances/${id}`);
export const updateInstance = (id: string, body: { traffic_weight: number }) => apiPatch<Instance>(`/instances/${id}`, body);
export const stopInstance = (id: string, force = false) =>
  apiDelete<CommandRefs | null>(`/instances/${id}${toQuery({ force: force || undefined })}`);
export const drainInstance = (id: string, body: InstanceDrainRequest) => apiPost<CommandRefs>(`/instances/${id}/drain`, body);
export const restartInstance = (id: string, body: InstanceDrainRequest = { timeout_seconds: 120, then: 'restart' }) =>
  apiPost<CommandRefs>(`/instances/${id}/restart`, body);
export const getInstanceSessions = (id: string) => apiGet<SessionsSnapshot>(`/instances/${id}/sessions`);

// ─── Model cache ──────────────────────────────────────────────────────────────

export const listCacheItems = (params: CacheListParams = {}) => apiList<CacheItem>('/model-cache', clean(params));
export const getCacheSummary = (clusterId?: string) =>
  apiList<CacheSummary>('/model-cache/summary', clean({ cluster_id: clusterId }));
export const prefetchModel = (body: PrefetchRequest) => apiPost<CommandRefs>('/model-cache/prefetch', body);
export const evictCache = (body: EvictRequest) => apiPost<CommandRefs>('/model-cache/evict', body);
export const deleteCacheItem = (id: string, force = false) =>
  apiDelete<CommandRefs>(`/model-cache/${id}${toQuery({ force: force || undefined })}`);
export const pinCacheItem = (id: string, reason?: string) => apiPost<CacheItem>(`/model-cache/${id}/pin`, { reason: reason ?? null });
export const unpinCacheItem = (id: string) => apiPost<CacheItem>(`/model-cache/${id}/unpin`);

// ─── Benchmarks ───────────────────────────────────────────────────────────────

export const listBenchmarks = (params: BenchmarkListParams = {}) => apiList<Benchmark>('/benchmarks', clean(params));
export const getBenchmark = (id: string) => apiGet<Benchmark>(`/benchmarks/${id}`);
export const createBenchmark = (body: BenchmarkCreate) => apiPost<Benchmark>('/benchmarks', body);
export const updateBenchmark = (id: string, body: BenchmarkUpdate) => apiPatch<Benchmark>(`/benchmarks/${id}`, body);
export const deleteBenchmark = (id: string) => apiDelete<null>(`/benchmarks/${id}`);
export const startBenchmark = (id: string, body: BenchmarkStartRequest = {}) => apiPost<BenchmarkRun>(`/benchmarks/${id}/start`, body);
export const autoTuneBenchmark = (id: string, body: BenchmarkStartRequest = {}) =>
  apiPost<BenchmarkRun>(`/benchmarks/${id}/auto-tune`, body);
export const listBenchmarkRuns = (id: string, params: { page?: number; page_size?: number } = {}) =>
  apiList<BenchmarkRun>(`/benchmarks/${id}/runs`, clean(params));
export const getBenchmarkRun = (runId: string) => apiGet<BenchmarkRun>(`/benchmark-runs/${runId}`);
export const cancelBenchmarkRun = (runId: string) => apiPost<BenchmarkRun>(`/benchmark-runs/${runId}/cancel`);
export const applyBenchmarkRecommendation = (runId: string, body: ApplyRecommendationRequest) =>
  apiPost<Deployment>(`/benchmark-runs/${runId}/apply`, body);
/** CSV attachment — opened in a new tab (the session cookie goes along). */
export const benchmarkRunExportUrl = (runId: string) => `${getGatewayUrl()}/benchmark-runs/${runId}/export`;

// ─── System ───────────────────────────────────────────────────────────────────

export const getSystemInfo = () => apiGet<SystemInfo>('/system/info');
