'use client';

/**
 * Catalog + model + engine hooks — React Query wrappers over
 * lib/api/models.ts. Deployments, model cache and benchmarks have their own
 * files next to this one (hooks/api/models-*.ts).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/models';
import type {
  AutoPickRequest,
  CompatCheckListParams,
  EngineUpdate,
  EngineVersionCreate,
  EngineVersionUpdate,
  CatalogSearchParams,
  CompatCheckRequest,
  EngineListParams,
  LocalScanRequest,
  Model,
  ModelCreate,
  ModelListParams,
  ModelUpdate,
  ModelVersionCreate,
  RemoteSource,
} from '@/types/models';

export const modelKeys = {
  catalogSearch: (params: CatalogSearchParams) => ['catalog', 'search', params] as const,
  remoteFiles: (source: RemoteSource, repoId: string, revision?: string, credentialId?: string) =>
    ['catalog', 'remote-files', source, repoId, revision ?? '', credentialId ?? ''] as const,

  models: ['models'] as const,
  list: (params?: ModelListParams) => ['models', 'list', params] as const,
  detail: (id: string) => ['models', 'detail', id] as const,
  versions: (id: string) => ['models', 'detail', id, 'versions'] as const,
  files: (id: string, versionId: string) => ['models', 'detail', id, 'versions', versionId, 'files'] as const,
  versionCache: (id: string, versionId: string) => ['models', 'detail', id, 'versions', versionId, 'cache'] as const,

  engines: (params?: EngineListParams) => ['engines', params] as const,
  enginesAll: ['engines'] as const,
  compatChecks: (params?: CompatCheckListParams) => ['engines', 'compat-checks', params] as const,
  engineVersions: (engineId: string) => ['engines', engineId, 'versions'] as const,
  systemInfo: ['system', 'info'] as const,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

// ─── Catalog ──────────────────────────────────────────────────────────────────

export function useCatalogSearch(params: CatalogSearchParams, enabled = true) {
  return useQuery({
    queryKey: modelKeys.catalogSearch(params),
    queryFn: () => api.searchCatalog(params),
    enabled: enabled && params.q.trim().length > 0,
    staleTime: 5 * 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useRemoteFiles(source: RemoteSource, repoId: string, revision?: string, credentialId?: string) {
  return useQuery({
    queryKey: modelKeys.remoteFiles(source, repoId, revision, credentialId),
    queryFn: () => api.listRemoteFiles(source, repoId, revision || undefined, credentialId || undefined),
    enabled: repoId.trim().length > 0,
    staleTime: 5 * 60_000,
  });
}

export function useScanLocalPath() {
  return useMutation({
    mutationFn: (body: LocalScanRequest) => api.scanLocalPath(body),
    onError: (error) => toast.error(`Scan failed: ${errorMessage(error)}`),
  });
}

// ─── Models ───────────────────────────────────────────────────────────────────

export function useModels(params: ModelListParams = {}, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: modelKeys.list(params),
    queryFn: () => api.listModels(params),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
    ...options,
  });
}

export function useModel(id: string) {
  return useQuery({
    queryKey: modelKeys.detail(id),
    queryFn: () => api.getModel(id),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useModelVersions(id: string) {
  return useQuery({
    queryKey: modelKeys.versions(id),
    queryFn: () => api.listModelVersions(id),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useModelFiles(modelId: string, versionId: string) {
  return useQuery({
    queryKey: modelKeys.files(modelId, versionId),
    queryFn: () => api.listModelFiles(modelId, versionId),
    enabled: !!modelId && !!versionId,
    staleTime: 60_000,
  });
}

export function useModelVersionCache(modelId: string, versionId: string) {
  return useQuery({
    queryKey: modelKeys.versionCache(modelId, versionId),
    queryFn: () => api.listModelVersionCache(modelId, versionId),
    enabled: !!modelId && !!versionId,
    staleTime: 15_000,
  });
}

export function useRegisterModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelCreate) => api.registerModel(body),
    onSuccess: (model) => {
      qc.setQueryData(modelKeys.detail(model.id), model);
      qc.invalidateQueries({ queryKey: modelKeys.models });
      toast.success(`Model "${model.name}" registered`);
    },
    onError: (error) => toast.error(`Could not register the model: ${errorMessage(error)}`),
  });
}

export function useUpdateModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ModelUpdate }) => api.updateModel(id, body),
    onMutate: async ({ id, body }) => {
      await qc.cancelQueries({ queryKey: modelKeys.detail(id) });
      const previous = qc.getQueryData<Model>(modelKeys.detail(id));
      if (previous) qc.setQueryData<Model>(modelKeys.detail(id), { ...previous, ...body } as Model);
      return { previous };
    },
    onError: (error, { id }, ctx) => {
      if (ctx?.previous) qc.setQueryData(modelKeys.detail(id), ctx.previous);
      toast.error(`Could not update the model: ${errorMessage(error)}`);
    },
    onSuccess: (model) => {
      qc.setQueryData(modelKeys.detail(model.id), model);
      toast.success('Model updated');
    },
    onSettled: () => qc.invalidateQueries({ queryKey: modelKeys.models }),
  });
}

export function useDeleteModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, evictCache }: { id: string; evictCache?: boolean }) => api.deleteModel(id, evictCache),
    onSuccess: (_, { id }) => {
      qc.removeQueries({ queryKey: modelKeys.detail(id) });
      qc.invalidateQueries({ queryKey: modelKeys.models });
      toast.success('Model removed from the catalog');
    },
    onError: (error) => toast.error(`Could not delete the model: ${errorMessage(error)}`),
  });
}

export function useAddModelVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, body }: { modelId: string; body: ModelVersionCreate }) => api.addModelVersion(modelId, body),
    onSuccess: (_, { modelId }) => {
      qc.invalidateQueries({ queryKey: modelKeys.detail(modelId) });
      qc.invalidateQueries({ queryKey: modelKeys.models });
      toast.success('Version added');
    },
    onError: (error) => toast.error(`Could not add the version: ${errorMessage(error)}`),
  });
}

export function useUpdateModelVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      modelId,
      versionId,
      body,
    }: {
      modelId: string;
      versionId: string;
      body: { is_default?: boolean; quantization?: string | null; version?: string };
    }) => api.updateModelVersion(modelId, versionId, body),
    onSuccess: (_, { modelId }) => {
      qc.invalidateQueries({ queryKey: modelKeys.detail(modelId) });
      toast.success('Version updated');
    },
    onError: (error) => toast.error(`Could not update the version: ${errorMessage(error)}`),
  });
}

export function useDeleteModelVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, versionId }: { modelId: string; versionId: string }) => api.deleteModelVersion(modelId, versionId),
    onSuccess: (_, { modelId }) => {
      qc.invalidateQueries({ queryKey: modelKeys.detail(modelId) });
      toast.success('Version deleted');
    },
    onError: (error) => toast.error(`Could not delete the version: ${errorMessage(error)}`),
  });
}

export function useVerifyModelVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, versionId }: { modelId: string; versionId: string }) => api.verifyModelVersion(modelId, versionId),
    onSuccess: (result, { modelId }) => {
      qc.invalidateQueries({ queryKey: modelKeys.detail(modelId) });
      if (result.pickle_blocked) {
        toast.warning('Verification found pickle weights — the version is blocked until pickle is allowed on the model');
      } else if ((result.mismatches?.length ?? 0) > 0) {
        toast.error(`${result.mismatches?.length} file(s) failed their hash check`);
      } else {
        toast.success(`${result.files_checked} file(s) verified`);
      }
    },
    onError: (error) => toast.error(`Verification failed: ${errorMessage(error)}`),
  });
}

// ─── Engines and compatibility ────────────────────────────────────────────────

export function useEngines(params: EngineListParams = {}) {
  return useQuery({
    queryKey: modelKeys.engines(params),
    queryFn: async () => (await api.listEngines(params)).items,
    staleTime: 5 * 60_000,
  });
}

export function useEngineVersions(engineId: string) {
  return useQuery({
    queryKey: modelKeys.engineVersions(engineId),
    queryFn: async () => (await api.listEngineVersions(engineId)).items,
    enabled: !!engineId,
    staleTime: 5 * 60_000,
  });
}

export function useCompatCheck() {
  return useMutation({
    mutationFn: (body: CompatCheckRequest) => api.runCompatCheck(body),
    onError: (error) => toast.error(`Compatibility check failed: ${errorMessage(error)}`),
  });
}

export function useAutoPickEngine() {
  return useMutation({
    mutationFn: (body: AutoPickRequest) => api.autoPickEngine(body),
    onError: (error) => toast.error(`Auto-pick failed: ${errorMessage(error)}`),
  });
}

export function useCompatChecks(params: CompatCheckListParams = {}, enabled = true) {
  return useQuery({
    queryKey: modelKeys.compatChecks(params),
    queryFn: () => api.listCompatChecks(params),
    enabled,
    staleTime: 30_000,
  });
}

export function useUpdateEngine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: EngineUpdate }) => api.updateEngine(id, body),
    onSuccess: (engine) => {
      qc.invalidateQueries({ queryKey: modelKeys.enginesAll });
      toast.success(`${engine.display_name} ${engine.is_enabled ? 'enabled' : 'disabled'}`);
    },
    onError: (error) => toast.error(`Could not update the engine: ${errorMessage(error)}`),
  });
}

export function useCreateEngineVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ engineId, body }: { engineId: string; body: EngineVersionCreate }) => api.createEngineVersion(engineId, body),
    onSuccess: (version) => {
      qc.invalidateQueries({ queryKey: modelKeys.enginesAll });
      toast.success(`Engine version ${version.version} added`);
    },
    onError: (error) => toast.error(`Could not add the version: ${errorMessage(error)}`),
  });
}

export function useUpdateEngineVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ engineId, versionId, body }: { engineId: string; versionId: string; body: EngineVersionUpdate }) =>
      api.updateEngineVersion(engineId, versionId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: modelKeys.enginesAll });
      toast.success('Engine version updated');
    },
    onError: (error) => toast.error(`Could not update the version: ${errorMessage(error)}`),
  });
}

export function useDeleteEngineVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ engineId, versionId }: { engineId: string; versionId: string }) => api.deleteEngineVersion(engineId, versionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: modelKeys.enginesAll });
      toast.success('Engine version deleted');
    },
    onError: (error) => toast.error(`Could not delete the version: ${errorMessage(error)}`),
  });
}

// ─── System ───────────────────────────────────────────────────────────────────

export function useSystemInfo() {
  return useQuery({
    queryKey: modelKeys.systemInfo,
    queryFn: () => api.getSystemInfo(),
    staleTime: 10 * 60_000,
  });
}
