/**
 * Models mocks: catalog search (Hugging Face / ModelScope), remote file
 * listings, local path scans (Ollama blobs, LM Studio, HF cache, plain),
 * registered models with versions and files, the engine registry with
 * compatibility checks and auto-pick, deployments (placement preview,
 * create, scale, restart, stop/start, traffic weights), instances, the model
 * cache and benchmarks with progressive runs.
 *
 * Deployments, instances and slices live in the fleet store
 * (`handlers/fleet.ts`) so node drains and GPU pages see the same records.
 */

import { GiB, IDS } from '@/lib/mocks/fixtures/fleet';
import { recentSwaps } from '@/lib/mocks/fixtures/activity';
import { hash32, hexId, mulberry32, round, uuid } from '@/lib/mocks/fixtures/random';
import { sessionsSnapshot } from '@/lib/mocks/fixtures/sessions';
import {
  deploymentView,
  deviceView,
  fleetStore,
  instanceView,
  newId,
  recordCommand,
  releaseInstance,
  removeWhere,
  unloadInstance,
} from '@/lib/mocks/handlers/fleet';
import { accepted, bodyOf, created, fail, noContent, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { MockHandler, MockRequest, Schemas } from '@/lib/mocks/types';

type Model = Schemas['Model'];
type ModelVersion = Schemas['ModelVersion'];
type ModelFile = Schemas['ModelFile'];
type Engine = Schemas['Engine'];
type EngineVersion = Schemas['EngineVersion'];
type CompatCheck = Schemas['CompatCheck'];
type Deployment = Schemas['Deployment'];
type DeploymentSpec = Schemas['DeploymentSpec'];
type Instance = Schemas['Instance'];
type PlacementCandidate = Schemas['PlacementCandidate'];
type CacheItem = Schemas['CacheItem'];
type Benchmark = Schemas['Benchmark'];
type BenchmarkRun = Schemas['BenchmarkRun'];
type BenchmarkResult = BenchmarkRun['results'][number];
type SwapOperation = Schemas['SwapOperation'];
type FileFormat = Schemas['model_file_format'];
type ModelType = Schemas['model_type'];
type EngineName = Schemas['engine_name'];
type Vendor = Schemas['device_vendor'];

const T0 = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();
const hoursAgo = (h: number) => iso(T0 - h * 3_600_000);
const daysAgo = (d: number) => hoursAgo(d * 24);
const sha = (label: string) => hexId(`sha:${label}`, 32) + hexId(`sha2:${label}`, 32);
const GB = 1_000_000_000;

// ─── Engines ────────────────────────────────────────────────────────────────

const engineId = (n: number) => `00000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;

interface EngineSeed {
  n: number;
  name: EngineName;
  display: string;
  runtime: Engine['runtime'];
  types: ModelType[];
  vendors: Vendor[];
  os: Schemas['node_os'][];
  formats: FileFormat[];
  caps: Record<string, unknown>;
  description: string;
  versions: { version: string; image?: string; status?: EngineVersion['status']; isDefault?: boolean; flags?: Record<string, unknown>; minDriver?: string }[];
}

const CAPS = (over: Record<string, unknown>) => ({
  router_mode: false,
  tensor_parallel: false,
  pipeline_parallel: false,
  pd_split: false,
  speculative_decoding: false,
  kv_sharing: false,
  slices: false,
  control_vectors: false,
  native_formats: ['openai'],
  streaming: true,
  ...over,
});

const ENGINE_SEEDS: EngineSeed[] = [
  { n: 1, name: 'llama_cpp', display: 'llama.cpp', runtime: 'native', types: ['chat', 'thinking', 'vision', 'embedding', 'rerank'], vendors: ['nvidia', 'amd', 'hygon', 'metax', 'moorethreads', 'thead', 'apple'], os: ['linux', 'macos'], formats: ['gguf'], caps: CAPS({ router_mode: true, speculative_decoding: true, slices: true, control_vectors: true }), description: 'GGUF on CUDA, ROCm, MUSA, Vulkan and Metal. Router mode for fast load/unload, per-slot metrics.', versions: [{ version: 'b8798', isDefault: true, flags: { '--jinja': true, '--metrics': true, '--slots': true, '-ngl': 999 } }, { version: 'b8650', status: 'deprecated', flags: { '--metrics': true } }] },
  { n: 2, name: 'vllm', display: 'vLLM', runtime: 'container', types: ['chat', 'thinking', 'vision', 'embedding', 'rerank'], vendors: ['nvidia', 'amd', 'hygon', 'metax', 'moorethreads'], os: ['linux'], formats: ['safetensors'], caps: CAPS({ tensor_parallel: true, pipeline_parallel: true, pd_split: true, speculative_decoding: true, kv_sharing: true, slices: true }), description: 'PagedAttention, continuous batching, TP/PP, prefill-decode split and LMCache KV sharing.', versions: [{ version: 'v0.11.2', image: 'vllm/vllm-openai:v0.11.2', isDefault: true, flags: { '--enable-prefix-caching': true }, minDriver: '550.54' }, { version: 'v0.10.2', image: 'vllm/vllm-openai:v0.10.2', flags: {}, minDriver: '535.104' }] },
  { n: 3, name: 'vllm_omni', display: 'vLLM-Omni', runtime: 'container', types: ['tts', 'image', 'video'], vendors: ['nvidia'], os: ['linux'], formats: ['safetensors'], caps: CAPS({ tensor_parallel: true, async_jobs: true }), description: 'Text to speech, image and video (Qwen3-TTS, Wan2.2, HunyuanVideo) behind OpenAI-style endpoints.', versions: [{ version: 'v0.11.0', image: 'vllm/vllm-omni:v0.11.0', isDefault: true }] },
  { n: 4, name: 'sglang', display: 'SGLang', runtime: 'container', types: ['chat', 'thinking', 'vision', 'embedding'], vendors: ['nvidia', 'amd'], os: ['linux'], formats: ['safetensors'], caps: CAPS({ tensor_parallel: true, pipeline_parallel: true, pd_split: true, speculative_decoding: true, kv_sharing: true, slices: true }), description: 'RadixAttention prefix cache, EAGLE speculative decoding, PD disaggregation.', versions: [{ version: 'v0.5.4', image: 'lmsysorg/sglang:v0.5.4-cu128', isDefault: true }] },
  { n: 5, name: 'sglang_diffusion', display: 'SGLang Diffusion', runtime: 'native', types: ['image', 'video'], vendors: ['nvidia', 'amd', 'apple'], os: ['linux', 'macos'], formats: ['safetensors'], caps: CAPS({ tensor_parallel: true, streaming: false }), description: 'Diffusion image and video generation; also runs on Apple Silicon.', versions: [{ version: '0.5.4', isDefault: true }] },
  { n: 6, name: 'tensorrt_llm', display: 'TensorRT-LLM', runtime: 'container', types: ['chat', 'thinking'], vendors: ['nvidia'], os: ['linux'], formats: ['safetensors'], caps: CAPS({ tensor_parallel: true, pipeline_parallel: true, pd_split: true, speculative_decoding: true }), description: 'NVIDIA-only compiled engines for the lowest latency on Hopper and Ada.', versions: [{ version: '1.1.0', image: 'nvcr.io/nvidia/tensorrt-llm/release:1.1.0', isDefault: true, minDriver: '570.00' }] },
  { n: 7, name: 'mlx', display: 'MLX (mlx_lm.server)', runtime: 'native', types: ['chat', 'thinking', 'vision'], vendors: ['apple'], os: ['macos'], formats: ['mlx', 'safetensors'], caps: CAPS({ speculative_decoding: true }), description: 'Apple Silicon unified memory. The loaded model is addressed as default_model; metrics derive from proxy counters.', versions: [{ version: 'mlx-lm 0.29.1', isDefault: true, flags: { '--max-tokens': 4096 } }] },
  { n: 8, name: 'mlx_audio', display: 'mlx-audio', runtime: 'native', types: ['stt', 'tts'], vendors: ['apple'], os: ['macos'], formats: ['mlx', 'safetensors'], caps: CAPS({}), description: 'Kokoro and Whisper on Apple Silicon with OpenAI-style audio endpoints.', versions: [{ version: '0.2.6', isDefault: true }] },
  { n: 9, name: 'whisper_cpp', display: 'whisper.cpp', runtime: 'native', types: ['stt'], vendors: ['nvidia', 'amd', 'apple'], os: ['linux', 'macos'], formats: ['gguf', 'other'], caps: CAPS({ native_formats: [], streaming: false }), description: 'Speech to text from ggml Whisper weights.', versions: [{ version: 'v1.8.2', isDefault: true }] },
  { n: 10, name: 'stable_diffusion_cpp', display: 'stable-diffusion.cpp', runtime: 'native', types: ['image'], vendors: ['nvidia', 'amd', 'apple'], os: ['linux', 'macos'], formats: ['gguf', 'safetensors'], caps: CAPS({ native_formats: [], streaming: false }), description: 'SD 1.x/2.x/XL, SD3 and FLUX image generation in plain C++.', versions: [{ version: 'master-337', isDefault: true }] },
  { n: 11, name: 'custom', display: 'Custom engine (container image)', runtime: 'container', types: ['chat', 'thinking', 'vision', 'embedding', 'rerank', 'stt', 'tts', 'image', 'video'], vendors: ['nvidia', 'amd', 'hygon', 'metax', 'moorethreads', 'thead', 'apple'], os: ['linux', 'macos'], formats: ['gguf', 'safetensors', 'mlx', 'pickle', 'other'], caps: CAPS({}), description: 'Any OpenAI-compatible server image plus a config that says how to start it, check health and read metrics.', versions: [] },
];

function engineVersionId(name: string, version: string) {
  return uuid(`engine-version:${name}:${version}`);
}

const engines: Engine[] = ENGINE_SEEDS.map((seed) => ({
  id: engineId(seed.n),
  name: seed.name,
  display_name: seed.display,
  description: seed.description,
  runtime: seed.runtime,
  is_builtin: true,
  is_enabled: seed.name !== 'tensorrt_llm',
  capabilities: seed.caps,
  supported_formats: seed.formats,
  supported_model_types: seed.types,
  supported_os: seed.os,
  supported_vendors: seed.vendors,
  created_at: daysAgo(21),
  updated_at: daysAgo(21),
}));

const engineVersions: EngineVersion[] = ENGINE_SEEDS.flatMap((seed) =>
  seed.versions.map((v) => ({
    id: engineVersionId(seed.name, v.version),
    engine_id: engineId(seed.n),
    engine_name: seed.name,
    version: v.version,
    status: v.status ?? 'available',
    image: v.image ?? null,
    is_default: !!v.isDefault,
    artifacts:
      seed.runtime === 'native'
        ? {
            'linux/x86_64': { url: `https://releases.gpudeck.internal/${seed.name}/${v.version}/linux-x86_64-cuda.tar.gz`, sha256: sha(`${seed.name}${v.version}l`), kind: 'tarball' },
            'macos/aarch64': { url: `https://releases.gpudeck.internal/${seed.name}/${v.version}/macos-arm64-metal.tar.gz`, sha256: sha(`${seed.name}${v.version}m`), kind: 'tarball' },
          }
        : {},
    launch_template:
      seed.name === 'llama_cpp'
        ? { command: ['llama-server', '--host', '127.0.0.1', '--port', '{{port}}', '-m', '{{model_path}}', '-c', '{{context_size}}'], env: {} }
        : seed.name === 'mlx'
          ? { command: ['mlx_lm.server', '--host', '127.0.0.1', '--port', '{{port}}', '--model', '{{model_path}}'], env: {} }
          : { command: ['python3', '-m', `${seed.name}.entrypoints.openai.api_server`, '--port', '{{port}}', '--model', '{{model_path}}'], env: {} },
    default_launch_flags: v.flags ?? {},
    health_config: { path: '/health', interval_seconds: 5, timeout_seconds: 3, healthy_statuses: [200] },
    metrics_config: seed.name === 'llama_cpp' ? { path: '/metrics', format: 'prometheus', slots_path: '/slots', scrape_interval_seconds: 2 } : seed.name === 'mlx' ? { derive_from_proxy: true } : { path: '/metrics', format: 'prometheus', scrape_interval_seconds: 2 },
    port_range: [10240, 10340],
    min_driver_version: v.minDriver ?? null,
    requirements: {},
    created_at: daysAgo(21),
    updated_at: daysAgo(21),
  })),
);

function engineView(engine: Engine): Engine {
  return { ...engine, versions: engineVersions.filter((v) => v.engine_id === engine.id) };
}

function engineOfVersion(versionId: string): { engine: Engine; version: EngineVersion } | null {
  const version = engineVersions.find((v) => v.id === versionId);
  const engine = version && engines.find((e) => e.id === version.engine_id);
  return version && engine ? { engine, version } : null;
}

// ─── Models, versions and files ─────────────────────────────────────────────

interface VersionSeed {
  version: string;
  format: FileFormat;
  quantization?: string | null;
  revision?: string | null;
  status?: ModelVersion['status'];
  statusMessage?: string | null;
  isDefault?: boolean;
  params?: number | null;
  files: [string, number, boolean?][];
  createdDaysAgo?: number;
  config?: Record<string, unknown>;
}

interface ModelSeed {
  id: string;
  name: string;
  display: string;
  source: Model['source'];
  repo: string;
  type: ModelType;
  family: string;
  priority: Model['priority'];
  toolParser?: string | null;
  context?: number | null;
  flags?: Record<string, unknown>;
  description?: string;
  tags?: string[];
  aliases?: string[];
  allowPickle?: boolean;
  createdDaysAgo: number;
  versions: VersionSeed[];
}

const MODEL_IDS = {
  qwen: IDS.models.qwen,
  gemma: IDS.models.gemma,
  nomic: IDS.models.nomic,
  tinyllama: uuid('model:tinyllama'),
  qwen8b: uuid('model:qwen3-8b'),
  llama70: uuid('model:llama-3.3-70b-awq'),
  qwenvl: uuid('model:qwen2.5-vl-7b'),
  whisper: uuid('model:whisper-large-v3-turbo'),
  dialo: uuid('model:dialogpt-medium'),
};

const SHARDS = (prefix: string, count: number, totalBytes: number): [string, number][] =>
  Array.from({ length: count }, (_, i) => [`${prefix}-${String(i + 1).padStart(5, '0')}-of-${String(count).padStart(5, '0')}.safetensors`, Math.round(totalBytes / count)]);

const MODEL_SEEDS: ModelSeed[] = [
  {
    id: MODEL_IDS.qwen, name: 'qwen3-0.6b', display: 'Qwen3 0.6B', source: 'huggingface', repo: 'Qwen/Qwen3-0.6B-GGUF', type: 'thinking', family: 'qwen3', priority: 'always_on', toolParser: 'hermes', context: 32768,
    flags: { '--jinja': true, '--reasoning-format': 'deepseek' }, description: 'Small thinking model for routing, classification and tool calls. Always on.', tags: ['tools', 'thinking', 'gguf'], aliases: ['qwen3-small'], createdDaysAgo: 6,
    versions: [
      { version: 'Q8_0', format: 'gguf', quantization: 'Q8_0', revision: 'a9a60d0', isDefault: true, params: 751_632_384, files: [['Qwen3-0.6B-Q8_0.gguf', 639_446_688]], config: { context_length: 40960, architecture: 'qwen3', n_layer: 28 } },
      { version: 'Q4_K_M', format: 'gguf', quantization: 'Q4_K_M', revision: 'a9a60d0', status: 'registered', params: 751_632_384, files: [['Qwen3-0.6B-Q4_K_M.gguf', 396_705_472]], createdDaysAgo: 2 },
    ],
  },
  {
    id: MODEL_IDS.gemma, name: 'gemma-3-1b', display: 'Gemma 3 1B IT (QAT 4-bit)', source: 'local_path', repo: '/Users/alice/.lmstudio/models/mlx-community/gemma-3-1b-it-qat-4bit', type: 'chat', family: 'gemma3', priority: 'evictable', toolParser: null, context: 32768,
    flags: { '--max-tokens': 4096 }, description: 'MLX build from LM Studio, referenced in place (not copied).', tags: ['mlx', 'apple'], createdDaysAgo: 3,
    versions: [{ version: 'qat-4bit', format: 'mlx', quantization: '4bit (QAT)', isDefault: true, params: 999_885_952, files: [['model.safetensors', 731_825_420], ['config.json', 1_643], ['tokenizer.json', 33_384_570], ['tokenizer_config.json', 1_156_999], ['special_tokens_map.json', 662]], config: { model_type: 'gemma3_text', max_position_embeddings: 32768 } }],
  },
  {
    id: MODEL_IDS.nomic, name: 'nomic-embed-text-v1.5', display: 'nomic-embed-text v1.5', source: 'local_path', repo: '/Users/alice/.lmstudio/models/nomic-ai/nomic-embed-text-v1.5-GGUF', type: 'embedding', family: 'nomic-bert', priority: 'normal', toolParser: null, context: 2048,
    flags: { '--embeddings': true, '--pooling': 'mean' }, description: 'Embeddings for the docs indexer (RAG pipeline).', tags: ['embeddings', 'rag'], createdDaysAgo: 5,
    versions: [{ version: 'Q8_0', format: 'gguf', quantization: 'Q8_0', isDefault: true, params: 136_731_648, files: [['nomic-embed-text-v1.5.Q8_0.gguf', 146_146_432]] }],
  },
  {
    id: MODEL_IDS.tinyllama, name: 'tinyllama', display: 'TinyLlama 1.1B Chat', source: 'local_path', repo: '/Users/alice/.ollama/models/manifests/registry.ollama.ai/library/tinyllama/latest', type: 'chat', family: 'llama', priority: 'evictable', toolParser: null, context: 2048,
    description: 'Ollama blob referenced in place — handy smoke-test model.', tags: ['ollama', 'smoke-test'], createdDaysAgo: 9,
    versions: [{ version: 'latest', format: 'gguf', quantization: 'Q4_0', isDefault: true, params: 1_100_048_384, files: [['blobs/sha256-2af3b81862c6be03c769683af18efdadb2c33f60ff32ab6f83e42c043d6c7816', 637_699_456]] }],
  },
  {
    id: MODEL_IDS.qwen8b, name: 'qwen3-8b', display: 'Qwen3 8B', source: 'huggingface', repo: 'Qwen/Qwen3-8B', type: 'thinking', family: 'qwen3', priority: 'normal', toolParser: 'hermes', context: 32768,
    flags: { '--reasoning-parser': 'deepseek_r1', '--enable-auto-tool-choice': true }, description: 'Agent model for overnight batch workflows (Night layout).', tags: ['agents', 'night-layout'], createdDaysAgo: 4,
    versions: [{ version: 'main', format: 'safetensors', revision: 'b968826', isDefault: true, params: 8_190_735_360, files: [...SHARDS('model', 5, 16_381_470_720), ['config.json', 728], ['tokenizer.json', 11_422_654], ['generation_config.json', 239]] }],
  },
  {
    id: MODEL_IDS.llama70, name: 'llama-3.3-70b-awq', display: 'Llama 3.3 70B Instruct (AWQ)', source: 'huggingface', repo: 'casperhansen/llama-3.3-70b-instruct-awq', type: 'chat', family: 'llama3', priority: 'normal', toolParser: 'llama3_json', context: 16384,
    flags: { '--quantization': 'awq_marlin' }, description: 'Prefetched for the Night layout; needs both L40S with TP=2.', tags: ['tp2', 'night-layout'], createdDaysAgo: 2,
    versions: [{ version: 'main', format: 'safetensors', quantization: 'AWQ 4-bit', revision: '0bd4c5e', isDefault: true, params: 70_553_706_496, files: [...SHARDS('model', 9, 39_771_930_624), ['config.json', 1_102], ['tokenizer.json', 17_209_920]] }],
  },
  {
    id: MODEL_IDS.qwenvl, name: 'qwen2.5-vl-7b', display: 'Qwen2.5-VL 7B Instruct', source: 'modelscope', repo: 'Qwen/Qwen2.5-VL-7B-Instruct', type: 'vision', family: 'qwen2.5-vl', priority: 'normal', toolParser: 'hermes', context: 32768,
    description: 'Document and screenshot understanding — from the ModelScope mirror.', tags: ['vision', 'modelscope'], createdDaysAgo: 1,
    versions: [{ version: 'master', format: 'safetensors', revision: 'master', isDefault: true, status: 'verifying', statusMessage: 'Hashing 5 shards on gpu-box-01', params: 8_292_166_656, files: [...SHARDS('model', 5, 16_584_333_312), ['config.json', 1_374], ['preprocessor_config.json', 350]] }],
  },
  {
    id: MODEL_IDS.whisper, name: 'whisper-large-v3-turbo', display: 'Whisper large-v3 turbo', source: 'huggingface', repo: 'ggerganov/whisper.cpp', type: 'stt', family: 'whisper', priority: 'evictable', toolParser: null, context: null,
    description: 'Meeting transcription via whisper.cpp.', tags: ['audio'], createdDaysAgo: 8,
    versions: [{ version: 'large-v3-turbo-q5_0', format: 'other', quantization: 'q5_0', revision: '5359861', isDefault: true, params: 809_000_000, files: [['ggml-large-v3-turbo-q5_0.bin', 574_041_195]] }],
  },
  {
    id: MODEL_IDS.dialo, name: 'dialogpt-medium', display: 'DialoGPT medium', source: 'huggingface', repo: 'microsoft/DialoGPT-medium', type: 'chat', family: 'gpt2', priority: 'evictable', toolParser: null, context: 1024,
    description: 'Legacy checkpoint kept to show the pickle guard.', tags: ['legacy'], allowPickle: false, createdDaysAgo: 12,
    versions: [{ version: 'main', format: 'pickle', revision: '7b40bb0', isDefault: true, status: 'blocked', statusMessage: 'Only pickle weights (pytorch_model.bin) — blocked until "Allow pickle" is enabled on the model', params: 354_823_168, files: [['pytorch_model.bin', 863_128_229], ['config.json', 642], ['vocab.json', 1_042_301]] }],
  },
];

const models: Model[] = [];
const versions: ModelVersion[] = [];
const files: ModelFile[] = [];

function versionIdOf(modelId: string, version: string) {
  return uuid(`model-version:${modelId}:${version}`);
}

function addVersion(modelId: string, seed: VersionSeed, createdAt: string): ModelVersion {
  const id = versionIdOf(modelId, seed.version);
  const versionFiles: ModelFile[] = seed.files.map(([path, size, required]) => ({
    id: uuid(`model-file:${id}:${path}`),
    model_version_id: id,
    path,
    size_bytes: size,
    format: /\.gguf$|blobs\//.test(path) ? 'gguf' : /\.safetensors$/.test(path) ? (seed.format === 'mlx' ? 'mlx' : 'safetensors') : /\.bin$/.test(path) && seed.format === 'pickle' ? 'pickle' : path.endsWith('.json') ? 'other' : seed.format,
    sha256: seed.status === 'registered' ? null : sha(`${id}:${path}`),
    is_required: required ?? (!path.endsWith('.json') || path === 'config.json' || path === 'tokenizer.json'),
    shard_index: /-(\d{5})-of-/.test(path) ? Number(/-(\d{5})-of-/.exec(path)?.[1]) : null,
    shard_count: /-of-(\d{5})/.test(path) ? Number(/-of-(\d{5})/.exec(path)?.[1]) : null,
    cached_on_nodes: 0,
  }));
  files.push(...versionFiles);
  const version: ModelVersion = {
    id,
    model_id: modelId,
    version: seed.version,
    revision: seed.revision ?? null,
    format: seed.format,
    quantization: seed.quantization ?? null,
    parameter_count: seed.params ?? null,
    total_size_bytes: versionFiles.reduce((s, f) => s + f.size_bytes, 0),
    file_count: versionFiles.length,
    status: seed.status ?? 'ready',
    status_message: seed.statusMessage ?? null,
    is_default: !!seed.isDefault,
    config: seed.config ?? {},
    created_at: createdAt,
    updated_at: createdAt,
  };
  versions.push(version);
  return version;
}

for (const seed of MODEL_SEEDS) {
  const createdAt = daysAgo(seed.createdDaysAgo);
  models.push({
    id: seed.id,
    name: seed.name,
    display_name: seed.display,
    description: seed.description ?? null,
    source: seed.source,
    repo_id: seed.repo,
    type: seed.type,
    family: seed.family,
    priority: seed.priority,
    tool_parser_default: seed.toolParser ?? null,
    default_context_size: seed.context ?? null,
    default_launch_flags: seed.flags ?? {},
    trust_remote_code: false,
    allow_pickle: seed.allowPickle ?? false,
    tags: seed.tags ?? [],
    aliases: seed.aliases ?? [],
    metadata: {},
    created_at: createdAt,
    updated_at: createdAt,
  });
  for (const v of seed.versions) addVersion(seed.id, v, v.createdDaysAgo ? daysAgo(v.createdDaysAgo) : createdAt);
}

function defaultVersion(modelId: string): ModelVersion | undefined {
  return versions.find((v) => v.model_id === modelId && v.is_default) ?? versions.find((v) => v.model_id === modelId);
}

function modelView(model: Model): Model {
  return {
    ...model,
    default_version: defaultVersion(model.id) ?? null,
    version_count: versions.filter((v) => v.model_id === model.id).length,
    deployment_count: fleetStore.deployments.filter((d) => d.model_id === model.id && d.status !== 'deleting').length,
  };
}

function fileView(file: ModelFile): ModelFile {
  const nodes = new Set(fleetStore.cacheItems.filter((c) => c.model_file_id === file.id && c.state === 'ready').map((c) => c.node_id));
  return { ...file, cached_on_nodes: nodes.size };
}

// ─── Compatibility and VRAM estimate ────────────────────────────────────────

/** Weights + KV cache for the context + 8 % runtime overhead. */
function estimateVram(version: ModelVersion, contextSize: number | null | undefined, gpuCount = 1): number {
  const weights = version.total_size_bytes;
  const params = version.parameter_count ?? weights / 2;
  const kvPerToken = Math.max(8_192, Math.min(262_144, (params / 1e9) * 16_384));
  const kv = kvPerToken * (contextSize ?? 8192);
  return Math.round(((weights + kv) * 1.08) / Math.max(1, gpuCount));
}

const FLEET_VENDORS = (): Vendor[] => [...new Set(fleetStore.devices.map((d) => d.vendor))];

function compatCheck(version: ModelVersion, ev: EngineVersion, opts: { vendor?: Vendor | null; nodeId?: string | null; contextSize?: number | null; gpuCount?: number } = {}): CompatCheck {
  const model = models.find((m) => m.id === version.model_id);
  const engine = engines.find((e) => e.id === ev.engine_id);
  const reasons: CompatCheck['reasons'] = [];
  let result: CompatCheck['result'] = 'compatible';
  let score = 50;
  const formats = engine?.supported_formats ?? [];
  const types = engine?.supported_model_types ?? [];
  const vendors = engine?.supported_vendors ?? [];
  const fleetVendors = opts.vendor ? [opts.vendor] : FLEET_VENDORS();
  const usableVendors = fleetVendors.filter((v) => vendors.includes(v));

  if (!formats.includes(version.format)) {
    result = 'incompatible';
    reasons.push({ code: 'FORMAT_UNSUPPORTED', severity: 'error', message: `${engine?.display_name} does not load ${version.format.toUpperCase()} weights (supports ${formats.map((f) => f.toUpperCase()).join(', ')})` });
  } else {
    reasons.push({ code: 'FORMAT_OK', severity: 'info', message: `${version.format.toUpperCase()} is a native format for ${engine?.display_name}` });
  }
  if (model && !types.includes(model.type)) {
    result = 'incompatible';
    reasons.push({ code: 'MODEL_TYPE_UNSUPPORTED', severity: 'error', message: `${engine?.display_name} does not serve ${model.type} models` });
  }
  if (usableVendors.length === 0) {
    result = 'incompatible';
    reasons.push({ code: 'NO_SUPPORTED_DEVICE', severity: 'error', message: `No ${vendors.join(' / ')} accelerator in the fleet (have ${fleetVendors.join(', ') || 'none'})` });
  } else {
    reasons.push({ code: 'DEVICE_OK', severity: 'info', message: `Runs on ${usableVendors.map((v) => (v === 'nvidia' ? 'NVIDIA' : v === 'apple' ? 'Apple Silicon' : v)).join(' and ')} in this fleet` });
  }
  if (version.format === 'pickle' && !model?.allow_pickle) {
    result = 'incompatible';
    reasons.push({ code: 'PICKLE_BLOCKED', severity: 'error', message: 'Pickle weights are blocked for this model (enable "Allow pickle" to override)' });
  }
  if (ev.status === 'deprecated') {
    reasons.push({ code: 'ENGINE_DEPRECATED', severity: 'warning', message: `${engine?.display_name} ${ev.version} is deprecated — prefer the default version` });
    score -= 15;
  }
  if (engine && !engine.is_enabled) {
    reasons.push({ code: 'ENGINE_DISABLED', severity: 'warning', message: `${engine.display_name} is disabled in the engine registry` });
    score -= 30;
  }
  const est = estimateVram(version, opts.contextSize ?? model?.default_context_size ?? 8192, opts.gpuCount ?? 1);
  const maxFree = Math.max(
    0,
    ...fleetStore.devices
      .filter((d) => usableVendors.includes(d.vendor))
      .map((d) => {
        const v = deviceView(d);
        return v.vram_total_bytes * (v.is_unified_memory ? 0.75 : 0.9) - (v.vram_reserved_bytes ?? 0);
      }),
  );
  if (result === 'compatible') {
    if (est > maxFree) {
      reasons.push({ code: 'VRAM_TIGHT', severity: 'warning', message: `Needs ~${(est / GiB).toFixed(1)} GiB per GPU; the largest free block is ${(maxFree / GiB).toFixed(1)} GiB — placement may evict a lower-priority model` });
      score -= 10;
    } else {
      reasons.push({ code: 'VRAM_OK', severity: 'info', message: `Needs ~${(est / GiB).toFixed(1)} GiB per GPU (weights + KV for the context)` });
    }
    const pref: Partial<Record<EngineName, Partial<Record<FileFormat, number>>>> = {
      llama_cpp: { gguf: 40 },
      mlx: { mlx: 45, safetensors: 20 },
      vllm: { safetensors: 42 },
      sglang: { safetensors: 36 },
      tensorrt_llm: { safetensors: 30 },
      whisper_cpp: { other: 40, gguf: 40 },
      stable_diffusion_cpp: { gguf: 30, safetensors: 30 },
      vllm_omni: { safetensors: 35 },
      mlx_audio: { mlx: 35 },
      sglang_diffusion: { safetensors: 30 },
    };
    score += pref[engine?.name ?? 'custom']?.[version.format] ?? 5;
    if (ev.is_default) score += 5;
  } else {
    score = Math.max(0, score - 50);
  }
  return {
    id: newId('compat'),
    model_version_id: version.id,
    engine_version_id: ev.id,
    engine_name: engine?.name,
    engine_version: ev.version,
    node_id: opts.nodeId ?? null,
    device_vendor: opts.vendor ?? null,
    result,
    score: Math.max(0, Math.min(100, score)),
    reasons,
    estimated_vram_bytes: est,
    checked_at: new Date().toISOString(),
  };
}

const compatHistory: CompatCheck[] = [];

function compatAll(version: ModelVersion, opts: Parameters<typeof compatCheck>[2] = {}): CompatCheck[] {
  const enabledEngines = new Set(engines.filter((e) => e.is_enabled).map((e) => e.id));
  return engineVersions
    .filter((ev) => enabledEngines.has(ev.engine_id) && ev.status !== 'disabled')
    .map((ev) => compatCheck(version, ev, opts))
    .sort((a, b) => b.score - a.score);
}

const SUGGESTED_FLAGS: Partial<Record<EngineName, Record<Schemas['deployment_preset'], Record<string, unknown>>>> = {
  llama_cpp: { low_latency: { '--parallel': 4, '--flash-attn': true, '--cont-batching': true }, high_throughput: { '--parallel': 16, '--ubatch-size': 2048, '--cont-batching': true } },
  vllm: { low_latency: { '--max-num-seqs': 32, '--enable-chunked-prefill': true }, high_throughput: { '--max-num-seqs': 256, '--max-num-batched-tokens': 16384 } },
  sglang: { low_latency: { '--max-running-requests': 32 }, high_throughput: { '--max-running-requests': 256, '--chunked-prefill-size': 8192 } },
  mlx: { low_latency: { '--max-tokens': 2048 }, high_throughput: { '--max-tokens': 4096 } },
};

const PARSER_BY_FAMILY: Record<string, Partial<Record<EngineName, string>>> = {
  qwen3: { llama_cpp: 'jinja', vllm: 'hermes', sglang: 'qwen25' },
  'qwen2.5-vl': { llama_cpp: 'jinja', vllm: 'hermes', sglang: 'qwen25' },
  llama3: { llama_cpp: 'jinja', vllm: 'llama3_json', sglang: 'llama3' },
  gemma3: { llama_cpp: 'jinja', mlx: 'auto' },
};

function autoPick(version: ModelVersion, opts: { contextSize?: number | null; gpuCount?: number; preset?: Schemas['deployment_preset'] | null; nodeId?: string | null }): Schemas['AutoPickResult'] {
  const checks = compatAll(version, { contextSize: opts.contextSize, gpuCount: opts.gpuCount, nodeId: opts.nodeId });
  const best = checks.find((c) => c.result === 'compatible') ?? null;
  const ev = best ? engineVersions.find((v) => v.id === best.engine_version_id) ?? null : null;
  const model = models.find((m) => m.id === version.model_id);
  const engineName = ev?.engine_name;
  return {
    check: best,
    engine_version: ev,
    alternatives: checks.filter((c) => c !== best),
    suggested_launch_flags: engineName ? { ...(ev?.default_launch_flags ?? {}), ...(SUGGESTED_FLAGS[engineName]?.[opts.preset ?? 'low_latency'] ?? {}) } : {},
    suggested_tool_parser: engineName ? (PARSER_BY_FAMILY[model?.family ?? '']?.[engineName] ?? model?.tool_parser_default ?? null) : null,
  };
}

// ─── Placement ──────────────────────────────────────────────────────────────

function resolveSpec(spec: Partial<DeploymentSpec>) {
  const model = models.find((m) => m.id === spec.model_id);
  const version = model ? (spec.model_version_id ? versions.find((v) => v.id === spec.model_version_id) : defaultVersion(model.id)) : undefined;
  let ev = spec.engine_version_id ? engineVersions.find((v) => v.id === spec.engine_version_id) : undefined;
  if (!ev && version) ev = autoPick(version, { contextSize: spec.context_size, gpuCount: spec.gpu_count, preset: spec.preset }).engine_version ?? undefined;
  const engine = ev ? engines.find((e) => e.id === ev?.engine_id) : undefined;
  return { model, version, ev, engine };
}

function previewPlacement(spec: Partial<DeploymentSpec>, replicasOverride?: number | null, excludeDeploymentId?: string): Schemas['PlacementPreview'] {
  const { model, version, ev, engine } = resolveSpec(spec);
  if (!model || !version || !ev || !engine) {
    return { feasible: false, placements: [], rejected: [{ reason: !model ? 'Unknown model' : 'No compatible engine version for this model' }], compat: null, would_evict: [] };
  }
  const replicas = Math.max(0, replicasOverride ?? spec.replicas ?? 1);
  const gpuCount = Math.max(1, (spec.tp_size ?? 1) * (spec.pp_size ?? 1) > 1 ? (spec.tp_size ?? 1) * (spec.pp_size ?? 1) : spec.gpu_count ?? 1);
  const perGpu = spec.slice_vram_bytes ?? estimateVram(version, spec.context_size ?? model.default_context_size, gpuCount);
  const selector = (spec.node_selector ?? {}) as { node_ids?: string[]; device_ids?: string[]; [k: string]: unknown };
  const vendorFilter = spec.device_vendor_filter ?? [];
  const rejected: NonNullable<Schemas['PlacementPreview']['rejected']> = [];
  const candidates: PlacementCandidate[] = [];
  const now = Date.now();

  for (const node of fleetStore.nodes) {
    if (node.status === 'removed') continue;
    if (spec.cluster_id && node.cluster_id !== spec.cluster_id) continue;
    if (selector.node_ids?.length && !selector.node_ids.includes(node.id)) continue;
    const labelPairs = Object.entries(selector).filter(([k]) => k !== 'node_ids' && k !== 'device_ids');
    if (labelPairs.some(([k, v]) => node.labels[k] !== v)) {
      rejected.push({ node_id: node.id, reason: `Labels do not match ${labelPairs.map(([k, v]) => `${k}=${String(v)}`).join(', ')}` });
      continue;
    }
    if (node.status !== 'online') {
      rejected.push({ node_id: node.id, reason: `Node is ${node.status}` });
      continue;
    }
    if (node.os && !(engine.supported_os ?? []).includes(node.os)) {
      rejected.push({ node_id: node.id, reason: `${engine.display_name} does not run on ${node.os === 'macos' ? 'macOS' : 'Linux'}` });
      continue;
    }
    const devices = fleetStore.devices
      .filter((d) => d.node_id === node.id)
      .filter((d) => !selector.device_ids?.length || selector.device_ids.includes(d.id))
      .map((d) => deviceView(d, now));
    const usable = devices.filter((d) => (engine.supported_vendors ?? []).includes(d.vendor) && (!vendorFilter.length || vendorFilter.includes(d.vendor)));
    if (usable.length === 0) {
      rejected.push({ node_id: node.id, device_ids: devices.map((d) => d.id), reason: `No ${vendorFilter.length ? vendorFilter.join('/') : (engine.supported_vendors ?? []).join('/')} device for ${engine.display_name}` });
      continue;
    }
    const withFree = usable
      .map((d) => {
        const reservedByOthers = (d.slices ?? []).filter((s) => s.deployment_id !== excludeDeploymentId).reduce((sum, s) => sum + s.vram_bytes, 0);
        const usableTotal = d.vram_total_bytes * (d.is_unified_memory ? 0.75 : 0.9);
        return { d, free: usableTotal - reservedByOthers, usableTotal };
      })
      .sort((a, b) => b.free - a.free);
    const fits = withFree.filter((x) => x.free >= perGpu);
    if (fits.length < gpuCount) {
      rejected.push({ node_id: node.id, device_ids: withFree.map((x) => x.d.id), reason: `Needs ${gpuCount} GPU(s) with ${(perGpu / GiB).toFixed(1)} GiB free; best has ${(Math.max(0, withFree[0]?.free ?? 0) / GiB).toFixed(1)} GiB` });
      continue;
    }
    // One candidate per group of gpuCount devices on this node.
    for (let i = 0; i + gpuCount <= fits.length; i += gpuCount) {
      const group = fits.slice(i, i + gpuCount);
      const freeAfter = Math.min(...group.map((x) => x.free - perGpu));
      const ratio = group.reduce((s, x) => s + (x.free - perGpu) / x.usableTotal, 0) / group.length;
      const score = Math.round(spec.placement_strategy === 'pack' ? 100 - ratio * 60 : 40 + ratio * 60);
      candidates.push({
        node_id: node.id,
        node_name: node.name,
        device_ids: group.map((x) => x.d.id),
        device_names: group.map((x) => `${x.d.name} #${x.d.index}`),
        engine_version_id: ev.id,
        estimated_vram_bytes: perGpu * gpuCount,
        free_vram_bytes_after: Math.max(0, Math.round(freeAfter)),
        score,
        slice: spec.slice_vram_bytes || spec.slice_compute_pct ? { vram_bytes: spec.slice_vram_bytes ?? perGpu, compute_pct: spec.slice_compute_pct ?? 100 } : { vram_bytes: perGpu, compute_pct: 100 },
        reasons: [
          `${(Math.max(0, freeAfter) / GiB).toFixed(1)} GiB free after placement`,
          spec.placement_strategy === 'pack' ? 'Pack: prefers fuller devices' : 'Spread: prefers emptier devices',
          ...(group[0].d.is_unified_memory ? ['Unified memory (75 % usable for models)'] : []),
        ],
      });
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  // Spread prefers distinct nodes first, pack fills the best node first.
  const placements: PlacementCandidate[] = [];
  const pool = [...candidates];
  while (placements.length < replicas && pool.length > 0) {
    const used = new Set(placements.map((p) => p.node_id));
    const idx = spec.placement_strategy === 'pack' ? 0 : Math.max(0, pool.findIndex((c) => !used.has(c.node_id)));
    placements.push(pool.splice(idx, 1)[0]);
  }
  const feasible = placements.length >= replicas;
  const wouldEvict = feasible
    ? []
    : fleetStore.instances
        .filter((i) => i.state === 'ready')
        .filter((i) => {
          const dep = fleetStore.deployments.find((d) => d.id === i.deployment_id);
          return dep?.effective_priority === 'evictable' && dep.id !== excludeDeploymentId;
        })
        .map((i) => instanceView(i, now));
  return { feasible, placements, rejected, compat: compatCheck(version, ev, { contextSize: spec.context_size, gpuCount }), would_evict: wouldEvict };
}

// ─── Deployments ────────────────────────────────────────────────────────────

const sessionSwaps: SwapOperation[] = [];

function recordSwap(deployment: Deployment, kind: SwapOperation['kind'], instance: Instance | null, ms = 4000): SwapOperation {
  const now = Date.now();
  const names = kind === 'load' || kind === 'scale_up' ? ['plan', 'load', 'health', 'route_on'] : kind === 'unload' || kind === 'scale_down' ? ['route_off', 'drain', 'unload'] : ['route_off', 'drain', 'unload', 'load', 'health', 'route_on'];
  const step = ms / names.length;
  const swap: SwapOperation = {
    id: newId('swap'),
    kind,
    state: 'completed',
    trigger: 'manual',
    deployment_id: deployment.id,
    deployment_name: deployment.name,
    from_instance_id: instance?.id ?? null,
    to_instance_id: instance?.id ?? null,
    node_id: instance?.node_id ?? null,
    layout_id: null,
    rule_run_id: null,
    rule_name: null,
    plan: { action: kind },
    rollback: {},
    drain_timeout_seconds: 120,
    dry_run: false,
    current_step: names.length,
    steps: names.map((name, i) => ({ name, state: 'completed', started_at: iso(now + i * step), finished_at: iso(now + (i + 1) * step), error: null, command_id: null, details: null })),
    started_at: iso(now),
    finished_at: iso(now + ms),
    error_message: null,
    created_at: iso(now),
    updated_at: iso(now + ms),
  };
  sessionSwaps.unshift(swap);
  return swap;
}

function aliasFor(deployment: Deployment): string {
  const fixtureIds: string[] = Object.values(IDS.instances);
  const fixture = fleetStore.instances.find((i) => i.deployment_id === deployment.id && fixtureIds.includes(i.id));
  if (fixture) return fixture.id;
  const model = models.find((m) => m.id === deployment.model_id);
  if (model?.type === 'embedding') return IDS.instances.nomicL40s;
  return IDS.instances.qwenL40s;
}

function nextPort(nodeId: string): number {
  const used = new Set(fleetStore.instances.filter((i) => i.node_id === nodeId && i.state !== 'stopped').map((i) => i.port));
  let port = 10240;
  while (used.has(port)) port += 1;
  return port;
}

function launchInstance(deployment: Deployment, placement: PlacementCandidate, loadMs = 4000): Instance {
  const now = Date.now();
  const node = fleetStore.nodes.find((n) => n.id === placement.node_id);
  const id = newId('instance');
  const instance: Instance = {
    id,
    deployment_id: deployment.id,
    deployment_name: deployment.name,
    model_name: deployment.model_name,
    node_id: placement.node_id,
    node_name: node?.name,
    device_ids: placement.device_ids,
    port: nextPort(placement.node_id),
    served_model_name: deployment.name,
    engine_process_id: newId('engine-process'),
    engine_name: deployment.engine_name ?? undefined,
    state: 'loading',
    state_message: 'Loading weights from the node cache',
    health: 'unknown',
    health_message: null,
    traffic_weight: 100,
    pd_role: null,
    pid: 20000 + (hash32(id) % 40000),
    container_id: null,
    resources: { vram_bytes: placement.estimated_vram_bytes, slots: Number(deployment.launch_flags?.['--parallel'] ?? 4), context_size: deployment.context_size ?? 8192 },
    restart_count: 0,
    state_changed_at: iso(now),
    started_at: iso(now),
    ready_at: null,
    stopped_at: null,
    last_health_at: null,
    last_request_at: null,
    live: {},
    created_at: iso(now),
    updated_at: iso(now),
  };
  fleetStore.instances.push(instance);
  fleetStore.metricAlias.set(id, aliasFor(deployment));
  const perDevice = Math.round((placement.slice?.vram_bytes ?? placement.estimated_vram_bytes ?? 0) / Math.max(1, placement.device_ids.length === 1 ? 1 : 1));
  for (const deviceId of placement.device_ids) {
    fleetStore.slices.push({
      id: newId('slice'),
      device_id: deviceId,
      instance_id: id,
      deployment_id: deployment.id,
      name: deployment.name,
      vram_bytes: perDevice,
      compute_pct: placement.slice?.compute_pct ?? 100,
      status: 'reserved',
      created_at: iso(now),
      released_at: null,
    });
  }
  recordCommand(placement.node_id, 'load_model', { instanceId: id, ms: loadMs });
  setTimeout(() => {
    if (instance.state !== 'loading') return;
    instance.state = 'ready';
    instance.state_message = null;
    instance.health = 'healthy';
    instance.ready_at = new Date().toISOString();
    instance.state_changed_at = instance.ready_at;
    for (const s of fleetStore.slices) if (s.instance_id === id && s.status === 'reserved') s.status = 'active';
    settleDeployment(deployment);
  }, loadMs);
  return instance;
}

function liveInstances(deploymentId: string): Instance[] {
  return fleetStore.instances.filter((i) => i.deployment_id === deploymentId && i.state !== 'stopped');
}

function settleDeployment(deployment: Deployment): void {
  const live = liveInstances(deployment.id);
  const ready = live.filter((i) => i.state === 'ready').length;
  if (!deployment.desired_running) {
    deployment.status = live.length ? deployment.status : 'stopped';
    return;
  }
  if (live.some((i) => i.state === 'loading' || i.state === 'draining' || i.state === 'unloading')) return;
  deployment.status = ready >= deployment.replicas ? 'running' : ready > 0 ? 'degraded' : 'failed';
  deployment.status_message = ready >= deployment.replicas ? null : `${ready} of ${deployment.replicas} replicas ready — not enough free VRAM for the rest`;
  deployment.updated_at = new Date().toISOString();
}

function placeReplicas(deployment: Deployment, count: number): Instance[] {
  if (count <= 0) return [];
  const preview = previewPlacement(deployment, count, undefined);
  const launched = preview.placements.slice(0, count).map((p, i) => launchInstance(deployment, p, 3500 + i * 1200));
  launched.forEach((inst) => recordSwap(deployment, 'scale_up', inst));
  if (launched.length < count) {
    setTimeout(() => settleDeployment(deployment), 5000 + launched.length * 1200);
  }
  return launched;
}

function scaleTo(deployment: Deployment, replicas: number): string[] {
  const live = liveInstances(deployment.id);
  deployment.replicas = replicas;
  const commandIds: string[] = [];
  if (replicas > live.length) {
    deployment.status = 'scaling';
    placeReplicas(deployment, replicas - live.length).forEach((i) => commandIds.push(i.id));
  } else if (replicas < live.length) {
    deployment.status = 'scaling';
    const victims = [...live].sort((a, b) => a.traffic_weight - b.traffic_weight).slice(0, live.length - replicas);
    for (const v of victims) {
      commandIds.push(unloadInstance(v, 3000).id);
      recordSwap(deployment, 'scale_down', v);
    }
    setTimeout(() => settleDeployment(deployment), 3300);
  }
  return commandIds;
}

function restartInstances(deployment: Deployment, list: Instance[]): string[] {
  const ids: string[] = [];
  list.forEach((inst, i) => {
    const delay = i * 4500;
    setTimeout(() => {
      inst.state = 'draining';
      inst.state_changed_at = new Date().toISOString();
      setTimeout(() => {
        inst.state = 'loading';
        inst.restart_count = (inst.restart_count ?? 0) + 1;
        inst.state_changed_at = new Date().toISOString();
      }, 1500);
      setTimeout(() => {
        inst.state = 'ready';
        inst.health = 'healthy';
        inst.ready_at = new Date().toISOString();
        inst.state_changed_at = inst.ready_at;
        if (i === list.length - 1) settleDeployment(deployment);
      }, 4000);
    }, delay);
    ids.push(recordCommand(inst.node_id, 'restart_instance', { instanceId: inst.id, ms: delay + 4000 }).id);
    recordSwap(deployment, 'restart', inst);
  });
  if (list.length) deployment.status = 'deploying';
  return ids;
}

const ROLLING_FIELDS: (keyof DeploymentSpec)[] = ['model_version_id', 'engine_version_id', 'gpu_count', 'slice_vram_bytes', 'slice_compute_pct', 'tp_size', 'pp_size', 'launch_flags', 'context_size', 'control_vectors', 'env_vars'];

function buildDeployment(body: Partial<Schemas['DeploymentCreate']>): { deployment?: Deployment; error?: ReturnType<typeof fail> } {
  if (!body.name || !body.model_id) return { error: fail(422, 'VALIDATION_ERROR', 'name and model_id are required') };
  if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(body.name)) return { error: fail(422, 'VALIDATION_ERROR', 'Serving name: lowercase letters, digits, dots, dashes and underscores') };
  const { model, version, ev, engine } = resolveSpec(body);
  if (!model || !version) return { error: fail(404, 'NOT_FOUND', 'Model or version not found') };
  if (!ev || !engine) return { error: fail(422, 'NO_COMPATIBLE_ENGINE', 'No enabled engine version can serve this model version') };
  const now = new Date().toISOString();
  const deployment: Deployment = {
    id: newId('deployment'),
    name: body.name,
    description: body.description ?? null,
    model_id: model.id,
    model_version_id: body.model_version_id ?? null,
    engine_version_id: body.engine_version_id ?? null,
    cluster_id: body.cluster_id ?? null,
    replicas: body.replicas ?? 1,
    min_replicas: body.min_replicas ?? 0,
    max_replicas: Math.max(body.max_replicas ?? 1, body.replicas ?? 1),
    gpu_count: body.gpu_count ?? 1,
    slice_vram_bytes: body.slice_vram_bytes ?? null,
    slice_compute_pct: body.slice_compute_pct ?? null,
    tp_size: body.tp_size ?? 1,
    pp_size: body.pp_size ?? 1,
    preset: body.preset ?? null,
    pd_split: body.pd_split ?? false,
    pd_config: body.pd_config ?? {},
    spec_decoding: body.spec_decoding ?? {},
    kv_sharing: body.kv_sharing ?? false,
    kv_sharing_config: body.kv_sharing_config ?? {},
    launch_flags: body.launch_flags ?? {},
    env_vars: body.env_vars ?? {},
    tool_parser: body.tool_parser ?? null,
    context_size: body.context_size ?? model.default_context_size ?? null,
    control_vectors: body.control_vectors ?? [],
    placement_strategy: body.placement_strategy ?? 'spread',
    node_selector: body.node_selector ?? {},
    device_vendor_filter: body.device_vendor_filter ?? [],
    traffic_config: { strategy: 'least_sessions', failover: true, max_retries: 1, health_eject: true, ...(body.traffic_config ?? {}) },
    priority_override: body.priority_override ?? null,
    effective_priority: body.priority_override ?? model.priority,
    model_name: model.display_name ?? model.name,
    model_type: model.type,
    engine_name: engine.name,
    engine_version: ev.version,
    status: body.start === false ? 'stopped' : 'scheduling',
    status_message: null,
    desired_running: body.start !== false,
    last_request_at: null,
    ready_replicas: 0,
    active_swap_ids: [],
    live: {},
    created_at: now,
    updated_at: now,
  };
  return { deployment };
}

function deploymentRecord(id: string) {
  return fleetStore.deployments.find((d) => d.id === id);
}

function filterDeployments(req: MockRequest): Deployment[] {
  const q = req.query;
  const search = (q.get('search') ?? '').toLowerCase();
  const status = q.get('status');
  const engine = q.get('engine');
  const priority = q.get('priority');
  const clusterId = q.get('cluster_id');
  const modelId = q.get('model_id');
  const origin = q.get('origin');
  return fleetStore.deployments.filter(
    (d) =>
      (!origin || (d.origin ?? 'managed') === origin) &&
      (!status || d.status === status) &&
      (!engine || d.engine_name === engine) &&
      (!priority || d.effective_priority === priority) &&
      (!clusterId || d.cluster_id === clusterId) &&
      (!modelId || d.model_id === modelId) &&
      (!search || [d.name, d.model_name ?? '', d.description ?? ''].some((s) => s.toLowerCase().includes(search))),
  );
}

// ─── Model cache ────────────────────────────────────────────────────────────

function seedCache(): CacheItem[] {
  const out: CacheItem[] = [];
  const add = (nodeId: string, modelId: string, versionName: string, opts: { localPath?: (path: string) => string; inUse?: boolean; pinned?: string | null; lastUsedHours?: number | null; state?: CacheItem['state']; progress?: number; createdDays?: number } = {}) => {
    const model = models.find((m) => m.id === modelId);
    const version = versions.find((v) => v.model_id === modelId && v.version === versionName);
    const node = fleetStore.nodes.find((n) => n.id === nodeId);
    if (!model || !version || !node) return;
    for (const file of files.filter((f) => f.model_version_id === version.id)) {
      const lastUsed = opts.lastUsedHours === null ? null : hoursAgo(opts.lastUsedHours ?? 0.01);
      out.push({
        id: uuid(`cache:${nodeId}:${file.id}`),
        node_id: nodeId,
        node_name: node.name,
        model_id: modelId,
        model_name: model.display_name ?? model.name,
        model_version_id: version.id,
        version: version.version,
        model_file_id: file.id,
        path: file.path,
        local_path: opts.localPath ? opts.localPath(file.path) : `${node.cache_dir}/${model.repo_id.replace(/^\/.*\//, '').replace('/', '--')}/${version.version}/${file.path}`,
        size_bytes: file.size_bytes,
        state: opts.state ?? 'ready',
        progress_pct: opts.state === 'downloading' ? opts.progress ?? 40 : 100,
        sha256_verified: (opts.state ?? 'ready') === 'ready',
        pinned: !!opts.pinned,
        pinned_reason: opts.pinned ?? null,
        in_use: !!opts.inUse,
        error_message: null,
        last_used_at: lastUsed,
        created_at: daysAgo(opts.createdDays ?? 3),
        updated_at: lastUsed ?? daysAgo(opts.createdDays ?? 3),
      });
    }
  };
  const mac = IDS.nodes.mac;
  const gpu = IDS.nodes.gpu;
  add(mac, MODEL_IDS.qwen, 'Q8_0', { inUse: true, pinned: 'Always-on model', createdDays: 6, localPath: () => '/Users/alice/.ollama/models/blobs/sha256-7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa' });
  add(mac, MODEL_IDS.gemma, 'qat-4bit', { inUse: true, createdDays: 3, localPath: (p) => `/Users/alice/.lmstudio/models/mlx-community/gemma-3-1b-it-qat-4bit/${p}` });
  add(mac, MODEL_IDS.tinyllama, 'latest', { lastUsedHours: 26 * 5, createdDays: 9, localPath: (p) => `/Users/alice/.ollama/models/${p}` });
  add(mac, MODEL_IDS.nomic, 'Q8_0', { lastUsedHours: 30, createdDays: 5, localPath: (p) => `/Users/alice/.lmstudio/models/nomic-ai/nomic-embed-text-v1.5-GGUF/${p}` });
  add(gpu, MODEL_IDS.qwen, 'Q8_0', { inUse: true, pinned: 'Always-on model', createdDays: 6 });
  add(gpu, MODEL_IDS.nomic, 'Q8_0', { inUse: true, createdDays: 5 });
  add(gpu, MODEL_IDS.qwen8b, 'main', { lastUsedHours: 9, pinned: 'Night layout (prefetch)', createdDays: 4 });
  add(gpu, MODEL_IDS.llama70, 'main', { lastUsedHours: null, pinned: 'Night layout (prefetch)', createdDays: 2 });
  add(gpu, MODEL_IDS.whisper, 'large-v3-turbo-q5_0', { lastUsedHours: 24 * 6, createdDays: 8 });
  add(gpu, MODEL_IDS.qwenvl, 'master', { state: 'downloading', progress: 62, lastUsedHours: null, createdDays: 0.05 });
  return out;
}

fleetStore.cacheItems.push(...seedCache());

/** Downloading items advance a little on every read so the progress bars move. */
function tickDownloads(): void {
  const now = Date.now();
  for (const item of fleetStore.cacheItems) {
    if (item.state !== 'downloading') continue;
    const last = Date.parse(item.updated_at ?? iso(now));
    const gained = ((now - last) / 1000) * (12 + (hash32(item.id) % 10));
    item.progress_pct = Math.min(100, round((item.progress_pct ?? 0) + gained, 1));
    item.updated_at = iso(now);
    if (item.progress_pct >= 100) {
      item.state = 'ready';
      item.sha256_verified = true;
    }
  }
}

function cacheSummary(nodeId: string): Schemas['CacheSummary'] {
  const node = fleetStore.nodes.find((n) => n.id === nodeId);
  const items = fleetStore.cacheItems.filter((c) => c.node_id === nodeId && c.state !== 'evicted');
  const byVersion = new Map<string, CacheItem[]>();
  for (const item of items) byVersion.set(item.model_version_id, [...(byVersion.get(item.model_version_id) ?? []), item]);
  const disk = node?.disks?.find((d) => d.is_cache_dir) ?? node?.disks?.find((d) => (node.cache_dir ?? '').startsWith(d.mount) && d.mount !== '/') ?? node?.disks?.[0];
  return {
    node_id: nodeId,
    node_name: node?.name,
    cache_dir: node?.cache_dir ?? null,
    limit_bytes: node?.cache_limit_bytes ?? null,
    used_bytes: items.reduce((s, i) => s + i.size_bytes, 0),
    free_disk_bytes: disk?.free_bytes ?? null,
    pinned_bytes: items.filter((i) => i.pinned).reduce((s, i) => s + i.size_bytes, 0),
    item_count: items.length,
    by_model: [...byVersion.values()].map((group) => ({
      model_id: group[0].model_id,
      model_name: group[0].model_name,
      model_version_id: group[0].model_version_id,
      version: group[0].version,
      size_bytes: group.reduce((s, i) => s + i.size_bytes, 0),
      pinned: group.some((i) => i.pinned),
      in_use: group.some((i) => i.in_use),
      state: group.some((i) => i.state === 'downloading') ? 'downloading' : group.some((i) => i.state === 'evicting') ? 'evicting' : 'ready',
      last_used_at: group.map((i) => i.last_used_at).filter(Boolean).sort().pop() ?? null,
    })),
  };
}

function evictItems(items: CacheItem[], force: boolean): { evicted: CacheItem[]; skipped: CacheItem[] } {
  const evicted: CacheItem[] = [];
  const skipped: CacheItem[] = [];
  for (const item of items) {
    if ((item.in_use || item.pinned) && !force) {
      skipped.push(item);
      continue;
    }
    item.state = 'evicting';
    item.updated_at = new Date().toISOString();
    evicted.push(item);
    setTimeout(() => {
      item.state = 'evicted';
      item.pinned = false;
      item.updated_at = new Date().toISOString();
    }, 2500);
  }
  return { evicted, skipped };
}

// ─── Benchmarks ─────────────────────────────────────────────────────────────

interface CurveProfile {
  ttft: number;
  tpot: number;
  knee: number;
}

const CURVES: Record<string, CurveProfile> = {
  [IDS.deployments.qwen]: { ttft: 95, tpot: 11, knee: 10 },
  [IDS.deployments.gemma]: { ttft: 140, tpot: 17, knee: 4 },
  [IDS.deployments.nomic]: { ttft: 18, tpot: 0, knee: 24 },
};

function curveFor(deploymentId: string): CurveProfile {
  return CURVES[deploymentId] ?? { ttft: 160, tpot: 21, knee: 6 + (hash32(deploymentId) % 8) };
}

function resultAt(benchmark: Benchmark, c: number, variant = 0): BenchmarkResult {
  const p = curveFor(benchmark.deployment_id);
  const workload = (benchmark.workload ?? {}) as { output_tokens?: number; input_tokens?: number; requests_per_level?: number };
  const out = p.tpot === 0 ? 1 : (workload.output_tokens ?? 256);
  const input = workload.input_tokens ?? 512;
  const rnd = mulberry32(hash32(`${benchmark.id}:${c}:${variant}`));
  const pressure = Math.pow(c / p.knee, 2);
  const tuned = variant > 0 ? 0.88 : 1;
  const ttft50 = p.ttft * (1 + pressure * 0.55) * (0.95 + rnd() * 0.1) * tuned;
  const ttft95 = ttft50 * (1.45 + pressure * 0.25);
  const tpot50 = p.tpot * (1 + (c / p.knee) * 0.32) * tuned;
  const e2e50 = ttft50 + out * tpot50;
  const e2e95 = ttft95 + out * tpot50 * 1.18;
  const rps = (c / Math.max(1, e2e50)) * 1000 * (c > p.knee * 2.2 ? 0.92 : 1);
  const requests = workload.requests_per_level ?? Math.max(40, c * 12);
  const errors = c > p.knee * 3 ? Math.round(requests * 0.02 * rnd()) : 0;
  const slo = (benchmark.target_slo ?? {}) as { ttft_ms_p95?: number; e2e_ms_p95?: number; min_generation_tps?: number; max_error_rate_pct?: number };
  const genTps = p.tpot === 0 ? 0 : rps * out;
  const sloMet = (!slo.ttft_ms_p95 || ttft95 <= slo.ttft_ms_p95) && (!slo.e2e_ms_p95 || e2e95 <= slo.e2e_ms_p95) && (!slo.max_error_rate_pct || (errors / requests) * 100 <= slo.max_error_rate_pct);
  return {
    concurrency: c,
    requests,
    errors,
    rps: round(rps, 2),
    generation_tps: round(genTps, 1),
    prompt_tps: round(rps * input, 0),
    ttft_ms: { p50: round(ttft50, 0), p90: round(ttft50 * 1.3, 0), p95: round(ttft95, 0), p99: round(ttft95 * 1.25, 0) },
    tpot_ms: { p50: round(tpot50, 1), p95: round(tpot50 * 1.2, 1) },
    e2e_ms: { p50: round(e2e50, 0), p90: round(e2e50 * 1.15, 0), p95: round(e2e95, 0), p99: round(e2e95 * 1.2, 0) },
    slo_met: sloMet,
    launch_flags: variant > 0 ? { '--parallel': Math.max(4, c), '--ubatch-size': 1024 } : null,
  };
}

function recommendationFor(benchmark: Benchmark, results: BenchmarkResult[]): { best: number | null; rec: Record<string, unknown>; met: boolean } {
  const passing = results.filter((r) => r.slo_met);
  const best = passing.length ? passing.reduce((a, b) => ((b.rps ?? 0) > (a.rps ?? 0) ? b : a)) : null;
  if (!best) {
    return { best: null, met: false, rec: { reason: 'No concurrency level met the SLO — add a replica or relax the target.', replicas: 2 } };
  }
  const dep = deploymentRecord(benchmark.deployment_id);
  const peakRps = Math.max(...results.map((r) => r.rps ?? 0));
  return {
    best: best.concurrency ?? null,
    met: true,
    rec: {
      concurrency: best.concurrency,
      launch_flags: best.launch_flags ?? (dep?.engine_name === 'llama_cpp' ? { '--parallel': best.concurrency, '--cont-batching': true } : { '--max-num-seqs': best.concurrency }),
      replicas: dep?.replicas ?? 1,
      reason: `Concurrency ${best.concurrency} is the highest level that keeps p95 TTFT at ${Math.round(Number((best.ttft_ms as Record<string, number>)?.p95 ?? 0))} ms within the SLO, serving ${best.rps} req/s (${Math.round(((best.rps ?? 0) / Math.max(0.01, peakRps)) * 100)} % of the measured peak).`,
    },
  };
}

const benchmarks: Benchmark[] = [
  {
    id: uuid('benchmark:qwen-chat'),
    name: 'qwen3 chat latency sweep',
    description: 'Interactive chat SLO for the support bot: p95 TTFT under 600 ms.',
    deployment_id: IDS.deployments.qwen,
    deployment_name: 'qwen3-0.6b',
    concurrency_levels: [1, 2, 4, 8, 12, 16, 24, 32],
    duration_seconds: 60,
    workload: { input_tokens: 512, output_tokens: 256, requests_per_level: 120, stream: true, dataset: 'sharegpt-short' },
    target_slo: { ttft_ms_p95: 600, e2e_ms_p95: 6000, max_error_rate_pct: 1 },
    auto_tune: true,
    run_count: 3,
    created_at: daysAgo(5),
    updated_at: daysAgo(1),
  },
  {
    id: uuid('benchmark:nomic-embed'),
    name: 'nomic-embed indexing throughput',
    description: 'Batch embedding throughput for the nightly docs re-index.',
    deployment_id: IDS.deployments.nomic,
    deployment_name: 'nomic-embed',
    concurrency_levels: [8, 16, 32, 48, 64],
    duration_seconds: 45,
    workload: { input_tokens: 384, output_tokens: 0, requests_per_level: 400, stream: false, dataset: 'docs-chunks' },
    target_slo: { e2e_ms_p95: 250, max_error_rate_pct: 0.5 },
    auto_tune: false,
    run_count: 1,
    created_at: daysAgo(4),
    updated_at: daysAgo(4),
  },
  {
    id: uuid('benchmark:gemma-mac'),
    name: 'gemma on the Mac (MLX)',
    description: 'How far the M5 goes before TTFT degrades.',
    deployment_id: IDS.deployments.gemma,
    deployment_name: 'gemma-3-1b',
    concurrency_levels: [1, 2, 3, 4, 6, 8],
    duration_seconds: 60,
    workload: { input_tokens: 256, output_tokens: 192, requests_per_level: 60, stream: true },
    target_slo: { ttft_ms_p95: 450 },
    auto_tune: false,
    run_count: 1,
    created_at: daysAgo(2),
    updated_at: daysAgo(2),
  },
];

interface RunState {
  run: BenchmarkRun;
  startedMs: number;
  totalMs: number;
  variants: number;
}

const runs: RunState[] = [];

function completedRun(benchmark: Benchmark, startedHoursAgo: number, autoTune: boolean, label: string): RunState {
  const levels = benchmark.concurrency_levels ?? [1, 2, 4, 8];
  const results = levels.map((c) => resultAt(benchmark, c, 0));
  if (autoTune) results.push(...levels.filter((c) => c >= 4 && c <= 16).map((c) => resultAt(benchmark, c, 1)));
  const { best, rec, met } = recommendationFor(benchmark, results);
  const started = T0 - startedHoursAgo * 3_600_000;
  const instance = fleetStore.instances.find((i) => i.deployment_id === benchmark.deployment_id);
  return {
    startedMs: started,
    totalMs: levels.length * (benchmark.duration_seconds ?? 60) * 1000,
    variants: autoTune ? 1 : 0,
    run: {
      id: uuid(`benchmark-run:${benchmark.id}:${label}`),
      benchmark_id: benchmark.id,
      benchmark_name: benchmark.name,
      status: 'completed',
      progress_pct: 100,
      instance_id: instance?.id ?? null,
      node_id: instance?.node_id ?? null,
      results,
      best_concurrency: best,
      slo_met: met,
      recommendation: rec,
      error_message: null,
      started_at: iso(started),
      finished_at: iso(started + levels.length * (benchmark.duration_seconds ?? 60) * 1000),
      created_at: iso(started - 2000),
    },
  };
}

runs.push(completedRun(benchmarks[0], 26, true, 'a'), completedRun(benchmarks[0], 50, false, 'b'), completedRun(benchmarks[0], 120, false, 'c'));
runs.push(completedRun(benchmarks[1], 96, false, 'a'));
{
  const failed = completedRun(benchmarks[2], 48, false, 'a');
  failed.run.status = 'failed';
  failed.run.results = failed.run.results.slice(0, 3);
  failed.run.best_concurrency = null;
  failed.run.slo_met = null;
  failed.run.recommendation = {};
  failed.run.progress_pct = 50;
  failed.run.error_message = 'Instance restarted by the auto-heal rule during level 4 — run aborted';
  runs.push(failed);
}

/** In-flight runs compute their progress and revealed results from wall time. */
function runView(state: RunState): BenchmarkRun {
  const { run } = state;
  if (run.status !== 'running' && run.status !== 'pending') return run;
  const benchmark = benchmarks.find((b) => b.id === run.benchmark_id);
  if (!benchmark) return run;
  const elapsed = Date.now() - state.startedMs;
  const levels = benchmark.concurrency_levels ?? [1, 2, 4, 8];
  const variantLevels = state.variants ? levels.filter((c) => c >= 4 && c <= 16) : [];
  const steps = levels.length + variantLevels.length;
  if (elapsed < 1500) return { ...run, status: 'pending', progress_pct: 0 };
  const done = Math.min(steps, Math.floor((elapsed - 1500) / (state.totalMs / steps)));
  const results = [...levels.map((c) => resultAt(benchmark, c, 0)), ...variantLevels.map((c) => resultAt(benchmark, c, 1))].slice(0, done);
  if (done >= steps) {
    const { best, rec, met } = recommendationFor(benchmark, results);
    Object.assign(run, { status: 'completed', progress_pct: 100, results, best_concurrency: best, slo_met: met, recommendation: rec, finished_at: new Date().toISOString() });
    return run;
  }
  return { ...run, status: 'running', progress_pct: round(((elapsed - 1500) / state.totalMs) * 100, 1), results };
}

function benchmarkView(benchmark: Benchmark): Benchmark {
  const own = runs.filter((r) => r.run.benchmark_id === benchmark.id).sort((a, b) => b.startedMs - a.startedMs);
  return { ...benchmark, run_count: own.length, last_run: own[0] ? runView(own[0]) : null };
}

function startRun(benchmark: Benchmark, body: Partial<Schemas['BenchmarkStartRequest']>, autoTune: boolean): BenchmarkRun | ReturnType<typeof fail> {
  if (runs.some((r) => r.run.benchmark_id === benchmark.id && (runView(r).status === 'running' || runView(r).status === 'pending'))) {
    return fail(409, 'RUN_IN_PROGRESS', 'A run of this benchmark is already in progress');
  }
  const dep = deploymentRecord(benchmark.deployment_id);
  const instance = body.instance_id ? fleetStore.instances.find((i) => i.id === body.instance_id) : liveInstances(benchmark.deployment_id).find((i) => i.state === 'ready');
  if (!dep || !instance) return fail(409, 'NO_READY_INSTANCE', 'The deployment has no ready instance to benchmark');
  const now = Date.now();
  const levels = benchmark.concurrency_levels ?? [1, 2, 4, 8];
  const state: RunState = {
    startedMs: now,
    // Compressed for the mock: ~4 s per level instead of duration_seconds.
    totalMs: (levels.length + (autoTune ? levels.filter((c) => c >= 4 && c <= 16).length : 0)) * 4000,
    variants: autoTune ? 1 : 0,
    run: {
      id: newId('benchmark-run'),
      benchmark_id: benchmark.id,
      benchmark_name: benchmark.name,
      status: 'pending',
      progress_pct: 0,
      instance_id: instance.id,
      node_id: instance.node_id,
      results: [],
      best_concurrency: null,
      slo_met: null,
      recommendation: {},
      error_message: null,
      started_at: iso(now),
      finished_at: null,
      created_at: iso(now),
    },
  };
  runs.push(state);
  recordCommand(instance.node_id, 'run_benchmark', { instanceId: instance.id, ms: state.totalMs });
  return runView(state);
}

function runsCsv(run: BenchmarkRun): string {
  const header = 'concurrency,requests,errors,rps,generation_tps,ttft_p50_ms,ttft_p95_ms,tpot_p50_ms,e2e_p50_ms,e2e_p95_ms,slo_met,launch_flags';
  const rows = run.results.map((r) =>
    [
      r.concurrency,
      r.requests,
      r.errors,
      r.rps,
      r.generation_tps,
      (r.ttft_ms as Record<string, number>)?.p50,
      (r.ttft_ms as Record<string, number>)?.p95,
      (r.tpot_ms as Record<string, number>)?.p50,
      (r.e2e_ms as Record<string, number>)?.p50,
      (r.e2e_ms as Record<string, number>)?.p95,
      r.slo_met,
      r.launch_flags ? `"${JSON.stringify(r.launch_flags).replace(/"/g, '""')}"` : '',
    ].join(','),
  );
  return [header, ...rows].join('\n');
}

// ─── Catalog search fixtures ────────────────────────────────────────────────

type SearchSeed = [repo: string, type: ModelType, formats: FileFormat[], downloads: number, likes: number, sizeGb: number, tags: string[], pipeline: string];

const HF_SEEDS: SearchSeed[] = [
  ['Qwen/Qwen3-0.6B-GGUF', 'thinking', ['gguf'], 412_331, 211, 0.64, ['qwen3', 'gguf', 'tools'], 'text-generation'],
  ['Qwen/Qwen3-8B', 'thinking', ['safetensors'], 3_912_044, 1_102, 16.4, ['qwen3', 'conversational'], 'text-generation'],
  ['Qwen/Qwen3-8B-GGUF', 'thinking', ['gguf'], 288_901, 160, 8.7, ['qwen3', 'gguf'], 'text-generation'],
  ['Qwen/Qwen3-30B-A3B-Instruct-2507', 'chat', ['safetensors'], 1_240_118, 834, 61.1, ['qwen3_moe', 'moe'], 'text-generation'],
  ['unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF', 'chat', ['gguf'], 802_430, 402, 18.6, ['qwen3_moe', 'gguf', 'unsloth'], 'text-generation'],
  ['Qwen/Qwen2.5-VL-7B-Instruct', 'vision', ['safetensors'], 2_118_337, 1_480, 16.6, ['qwen2_5_vl', 'multimodal'], 'image-text-to-text'],
  ['google/gemma-3-1b-it', 'chat', ['safetensors'], 1_811_540, 612, 2.0, ['gemma3_text', 'gated'], 'text-generation'],
  ['google/gemma-3-12b-it-qat-q4_0-gguf', 'vision', ['gguf'], 210_882, 377, 8.1, ['gemma3', 'gguf', 'qat'], 'image-text-to-text'],
  ['mlx-community/gemma-3-1b-it-qat-4bit', 'chat', ['mlx'], 96_410, 41, 0.77, ['mlx', 'gemma3_text'], 'text-generation'],
  ['mlx-community/Qwen3-8B-4bit', 'thinking', ['mlx'], 151_200, 58, 4.6, ['mlx', 'qwen3'], 'text-generation'],
  ['meta-llama/Llama-3.3-70B-Instruct', 'chat', ['safetensors'], 1_603_115, 2_390, 141.1, ['llama', 'gated'], 'text-generation'],
  ['casperhansen/llama-3.3-70b-instruct-awq', 'chat', ['safetensors'], 74_220, 33, 39.8, ['llama', 'awq'], 'text-generation'],
  ['bartowski/Llama-3.2-3B-Instruct-GGUF', 'chat', ['gguf'], 690_204, 188, 2.0, ['llama', 'gguf'], 'text-generation'],
  ['microsoft/Phi-4-mini-instruct', 'chat', ['safetensors'], 512_377, 590, 7.7, ['phi3', 'tools'], 'text-generation'],
  ['mistralai/Mistral-Small-3.2-24B-Instruct-2506', 'vision', ['safetensors'], 402_118, 471, 48.0, ['mistral3'], 'image-text-to-text'],
  ['deepseek-ai/DeepSeek-R1-Distill-Qwen-7B', 'thinking', ['safetensors'], 1_022_401, 731, 15.2, ['qwen2', 'reasoning'], 'text-generation'],
  ['nomic-ai/nomic-embed-text-v1.5-GGUF', 'embedding', ['gguf'], 340_558, 132, 0.55, ['nomic_bert', 'gguf', 'sentence-similarity'], 'feature-extraction'],
  ['BAAI/bge-m3', 'embedding', ['safetensors', 'pickle'], 4_410_022, 2_120, 2.3, ['xlm-roberta', 'sentence-similarity'], 'sentence-similarity'],
  ['BAAI/bge-reranker-v2-m3', 'rerank', ['safetensors'], 1_301_744, 690, 2.3, ['xlm-roberta', 'reranker'], 'text-classification'],
  ['ggerganov/whisper.cpp', 'stt', ['other'], 1_904_115, 1_011, 6.2, ['whisper', 'ggml'], 'automatic-speech-recognition'],
  ['hexgrad/Kokoro-82M', 'tts', ['safetensors', 'pickle'], 2_480_331, 4_910, 0.33, ['kokoro', 'tts'], 'text-to-speech'],
  ['black-forest-labs/FLUX.1-schnell', 'image', ['safetensors'], 1_120_004, 4_312, 33.7, ['flux', 'diffusers'], 'text-to-image'],
  ['Wan-AI/Wan2.2-TI2V-5B', 'video', ['safetensors'], 211_090, 512, 24.1, ['wan', 'diffusers'], 'text-to-video'],
  ['microsoft/DialoGPT-medium', 'chat', ['pickle'], 301_221, 380, 0.86, ['gpt2', 'legacy'], 'text-generation'],
];

const MS_SEEDS: SearchSeed[] = [
  ['Qwen/Qwen3-8B', 'thinking', ['safetensors'], 812_004, 310, 16.4, ['qwen3'], 'text-generation'],
  ['Qwen/Qwen2.5-VL-7B-Instruct', 'vision', ['safetensors'], 421_552, 288, 16.6, ['qwen2_5_vl'], 'image-text-to-text'],
  ['ZhipuAI/glm-4-9b-chat', 'chat', ['safetensors'], 220_310, 194, 18.8, ['glm4'], 'text-generation'],
  ['iic/SenseVoiceSmall', 'stt', ['pickle'], 501_884, 402, 0.94, ['sensevoice', 'asr'], 'automatic-speech-recognition'],
  ['iic/CosyVoice2-0.5B', 'tts', ['safetensors'], 180_225, 260, 1.9, ['cosyvoice'], 'text-to-speech'],
  ['deepseek-ai/DeepSeek-R1-Distill-Qwen-7B', 'thinking', ['safetensors'], 330_118, 152, 15.2, ['reasoning'], 'text-generation'],
  ['AI-ModelScope/bge-reranker-v2-m3', 'rerank', ['safetensors'], 88_011, 41, 2.3, ['reranker'], 'text-classification'],
];

function searchCatalog(req: MockRequest): Schemas['CatalogSearchResult'][] {
  const q = (req.query.get('q') ?? '').trim().toLowerCase();
  const source = (req.query.get('source') ?? 'huggingface') as 'huggingface' | 'modelscope';
  const type = req.query.get('type');
  const format = req.query.get('format');
  const author = (req.query.get('author') ?? '').toLowerCase();
  const sort = req.query.get('sort') ?? 'downloads';
  const limit = Number(req.query.get('limit') ?? 30);
  const seeds = source === 'modelscope' ? MS_SEEDS : HF_SEEDS;
  const terms = q.split(/\s+/).filter(Boolean);
  let list = seeds.filter(([repo, , , , , , tags]) => terms.every((t) => repo.toLowerCase().includes(t) || tags.some((tag) => tag.includes(t))));
  if (list.length === 0 && q) {
    // Unknown query: synthesise the usual community quantisations so every search shows something.
    const base = q.replace(/[^a-z0-9.-]+/gi, '-').replace(/^-|-$/g, '');
    const title = base.split('-').map((w) => (w.length <= 3 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1))).join('-');
    list = [
      [`bartowski/${title}-GGUF`, 'chat', ['gguf'], 18_000 + (hash32(base) % 40_000), 31, 4.9, ['gguf'], 'text-generation'],
      [`unsloth/${title}-GGUF`, 'chat', ['gguf'], 9_000 + (hash32(base + 'u') % 20_000), 17, 4.9, ['gguf', 'unsloth'], 'text-generation'],
      [`mlx-community/${title}-4bit`, 'chat', ['mlx'], 2_000 + (hash32(base + 'm') % 5_000), 6, 2.8, ['mlx'], 'text-generation'],
    ];
  }
  const registered = new Map(models.map((m) => [m.repo_id.toLowerCase(), m.id]));
  let results = list
    .filter(([repo, t, fmts]) => (!type || t === type) && (!format || fmts.includes(format as FileFormat)) && (!author || repo.toLowerCase().startsWith(`${author}/`)))
    .map(([repo, t, fmts, downloads, likes, sizeGb, tags, pipeline]) => ({
      source,
      repo_id: repo,
      name: repo.split('/')[1],
      author: repo.split('/')[0],
      detected_type: t,
      formats: fmts,
      downloads,
      likes,
      total_size_bytes: Math.round(sizeGb * GB),
      tags,
      pipeline_tag: pipeline,
      library: fmts.includes('gguf') ? 'gguf' : fmts.includes('mlx') ? 'mlx' : 'transformers',
      gated: tags.includes('gated'),
      private: false,
      last_modified: daysAgo(1 + (hash32(repo) % 200)),
      already_registered_model_id: registered.get(repo.toLowerCase()) ?? null,
    }));
  results = results.sort((a, b) => (sort === 'likes' ? (b.likes ?? 0) - (a.likes ?? 0) : sort === 'last_modified' ? String(b.last_modified).localeCompare(String(a.last_modified)) : (b.downloads ?? 0) - (a.downloads ?? 0)));
  return results.slice(0, limit);
}

function paramsFromName(repo: string): number {
  const m = /(\d+(?:\.\d+)?)\s*[bB](?![a-z])/.exec(repo.replace(/A\d+B/i, ''));
  if (m) return Number(m[1]) * 1e9;
  const mm = /(\d+)\s*[mM](?![a-z])/.exec(repo);
  return mm ? Number(mm[1]) * 1e6 : 7e9;
}

function familyOf(repo: string): string {
  const r = repo.toLowerCase();
  if (r.includes('qwen3')) return 'qwen3';
  if (r.includes('qwen2.5-vl')) return 'qwen2.5-vl';
  if (r.includes('gemma-3') || r.includes('gemma3')) return 'gemma3';
  if (r.includes('llama-3') || r.includes('llama3')) return 'llama3';
  if (r.includes('phi-4')) return 'phi4';
  if (r.includes('mistral')) return 'mistral';
  if (r.includes('deepseek')) return 'deepseek';
  if (r.includes('nomic')) return 'nomic-bert';
  if (r.includes('bge')) return 'xlm-roberta';
  if (r.includes('whisper')) return 'whisper';
  if (r.includes('glm')) return 'glm4';
  return r.split('/')[1]?.split('-')[0] ?? 'unknown';
}

function remoteFiles(source: 'huggingface' | 'modelscope', repo: string): Schemas['CatalogRemoteFiles'] {
  const seed = [...HF_SEEDS, ...MS_SEEDS].find(([r]) => r.toLowerCase() === repo.toLowerCase());
  const type: ModelType = seed?.[1] ?? (/(embed|bge)/i.test(repo) ? 'embedding' : 'chat');
  const params = paramsFromName(repo);
  const lower = repo.toLowerCase();
  const out: Schemas['CatalogRemoteFile'][] = [];
  const name = repo.split('/')[1] ?? repo;
  if (lower.includes('gguf') || seed?.[2].includes('gguf')) {
    const base = name.replace(/-GGUF$/i, '');
    const quants: [string, number][] = [['Q2_K', 0.37], ['Q3_K_M', 0.49], ['Q4_K_M', 0.6], ['Q5_K_M', 0.71], ['Q6_K', 0.82], ['Q8_0', 1.06], ['F16', 2.0]];
    for (const [q, bpp] of quants) out.push({ path: `${base}-${q}.gguf`, size_bytes: Math.round(params * bpp), format: 'gguf', lfs: true, is_required: false, sha256: sha(`${repo}${q}`) });
  } else if (lower.includes('whisper.cpp')) {
    for (const [f, size] of [['ggml-base.en.bin', 147_964_211], ['ggml-small.bin', 487_601_967], ['ggml-large-v3-turbo-q5_0.bin', 574_041_195], ['ggml-large-v3-turbo.bin', 1_624_555_275], ['ggml-large-v3.bin', 3_095_033_483]] as [string, number][]) {
      out.push({ path: f, size_bytes: size, format: 'other', lfs: true, is_required: false, sha256: sha(f) });
    }
  } else if (seed?.[2].includes('pickle') && !seed[2].includes('safetensors')) {
    out.push({ path: 'pytorch_model.bin', size_bytes: Math.round((seed[5] ?? 1) * GB), format: 'pickle', lfs: true, is_required: true, sha256: sha(`${repo}bin`) });
    out.push({ path: 'config.json', size_bytes: 642, format: 'other', is_required: true, sha256: null });
  } else {
    const total = Math.round((seed?.[5] ?? (params * 2) / GB) * GB);
    const shards = Math.max(1, Math.ceil(total / (4.9 * GB)));
    const mlx = lower.includes('mlx');
    if (shards === 1) out.push({ path: 'model.safetensors', size_bytes: total, format: mlx ? 'mlx' : 'safetensors', lfs: true, is_required: true, sha256: sha(`${repo}st`) });
    else for (let i = 1; i <= shards; i += 1) out.push({ path: `model-${String(i).padStart(5, '0')}-of-${String(shards).padStart(5, '0')}.safetensors`, size_bytes: Math.round(total / shards), format: mlx ? 'mlx' : 'safetensors', lfs: true, is_required: true, sha256: sha(`${repo}${i}`) });
    if (seed?.[2].includes('pickle')) out.push({ path: 'pytorch_model.bin', size_bytes: total, format: 'pickle', lfs: true, is_required: false, sha256: sha(`${repo}bin`) });
    out.push({ path: 'config.json', size_bytes: 1_102, format: 'other', is_required: true, sha256: null }, { path: 'tokenizer.json', size_bytes: 11_422_654, format: 'other', is_required: true, sha256: null }, { path: 'tokenizer_config.json', size_bytes: 9_732, format: 'other', is_required: true, sha256: null });
  }
  const family = familyOf(repo);
  return {
    source,
    repo_id: repo,
    revision: hexId(`rev:${repo}`, 16).slice(0, 7),
    files: out,
    total_size_bytes: out.reduce((s, f) => s + f.size_bytes, 0),
    has_pickle_only_weights: out.some((f) => f.format === 'pickle') && !out.some((f) => f.format === 'safetensors' || f.format === 'gguf' || f.format === 'mlx'),
    config: { architectures: [`${family}ForCausalLM`], max_position_embeddings: type === 'embedding' ? 8192 : 32768, num_parameters: params },
    suggested: {
      name: name.toLowerCase().replace(/-gguf$/, '').replace(/-instruct.*$/, '').replace(/_/g, '-'),
      type,
      family,
      context_size: type === 'embedding' ? 2048 : type === 'stt' ? null : 32768,
      tool_parser: PARSER_BY_FAMILY[family] ? (lower.includes('gguf') ? 'jinja' : PARSER_BY_FAMILY[family].vllm ?? null) : null,
    },
  };
}

function scanLocal(path: string): Schemas['LocalScanCandidate'][] {
  // The scanning host expands "~" (the Mac in the fixture fleet belongs to alice).
  const p = path.replace(/^~(?=\/)/, '/Users/alice').replace(/\/+$/, '');
  const gguf = (file: string, size: number): Schemas['CatalogRemoteFile'] => ({ path: file, size_bytes: size, format: 'gguf', is_required: true, sha256: null });
  if (p.includes('.ollama')) {
    return [
      { path: `${p}/manifests/registry.ollama.ai/library/qwen3/0.6b`, kind: 'ollama_blob', format: 'gguf', total_size_bytes: 522_653_767, files: [gguf('blobs/sha256-7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa', 522_653_767)], suggested: { name: 'qwen3-0.6b', type: 'thinking', family: 'qwen3', context_size: 40960, quantization: 'Q4_K_M' } },
      { path: `${p}/manifests/registry.ollama.ai/library/tinyllama/latest`, kind: 'ollama_blob', format: 'gguf', total_size_bytes: 637_699_456, files: [gguf('blobs/sha256-2af3b81862c6be03c769683af18efdadb2c33f60ff32ab6f83e42c043d6c7816', 637_699_456)], suggested: { name: 'tinyllama', type: 'chat', family: 'llama', context_size: 2048, quantization: 'Q4_0' } },
    ];
  }
  if (p.includes('.lmstudio')) {
    return [
      {
        path: `${p}/mlx-community/gemma-3-1b-it-qat-4bit`,
        kind: 'lmstudio',
        format: 'mlx',
        total_size_bytes: 766_369_294,
        files: [
          { path: 'model.safetensors', size_bytes: 731_825_420, format: 'mlx', is_required: true },
          { path: 'config.json', size_bytes: 1_643, format: 'other', is_required: true },
          { path: 'tokenizer.json', size_bytes: 33_384_570, format: 'other', is_required: true },
          { path: 'tokenizer_config.json', size_bytes: 1_156_999, format: 'other', is_required: true },
        ],
        suggested: { name: 'gemma-3-1b-it', type: 'chat', family: 'gemma3', context_size: 32768, quantization: '4bit (QAT)' },
      },
      { path: `${p}/nomic-ai/nomic-embed-text-v1.5-GGUF`, kind: 'lmstudio', format: 'gguf', total_size_bytes: 146_146_432, files: [gguf('nomic-embed-text-v1.5.Q8_0.gguf', 146_146_432)], suggested: { name: 'nomic-embed-text-v1.5', type: 'embedding', family: 'nomic-bert', context_size: 2048, quantization: 'Q8_0' } },
    ];
  }
  if (p.includes('huggingface')) {
    return [{ path: `${p}/models--Qwen--Qwen3-0.6B/snapshots/c1899de`, kind: 'hf_cache', format: 'safetensors', total_size_bytes: 1_503_300_328, files: [{ path: 'model.safetensors', size_bytes: 1_503_300_328, format: 'safetensors', is_required: true }, { path: 'config.json', size_bytes: 726, format: 'other', is_required: true }], suggested: { name: 'qwen3-0.6b-hf', type: 'thinking', family: 'qwen3', context_size: 40960, quantization: null } }];
  }
  if (p.endsWith('.gguf')) {
    const file = p.split('/').pop() ?? p;
    return [{ path: p, kind: 'plain', format: 'gguf', total_size_bytes: 4_920_734_016, files: [gguf(file, 4_920_734_016)], suggested: { name: file.replace(/\.gguf$/, '').toLowerCase(), type: 'chat', family: familyOf(file), context_size: 8192, quantization: /(Q\d_K_[SML]|Q\d_\d|F16)/i.exec(file)?.[1] ?? null } }];
  }
  if (p === '/models' || p.startsWith('/models/') || p.startsWith('/srv') || p.startsWith('/data')) {
    return [
      { path: `${p}/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`, kind: 'plain', format: 'gguf', total_size_bytes: 4_920_734_016, files: [gguf('Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf', 4_920_734_016)], suggested: { name: 'llama-3.1-8b-instruct', type: 'chat', family: 'llama3', context_size: 16384, quantization: 'Q4_K_M' } },
      { path: `${p}/bge-reranker-v2-m3`, kind: 'plain', format: 'safetensors', total_size_bytes: 2_271_071_852, files: [{ path: 'model.safetensors', size_bytes: 2_271_071_852, format: 'safetensors', is_required: true }, { path: 'config.json', size_bytes: 799, format: 'other', is_required: true }], suggested: { name: 'bge-reranker-v2-m3', type: 'rerank', family: 'xlm-roberta', context_size: 8192, quantization: null } },
    ];
  }
  return [];
}

// ─── Handlers ───────────────────────────────────────────────────────────────

function modelRecord(id: string) {
  return models.find((m) => m.id === id);
}

function versionRecord(modelId: string, versionId: string) {
  return versions.find((v) => v.id === versionId && v.model_id === modelId);
}

/**
 * External (observed) deployments and instances are read-only: every mutation answers 409
 * EXTERNAL_DEPLOYMENT, as the control plane does.
 */
function guardExternal(list: MockHandler[]): MockHandler[] {
  const externalDeployment = (id: string | undefined) => !!id && fleetStore.deployments.some((d) => d.id === id && d.origin === 'external');
  const externalInstance = (id: string | undefined) => !!id && fleetStore.instances.some((i) => i.id === id && i.origin === 'external');
  return list.map((h) => {
    if (h.method === 'GET') return h;
    const guarded = h.path.startsWith('/deployments/:deployment_id') || h.path.startsWith('/instances/:instance_id') || (h.method === 'POST' && (h.path === '/benchmarks' || h.path === '/swaps'));
    if (!guarded) return h;
    return {
      ...h,
      handle: (req: MockRequest) => {
        const body = (req.body ?? {}) as { deployment_id?: string; instance_id?: string };
        if (externalDeployment(req.params.deployment_id ?? body.deployment_id) || externalInstance(req.params.instance_id ?? body.instance_id)) {
          return fail(409, 'EXTERNAL_DEPLOYMENT', 'This is an external deployment that GPU Deck only observes; it cannot be changed from GPU Deck');
        }
        return h.handle(req);
      },
    };
  });
}

registerMockHandlers('models', guardExternal([
  // ── Catalog ───────────────────────────────────────────────────────────────
  { method: 'GET', path: '/catalog/search', handle: (req) => { const r = searchCatalog(req); return ok(r, { total: r.length, page: 1, page_size: r.length, has_more: false }); } },
  {
    method: 'GET',
    path: '/catalog/remote-files',
    handle: (req) => {
      const repo = req.query.get('repo_id');
      if (!repo) return fail(422, 'VALIDATION_ERROR', 'repo_id is required');
      return ok(remoteFiles((req.query.get('source') ?? 'huggingface') as 'huggingface' | 'modelscope', repo));
    },
  },
  {
    method: 'POST',
    path: '/catalog/local/scan',
    handle: (req) => {
      const body = bodyOf<Schemas['LocalScanRequest']>(req);
      if (!body.path || !(body.path.startsWith('/') || body.path.startsWith('~/'))) return fail(422, 'VALIDATION_ERROR', 'path must be absolute (or start with ~/ on the scanning host)');
      const list = scanLocal(body.path);
      return ok(list, { total: list.length, page: 1, page_size: list.length, has_more: false });
    },
  },

  // ── Models ────────────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/models',
    handle: (req) => {
      const q = req.query;
      const search = (q.get('search') ?? '').toLowerCase();
      const filters = { type: q.get('type'), source: q.get('source'), priority: q.get('priority'), family: q.get('family'), tag: q.get('tag') };
      const list = models
        .filter(
          (m) =>
            (!filters.type || m.type === filters.type) &&
            (!filters.source || m.source === filters.source) &&
            (!filters.priority || m.priority === filters.priority) &&
            (!filters.family || m.family === filters.family) &&
            (!filters.tag || (m.tags ?? []).includes(filters.tag)) &&
            (!search || [m.name, m.display_name ?? '', m.repo_id, m.family ?? '', ...(m.aliases ?? [])].some((s) => s.toLowerCase().includes(search))),
        )
        .map(modelView);
      return paginate(sortBy(list, q.get('sort'), '-created_at'), q, 25);
    },
  },
  {
    method: 'POST',
    path: '/models',
    handle: (req) => {
      const body = bodyOf<Schemas['ModelCreate']>(req);
      if (!body.name || !body.repo_id || !body.source || !body.type) return fail(422, 'VALIDATION_ERROR', 'name, source, repo_id and type are required');
      if (models.some((m) => m.name === body.name)) return fail(409, 'CONFLICT', `A model is already served as "${body.name}"`);
      const now = new Date().toISOString();
      const model: Model = {
        id: newId('model'),
        name: body.name,
        display_name: body.display_name ?? null,
        description: body.description ?? null,
        source: body.source,
        repo_id: body.repo_id,
        type: body.type,
        family: body.family ?? familyOf(body.repo_id),
        priority: body.priority ?? 'normal',
        tool_parser_default: body.tool_parser_default ?? null,
        default_context_size: body.default_context_size ?? null,
        default_launch_flags: body.default_launch_flags ?? {},
        trust_remote_code: !!body.trust_remote_code,
        allow_pickle: !!body.allow_pickle,
        tags: body.tags ?? [],
        aliases: body.aliases ?? [],
        metadata: body.metadata ?? {},
        created_at: now,
        updated_at: now,
      };
      models.unshift(model);
      const init = body.initial_version ?? { version: 'main', is_default: true };
      const listing = body.source === 'local_path' ? scanLocal(init.local_path ?? body.repo_id)[0]?.files ?? [] : remoteFiles(body.source as 'huggingface' | 'modelscope', body.repo_id).files;
      const chosen = (init.files?.length ? listing.filter((f) => init.files?.includes(f.path)) : listing).filter((f) => f.format !== 'pickle' || body.allow_pickle);
      const pickleOnly = listing.length > 0 && listing.every((f) => f.format === 'pickle' || f.format === 'other');
      const format: FileFormat = chosen.find((f) => f.format !== 'other')?.format ?? (pickleOnly ? 'pickle' : 'other');
      const version = addVersion(
        model.id,
        {
          version: init.version || 'main',
          format,
          quantization: init.quantization ?? null,
          revision: init.revision ?? null,
          isDefault: true,
          status: pickleOnly && !body.allow_pickle ? 'blocked' : 'verifying',
          statusMessage: pickleOnly && !body.allow_pickle ? 'Only pickle weights — blocked until "Allow pickle" is enabled' : 'Requesting hashes from the source',
          params: paramsFromName(body.repo_id),
          files: (chosen.length ? chosen : listing).map((f) => [f.path, f.size_bytes, f.is_required]),
        },
        now,
      );
      if (version.status === 'verifying') {
        setTimeout(() => {
          version.status = 'ready';
          version.status_message = null;
          version.updated_at = new Date().toISOString();
        }, 5000);
      }
      return created(modelView(model));
    },
  },
  { method: 'GET', path: '/models/:model_id', handle: (req) => { const m = modelRecord(req.params.model_id); return m ? ok(modelView(m)) : notFound('Model'); } },
  {
    method: 'PATCH',
    path: '/models/:model_id',
    handle: (req) => {
      const m = modelRecord(req.params.model_id);
      if (!m) return notFound('Model');
      const body = bodyOf<Schemas['ModelUpdate']>(req);
      if (body.name && models.some((x) => x.id !== m.id && x.name === body.name)) return fail(409, 'CONFLICT', `A model is already served as "${body.name}"`);
      Object.assign(m, body, { updated_at: new Date().toISOString() });
      for (const d of fleetStore.deployments) if (d.model_id === m.id) d.effective_priority = d.priority_override ?? m.priority;
      if (body.allow_pickle) {
        for (const v of versions) if (v.model_id === m.id && v.status === 'blocked') { v.status = 'ready'; v.status_message = null; }
      }
      return ok(modelView(m));
    },
  },
  {
    method: 'DELETE',
    path: '/models/:model_id',
    handle: (req) => {
      const m = modelRecord(req.params.model_id);
      if (!m) return notFound('Model');
      if (fleetStore.deployments.some((d) => d.model_id === m.id)) return fail(409, 'MODEL_IN_USE', 'Delete the deployments that serve this model first');
      models.splice(models.indexOf(m), 1);
      if (req.query.get('evict_cache') === 'true') evictItems(fleetStore.cacheItems.filter((c) => c.model_id === m.id), true);
      return noContent();
    },
  },
  { method: 'GET', path: '/models/:model_id/versions', handle: (req) => (modelRecord(req.params.model_id) ? paginate(versions.filter((v) => v.model_id === req.params.model_id), req.query) : notFound('Model')) },
  {
    method: 'POST',
    path: '/models/:model_id/versions',
    handle: (req) => {
      const m = modelRecord(req.params.model_id);
      if (!m) return notFound('Model');
      const body = bodyOf<Schemas['ModelVersionCreate']>(req);
      if (!body.version) return fail(422, 'VALIDATION_ERROR', 'version is required');
      if (versions.some((v) => v.model_id === m.id && v.version === body.version)) return fail(409, 'CONFLICT', `Version "${body.version}" already exists`);
      const listing = m.source === 'local_path' ? scanLocal(body.local_path ?? m.repo_id)[0]?.files ?? [] : remoteFiles(m.source as 'huggingface' | 'modelscope', m.repo_id).files;
      const chosen = body.files?.length ? listing.filter((f) => body.files?.includes(f.path) || f.format === 'other') : listing;
      if (body.is_default) for (const v of versions) if (v.model_id === m.id) v.is_default = false;
      const version = addVersion(m.id, { version: body.version, format: chosen.find((f) => f.format !== 'other')?.format ?? 'other', quantization: body.quantization ?? null, revision: body.revision ?? null, isDefault: !!body.is_default, status: 'verifying', params: paramsFromName(m.repo_id), files: chosen.map((f) => [f.path, f.size_bytes, f.is_required]) }, new Date().toISOString());
      setTimeout(() => { version.status = 'ready'; }, 4000);
      return created(version);
    },
  },
  { method: 'GET', path: '/models/:model_id/versions/:version_id', handle: (req) => { const v = versionRecord(req.params.model_id, req.params.version_id); return v ? ok(v) : notFound('Version'); } },
  {
    method: 'PATCH',
    path: '/models/:model_id/versions/:version_id',
    handle: (req) => {
      const v = versionRecord(req.params.model_id, req.params.version_id);
      if (!v) return notFound('Version');
      const body = bodyOf<{ is_default?: boolean; quantization?: string | null; version?: string }>(req);
      if (body.is_default) for (const x of versions) if (x.model_id === v.model_id) x.is_default = false;
      Object.assign(v, body, { updated_at: new Date().toISOString() });
      return ok(v);
    },
  },
  {
    method: 'DELETE',
    path: '/models/:model_id/versions/:version_id',
    handle: (req) => {
      const v = versionRecord(req.params.model_id, req.params.version_id);
      if (!v) return notFound('Version');
      if (v.is_default && versions.filter((x) => x.model_id === v.model_id).length > 1) return fail(409, 'DEFAULT_VERSION', 'Make another version the default first');
      if (fleetStore.deployments.some((d) => d.model_version_id === v.id)) return fail(409, 'VERSION_IN_USE', 'A deployment pins this version');
      versions.splice(versions.indexOf(v), 1);
      return noContent();
    },
  },
  { method: 'GET', path: '/models/:model_id/versions/:version_id/files', handle: (req) => paginate(files.filter((f) => f.model_version_id === req.params.version_id).map(fileView), req.query, 200) },
  {
    method: 'POST',
    path: '/models/:model_id/versions/:version_id/verify',
    handle: (req) => {
      const m = modelRecord(req.params.model_id);
      const v = versionRecord(req.params.model_id, req.params.version_id);
      if (!m || !v) return notFound('Version');
      const own = files.filter((f) => f.model_version_id === v.id);
      const pickleBlocked = own.some((f) => f.format === 'pickle') && !m.allow_pickle && !own.some((f) => f.format === 'safetensors' || f.format === 'gguf' || f.format === 'mlx');
      for (const f of own) if (!f.sha256) f.sha256 = sha(`${f.id}:verified`);
      v.status = pickleBlocked ? 'blocked' : 'ready';
      v.status_message = pickleBlocked ? v.status_message : null;
      v.updated_at = new Date().toISOString();
      return ok({ model_version_id: v.id, status: v.status, files_checked: own.length, mismatches: [], pickle_blocked: pickleBlocked });
    },
  },
  { method: 'GET', path: '/models/:model_id/versions/:version_id/cache', handle: (req) => { tickDownloads(); return paginate(fleetStore.cacheItems.filter((c) => c.model_version_id === req.params.version_id && c.state !== 'evicted'), req.query, 200); } },

  // ── Engines ───────────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/engines',
    handle: (req) => {
      const enabled = req.query.get('enabled');
      const type = req.query.get('model_type') as ModelType | null;
      const vendor = req.query.get('vendor') as Vendor | null;
      const list = engines
        .filter((e) => (enabled === null || String(e.is_enabled) === enabled) && (!type || (e.supported_model_types ?? []).includes(type)) && (!vendor || (e.supported_vendors ?? []).includes(vendor)))
        .map(engineView);
      return ok(list, { total: list.length, page: 1, page_size: list.length, has_more: false });
    },
  },
  {
    method: 'POST',
    path: '/engines/compat-check',
    handle: (req) => {
      const body = bodyOf<Schemas['CompatCheckRequest']>(req);
      const version = versions.find((v) => v.id === body.model_version_id);
      if (!version) return notFound('Model version');
      const opts = { vendor: body.device_vendor ?? null, nodeId: body.node_id ?? null, contextSize: body.context_size ?? null, gpuCount: body.gpu_count ?? 1 };
      let list: CompatCheck[];
      if (body.engine_version_id) {
        const ev = engineVersions.find((x) => x.id === body.engine_version_id);
        if (!ev) return notFound('Engine version');
        list = [compatCheck(version, ev, opts)];
      } else {
        list = compatAll(version, opts);
      }
      compatHistory.unshift(...list);
      return ok(list, { total: list.length, page: 1, page_size: list.length, has_more: false });
    },
  },
  {
    method: 'GET',
    path: '/engines/compat-checks',
    handle: (req) => {
      const mv = req.query.get('model_version_id');
      const ev = req.query.get('engine_version_id');
      const result = req.query.get('result');
      return paginate(compatHistory.filter((c) => (!mv || c.model_version_id === mv) && (!ev || c.engine_version_id === ev) && (!result || c.result === result)), req.query);
    },
  },
  {
    method: 'POST',
    path: '/engines/auto-pick',
    handle: (req) => {
      const body = bodyOf<Schemas['AutoPickRequest']>(req);
      const version = versions.find((v) => v.id === body.model_version_id);
      if (!version) return notFound('Model version');
      return ok(autoPick(version, { contextSize: body.context_size, gpuCount: body.gpu_count, preset: body.preset ?? null, nodeId: body.node_id ?? null }));
    },
  },
  { method: 'GET', path: '/engines/:engine_id', handle: (req) => { const e = engines.find((x) => x.id === req.params.engine_id); return e ? ok(engineView(e)) : notFound('Engine'); } },
  {
    method: 'PATCH',
    path: '/engines/:engine_id',
    handle: (req) => {
      const e = engines.find((x) => x.id === req.params.engine_id);
      if (!e) return notFound('Engine');
      const body = bodyOf<Schemas['EngineUpdate']>(req);
      if (body.is_enabled === false && fleetStore.deployments.some((d) => d.engine_name === e.name && d.desired_running)) {
        return fail(409, 'ENGINE_IN_USE', `Running deployments use ${e.display_name} — stop them before disabling it`);
      }
      Object.assign(e, body, { updated_at: new Date().toISOString() });
      return ok(engineView(e));
    },
  },
  { method: 'GET', path: '/engines/:engine_id/versions', handle: (req) => paginate(engineVersions.filter((v) => v.engine_id === req.params.engine_id), req.query) },
  {
    method: 'POST',
    path: '/engines/:engine_id/versions',
    handle: (req) => {
      const e = engines.find((x) => x.id === req.params.engine_id);
      if (!e) return notFound('Engine');
      const body = bodyOf<Schemas['EngineVersionCreate']>(req);
      if (!body.version) return fail(422, 'VALIDATION_ERROR', 'version is required');
      if (engineVersions.some((v) => v.engine_id === e.id && v.version === body.version)) return fail(409, 'CONFLICT', `${e.display_name} ${body.version} already exists`);
      if (e.runtime === 'container' && !body.image) return fail(422, 'VALIDATION_ERROR', 'Container engines need an image');
      const now = new Date().toISOString();
      if (body.is_default) for (const v of engineVersions) if (v.engine_id === e.id) v.is_default = false;
      const version: EngineVersion = {
        id: newId('engine-version'),
        engine_id: e.id,
        engine_name: e.name,
        version: body.version,
        status: body.status ?? 'available',
        image: body.image ?? null,
        is_default: !!body.is_default || !engineVersions.some((v) => v.engine_id === e.id),
        artifacts: body.artifacts ?? {},
        launch_template: body.launch_template ?? {},
        default_launch_flags: body.default_launch_flags ?? {},
        health_config: body.health_config ?? { path: '/health', interval_seconds: 5, timeout_seconds: 3, healthy_statuses: [200] },
        metrics_config: body.metrics_config ?? {},
        port_range: body.port_range ?? [10240, 10340],
        min_driver_version: body.min_driver_version ?? null,
        requirements: body.requirements ?? {},
        created_at: now,
        updated_at: now,
      };
      engineVersions.push(version);
      return created(version);
    },
  },
  { method: 'GET', path: '/engines/:engine_id/versions/:version_id', handle: (req) => { const v = engineVersions.find((x) => x.id === req.params.version_id && x.engine_id === req.params.engine_id); return v ? ok(v) : notFound('Engine version'); } },
  {
    method: 'PATCH',
    path: '/engines/:engine_id/versions/:version_id',
    handle: (req) => {
      const v = engineVersions.find((x) => x.id === req.params.version_id && x.engine_id === req.params.engine_id);
      if (!v) return notFound('Engine version');
      const body = bodyOf<Schemas['EngineVersionUpdate']>(req);
      if (body.is_default) for (const x of engineVersions) if (x.engine_id === v.engine_id) x.is_default = false;
      Object.assign(v, body, { updated_at: new Date().toISOString() });
      return ok(v);
    },
  },
  {
    method: 'DELETE',
    path: '/engines/:engine_id/versions/:version_id',
    handle: (req) => {
      const v = engineVersions.find((x) => x.id === req.params.version_id && x.engine_id === req.params.engine_id);
      if (!v) return notFound('Engine version');
      if (fleetStore.deployments.some((d) => d.engine_version_id === v.id || (d.engine_name === v.engine_name && d.engine_version === v.version))) {
        return fail(409, 'ENGINE_VERSION_IN_USE', 'Deployments run on this version');
      }
      engineVersions.splice(engineVersions.indexOf(v), 1);
      return noContent();
    },
  },

  // ── Deployments ───────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/deployments',
    handle: (req) => {
      const now = Date.now();
      const list = filterDeployments(req).map((d) => deploymentView(d, now));
      return paginate(sortBy(list, req.query.get('sort'), '-created_at'), req.query, 25);
    },
  },
  {
    method: 'POST',
    path: '/deployments/preview',
    handle: (req) => {
      const body = bodyOf<Schemas['PlacementPreviewRequest']>(req);
      if (body.deployment_id) {
        const d = deploymentRecord(body.deployment_id);
        if (!d) return notFound('Deployment');
        return ok(previewPlacement(d, body.replicas, d.id));
      }
      if (!body.deployment) return fail(422, 'VALIDATION_ERROR', 'deployment or deployment_id is required');
      return ok(previewPlacement(body.deployment, body.replicas));
    },
  },
  {
    method: 'POST',
    path: '/deployments',
    handle: (req) => {
      const body = bodyOf<Schemas['DeploymentCreate']>(req);
      if (body.name && fleetStore.deployments.some((d) => d.name === body.name)) return fail(409, 'CONFLICT', `A deployment named "${body.name}" already exists`);
      const { deployment, error } = buildDeployment(body);
      if (error || !deployment) return error ?? fail(422, 'VALIDATION_ERROR', 'Invalid deployment');
      if (body.dry_run) return ok(deploymentView(deployment));
      fleetStore.deployments.unshift(deployment);
      if (deployment.desired_running) {
        const preview = previewPlacement(deployment);
        if (preview.placements.length === 0) {
          deployment.status = 'failed';
          deployment.status_message = preview.rejected?.[0]?.reason ?? 'No device can fit this deployment';
        } else {
          setTimeout(() => {
            deployment.status = 'deploying';
            placeReplicas(deployment, deployment.replicas);
          }, 800);
        }
      }
      return created(deploymentView(deployment));
    },
  },
  { method: 'GET', path: '/deployments/:deployment_id', handle: (req) => { const d = deploymentRecord(req.params.deployment_id); return d ? ok(deploymentView(d)) : notFound('Deployment'); } },
  {
    method: 'PATCH',
    path: '/deployments/:deployment_id',
    handle: (req) => {
      const d = deploymentRecord(req.params.deployment_id);
      if (!d) return notFound('Deployment');
      const body = bodyOf<Schemas['DeploymentUpdate']>(req);
      const rolling = ROLLING_FIELDS.some((k) => k in body && JSON.stringify((body as Record<string, unknown>)[k]) !== JSON.stringify((d as Record<string, unknown>)[k]));
      const replicasChanged = typeof body.replicas === 'number' && body.replicas !== d.replicas;
      Object.assign(d, body, { updated_at: new Date().toISOString() });
      const model = d.model_id ? modelRecord(d.model_id) : undefined;
      d.effective_priority = d.priority_override ?? model?.priority ?? 'normal';
      if (body.engine_version_id) {
        const found = engineOfVersion(body.engine_version_id);
        if (found) {
          d.engine_name = found.engine.name;
          d.engine_version = found.version.version;
        }
      }
      if (rolling && d.desired_running) restartInstances(d, liveInstances(d.id));
      if (replicasChanged && d.desired_running) scaleTo(d, d.replicas);
      return ok(deploymentView(d));
    },
  },
  {
    method: 'DELETE',
    path: '/deployments/:deployment_id',
    handle: (req) => {
      const d = deploymentRecord(req.params.deployment_id);
      if (!d) return notFound('Deployment');
      d.status = 'deleting';
      d.desired_running = false;
      const ids = liveInstances(d.id).map((i) => unloadInstance(i, 2500).id);
      setTimeout(() => {
        removeWhere(fleetStore.deployments, (x) => x.id === d.id);
      }, 3000);
      return accepted({ command_ids: ids, swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/deployments/:deployment_id/scale',
    handle: (req) => {
      const d = deploymentRecord(req.params.deployment_id);
      if (!d) return notFound('Deployment');
      const body = bodyOf<Schemas['ScaleRequest']>(req);
      const replicas = Number(body.replicas);
      if (!Number.isInteger(replicas) || replicas < 0) return fail(422, 'VALIDATION_ERROR', 'replicas must be a non-negative integer');
      if (replicas > d.max_replicas) return fail(422, 'VALIDATION_ERROR', `replicas exceeds max_replicas (${d.max_replicas})`);
      if (body.placement_strategy) d.placement_strategy = body.placement_strategy;
      const ids = scaleTo(d, replicas);
      return accepted({ command_ids: ids, swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/deployments/:deployment_id/restart',
    handle: (req) => {
      const d = deploymentRecord(req.params.deployment_id);
      if (!d) return notFound('Deployment');
      return accepted({ command_ids: restartInstances(d, liveInstances(d.id)), swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/deployments/:deployment_id/stop',
    handle: (req) => {
      const d = deploymentRecord(req.params.deployment_id);
      if (!d) return notFound('Deployment');
      d.desired_running = false;
      const live = liveInstances(d.id);
      const ids = live.map((i) => unloadInstance(i, 3000).id);
      live.forEach((i) => recordSwap(d, 'unload', i));
      d.status = live.length ? 'deploying' : 'stopped';
      setTimeout(() => {
        d.status = 'stopped';
      }, 3200);
      return accepted({ command_ids: ids, swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/deployments/:deployment_id/start',
    handle: (req) => {
      const d = deploymentRecord(req.params.deployment_id);
      if (!d) return notFound('Deployment');
      d.desired_running = true;
      d.status = 'scheduling';
      const missing = Math.max(1, d.replicas) - liveInstances(d.id).length;
      if (d.replicas === 0) d.replicas = 1;
      const launched = placeReplicas(d, missing);
      if (launched.length === 0) {
        d.status = 'failed';
        d.status_message = 'No device can fit this deployment right now';
      }
      return accepted({ command_ids: launched.map((i) => i.id), swap_operation_ids: [] });
    },
  },
  {
    method: 'PUT',
    path: '/deployments/:deployment_id/traffic',
    handle: (req) => {
      const d = deploymentRecord(req.params.deployment_id);
      if (!d) return notFound('Deployment');
      const body = bodyOf<Schemas['TrafficWeightsRequest']>(req);
      for (const [instanceId, weight] of Object.entries(body.weights ?? {})) {
        const inst = fleetStore.instances.find((i) => i.id === instanceId && i.deployment_id === d.id);
        if (!inst) return fail(422, 'VALIDATION_ERROR', `Instance ${instanceId} is not part of this deployment`);
        if (weight < 0 || weight > 1000) return fail(422, 'VALIDATION_ERROR', 'Weights must be between 0 and 1000');
        inst.traffic_weight = weight;
      }
      d.traffic_config = { ...(d.traffic_config ?? {}), ...(body.strategy ? { strategy: body.strategy } : {}), weights: body.weights };
      return ok(deploymentView(d));
    },
  },
  {
    method: 'GET',
    path: '/deployments/:deployment_id/instances',
    handle: (req) => {
      const includeStopped = req.query.get('include_stopped') === 'true';
      const now = Date.now();
      return paginate(fleetStore.instances.filter((i) => i.deployment_id === req.params.deployment_id && (includeStopped || i.state !== 'stopped')).map((i) => instanceView(i, now)), req.query);
    },
  },
  {
    method: 'GET',
    path: '/deployments/:deployment_id/swaps',
    handle: (req) => {
      const list = [...sessionSwaps, ...recentSwaps(Date.now())].filter((s) => s.deployment_id === req.params.deployment_id);
      return paginate(list, req.query, 20);
    },
  },

  // ── Instances ─────────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/instances',
    handle: (req) => {
      const q = req.query;
      const now = Date.now();
      const includeStopped = q.get('include_stopped') === 'true';
      const list = fleetStore.instances
        .filter(
          (i) =>
            (includeStopped || i.state !== 'stopped') &&
            (!q.get('deployment_id') || i.deployment_id === q.get('deployment_id')) &&
            (!q.get('node_id') || i.node_id === q.get('node_id')) &&
            (!q.get('state') || i.state === q.get('state')) &&
            (!q.get('health') || i.health === q.get('health')) &&
            (!q.get('origin') || (i.origin ?? 'managed') === q.get('origin')),
        )
        .map((i) => instanceView(i, now));
      return paginate(list, q);
    },
  },
  { method: 'GET', path: '/instances/:instance_id', handle: (req) => { const i = fleetStore.instances.find((x) => x.id === req.params.instance_id); return i ? ok(instanceView(i)) : notFound('Instance'); } },
  {
    method: 'PATCH',
    path: '/instances/:instance_id',
    handle: (req) => {
      const i = fleetStore.instances.find((x) => x.id === req.params.instance_id);
      if (!i) return notFound('Instance');
      const body = bodyOf<Schemas['InstanceUpdate']>(req);
      if (typeof body.traffic_weight === 'number') i.traffic_weight = body.traffic_weight;
      return ok(instanceView(i));
    },
  },
  {
    method: 'DELETE',
    path: '/instances/:instance_id',
    handle: (req) => {
      const i = fleetStore.instances.find((x) => x.id === req.params.instance_id);
      if (!i) return notFound('Instance');
      const d = deploymentRecord(i.deployment_id);
      if (d) {
        d.replicas = Math.max(0, d.replicas - 1);
        recordSwap(d, 'scale_down', i);
      }
      const cmd = unloadInstance(i, 3000);
      setTimeout(() => d && settleDeployment(d), 3200);
      return accepted({ command_ids: [cmd.id], swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/instances/:instance_id/drain',
    handle: (req) => {
      const i = fleetStore.instances.find((x) => x.id === req.params.instance_id);
      if (!i) return notFound('Instance');
      const body = bodyOf<Schemas['InstanceDrainRequest']>(req);
      i.state = 'draining';
      i.traffic_weight = 0;
      i.state_changed_at = new Date().toISOString();
      const cmd = recordCommand(i.node_id, 'drain_instance', { instanceId: i.id, ms: 3000 });
      setTimeout(() => {
        if (body.then === 'unload') releaseInstance(i);
        else if (body.then === 'restart') {
          i.state = 'loading';
          setTimeout(() => { i.state = 'ready'; i.traffic_weight = 100; i.restart_count = (i.restart_count ?? 0) + 1; }, 2500);
        } else i.state = 'ready';
      }, 3000);
      return accepted({ command_ids: [cmd.id], swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/instances/:instance_id/restart',
    handle: (req) => {
      const i = fleetStore.instances.find((x) => x.id === req.params.instance_id);
      if (!i) return notFound('Instance');
      const d = deploymentRecord(i.deployment_id);
      return accepted({ command_ids: d ? restartInstances(d, [i]) : [], swap_operation_ids: [] });
    },
  },
  {
    method: 'GET',
    path: '/instances/:instance_id/sessions',
    handle: (req) => {
      const i = fleetStore.instances.find((x) => x.id === req.params.instance_id);
      if (!i) return notFound('Instance');
      const key = fleetStore.metricAlias.get(i.id) ?? i.id;
      const snap = sessionsSnapshot(key, Date.now());
      return ok({ ...snap, instance_id: i.id, sessions: snap.sessions.map((s) => ({ ...s, instance_id: i.id })) });
    },
  },

  // ── Model cache ───────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/model-cache',
    handle: (req) => {
      tickDownloads();
      const q = req.query;
      const unusedHours = Number(q.get('unused_for_hours') ?? 0);
      const pinned = q.get('pinned');
      const cutoff = Date.now() - unusedHours * 3_600_000;
      const list = fleetStore.cacheItems.filter(
        (c) =>
          (q.get('state') ? c.state === q.get('state') : c.state !== 'evicted') &&
          (!q.get('node_id') || c.node_id === q.get('node_id')) &&
          (!q.get('model_id') || c.model_id === q.get('model_id')) &&
          (!q.get('model_version_id') || c.model_version_id === q.get('model_version_id')) &&
          (pinned === null || String(c.pinned) === pinned) &&
          (!unusedHours || !c.last_used_at || Date.parse(c.last_used_at) < cutoff),
      );
      return paginate(sortBy(list, q.get('sort'), '-size_bytes'), q, 50);
    },
  },
  {
    method: 'GET',
    path: '/model-cache/summary',
    handle: (req) => {
      tickDownloads();
      const clusterId = req.query.get('cluster_id');
      const list = fleetStore.nodes.filter((n) => n.status !== 'removed' && (!clusterId || n.cluster_id === clusterId)).map((n) => cacheSummary(n.id));
      return ok(list, { total: list.length, page: 1, page_size: list.length, has_more: false });
    },
  },
  {
    method: 'POST',
    path: '/model-cache/prefetch',
    handle: (req) => {
      const body = bodyOf<Schemas['PrefetchRequest']>(req);
      const version = versions.find((v) => v.id === body.model_version_id);
      if (!version || !body.node_ids?.length) return fail(422, 'VALIDATION_ERROR', 'model_version_id and at least one node are required');
      const model = modelRecord(version.model_id);
      const commandIds: string[] = [];
      for (const nodeId of body.node_ids) {
        const node = fleetStore.nodes.find((n) => n.id === nodeId);
        if (!node) continue;
        commandIds.push(recordCommand(nodeId, 'prefetch_model', { ms: 12_000 }).id);
        for (const file of files.filter((f) => f.model_version_id === version.id)) {
          const existing = fleetStore.cacheItems.find((c) => c.node_id === nodeId && c.model_file_id === file.id);
          if (existing && existing.state !== 'evicted') {
            if (body.pin) { existing.pinned = true; existing.pinned_reason = body.pin_reason ?? 'Pinned at prefetch'; }
            continue;
          }
          const now = new Date().toISOString();
          const item: CacheItem = {
            id: newId('cache'),
            node_id: nodeId,
            node_name: node.name,
            model_id: version.model_id,
            model_name: model?.display_name ?? model?.name,
            model_version_id: version.id,
            version: version.version,
            model_file_id: file.id,
            path: file.path,
            local_path: `${node.cache_dir}/${(model?.repo_id ?? 'model').replace(/^\/.*\//, '').replace('/', '--')}/${version.version}/${file.path}`,
            size_bytes: file.size_bytes,
            state: 'downloading',
            progress_pct: 0,
            sha256_verified: false,
            pinned: !!body.pin,
            pinned_reason: body.pin ? body.pin_reason ?? 'Pinned at prefetch' : null,
            in_use: false,
            error_message: null,
            last_used_at: null,
            created_at: now,
            updated_at: now,
          };
          if (existing) Object.assign(existing, item, { id: existing.id });
          else fleetStore.cacheItems.push(item);
        }
      }
      return accepted({ command_ids: commandIds, swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/model-cache/evict',
    handle: (req) => {
      const body = bodyOf<Schemas['EvictRequest']>(req);
      if (!body.node_ids?.length) return fail(422, 'VALIDATION_ERROR', 'node_ids is required');
      let targets = fleetStore.cacheItems.filter((c) => body.node_ids?.includes(c.node_id) && (c.state === 'ready' || c.state === 'error'));
      if (body.cache_item_ids?.length) targets = targets.filter((c) => body.cache_item_ids?.includes(c.id));
      if (body.model_version_id) targets = targets.filter((c) => c.model_version_id === body.model_version_id);
      if (body.unpinned_older_than_seconds) {
        const cutoff = Date.now() - body.unpinned_older_than_seconds * 1000;
        targets = targets.filter((c) => !c.pinned && (!c.last_used_at || Date.parse(c.last_used_at) < cutoff));
      }
      if (body.unpin_only) {
        targets.forEach((c) => { c.pinned = false; c.pinned_reason = null; });
        return accepted({ command_ids: [], swap_operation_ids: [] });
      }
      if (body.target_free_bytes) {
        // Oldest first until enough is freed.
        targets = [...targets].filter((c) => !c.in_use && !c.pinned).sort((a, b) => String(a.last_used_at ?? '').localeCompare(String(b.last_used_at ?? '')));
        let freed = 0;
        targets = targets.filter((c) => {
          if (freed >= (body.target_free_bytes ?? 0)) return false;
          freed += c.size_bytes;
          return true;
        });
      }
      const { evicted } = evictItems(targets, !!body.force);
      const commandIds = [...new Set(evicted.map((c) => c.node_id))].map((n) => recordCommand(n, 'evict_cache', { ms: 2500 }).id);
      return accepted({ command_ids: commandIds, swap_operation_ids: [] });
    },
  },
  {
    method: 'DELETE',
    path: '/model-cache/:item_id',
    handle: (req) => {
      const item = fleetStore.cacheItems.find((c) => c.id === req.params.item_id);
      if (!item) return notFound('Cache item');
      const force = req.query.get('force') === 'true';
      if (item.in_use && !force) return fail(409, 'CACHE_IN_USE', 'A running instance uses this file — stop it first or force eviction');
      evictItems([item], true);
      return accepted({ command_ids: [recordCommand(item.node_id, 'evict_cache', { ms: 2500 }).id], swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/model-cache/:item_id/pin',
    handle: (req) => {
      const item = fleetStore.cacheItems.find((c) => c.id === req.params.item_id);
      if (!item) return notFound('Cache item');
      const body = bodyOf<{ reason?: string | null }>(req);
      item.pinned = true;
      item.pinned_reason = body.reason ?? 'Pinned by admin';
      return ok(item);
    },
  },
  {
    method: 'POST',
    path: '/model-cache/:item_id/unpin',
    handle: (req) => {
      const item = fleetStore.cacheItems.find((c) => c.id === req.params.item_id);
      if (!item) return notFound('Cache item');
      item.pinned = false;
      item.pinned_reason = null;
      return ok(item);
    },
  },

  // ── Benchmarks ────────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/benchmarks',
    handle: (req) => {
      const search = (req.query.get('search') ?? '').toLowerCase();
      const dep = req.query.get('deployment_id');
      const list = benchmarks.filter((b) => (!dep || b.deployment_id === dep) && (!search || [b.name, b.description ?? '', b.deployment_name ?? ''].some((s) => s.toLowerCase().includes(search)))).map(benchmarkView);
      return paginate(sortBy(list, req.query.get('sort'), '-created_at'), req.query, 25);
    },
  },
  {
    method: 'POST',
    path: '/benchmarks',
    handle: (req) => {
      const body = bodyOf<Schemas['BenchmarkCreate']>(req);
      const dep = body.deployment_id ? deploymentRecord(body.deployment_id) : undefined;
      if (!body.name || !dep) return fail(422, 'VALIDATION_ERROR', 'name and an existing deployment are required');
      const now = new Date().toISOString();
      const benchmark: Benchmark = {
        id: newId('benchmark'),
        name: body.name,
        description: body.description ?? null,
        deployment_id: dep.id,
        deployment_name: dep.name,
        concurrency_levels: body.concurrency_levels?.length ? [...body.concurrency_levels].sort((a, b) => a - b) : [1, 2, 4, 8, 16],
        duration_seconds: body.duration_seconds ?? 60,
        workload: body.workload ?? {},
        target_slo: body.target_slo ?? {},
        auto_tune: !!body.auto_tune,
        run_count: 0,
        created_at: now,
        updated_at: now,
      };
      benchmarks.unshift(benchmark);
      return created(benchmarkView(benchmark));
    },
  },
  { method: 'GET', path: '/benchmarks/:benchmark_id', handle: (req) => { const b = benchmarks.find((x) => x.id === req.params.benchmark_id); return b ? ok(benchmarkView(b)) : notFound('Benchmark'); } },
  {
    method: 'PATCH',
    path: '/benchmarks/:benchmark_id',
    handle: (req) => {
      const b = benchmarks.find((x) => x.id === req.params.benchmark_id);
      if (!b) return notFound('Benchmark');
      Object.assign(b, bodyOf<Schemas['BenchmarkUpdate']>(req), { updated_at: new Date().toISOString() });
      return ok(benchmarkView(b));
    },
  },
  {
    method: 'DELETE',
    path: '/benchmarks/:benchmark_id',
    handle: (req) => {
      const idx = benchmarks.findIndex((x) => x.id === req.params.benchmark_id);
      if (idx < 0) return notFound('Benchmark');
      benchmarks.splice(idx, 1);
      return noContent();
    },
  },
  {
    method: 'POST',
    path: '/benchmarks/:benchmark_id/start',
    handle: (req) => {
      const b = benchmarks.find((x) => x.id === req.params.benchmark_id);
      if (!b) return notFound('Benchmark');
      const body = bodyOf<Schemas['BenchmarkStartRequest']>(req);
      const result = startRun(b, body, body.auto_tune ?? b.auto_tune);
      return 'id' in result ? accepted(result) : result;
    },
  },
  {
    method: 'POST',
    path: '/benchmarks/:benchmark_id/auto-tune',
    handle: (req) => {
      const b = benchmarks.find((x) => x.id === req.params.benchmark_id);
      if (!b) return notFound('Benchmark');
      const result = startRun(b, bodyOf<Schemas['BenchmarkStartRequest']>(req), true);
      return 'id' in result ? accepted(result) : result;
    },
  },
  {
    method: 'GET',
    path: '/benchmarks/:benchmark_id/runs',
    handle: (req) => paginate(runs.filter((r) => r.run.benchmark_id === req.params.benchmark_id).sort((a, b) => b.startedMs - a.startedMs).map(runView), req.query),
  },
  { method: 'GET', path: '/benchmark-runs/:run_id', handle: (req) => { const r = runs.find((x) => x.run.id === req.params.run_id); return r ? ok(runView(r)) : notFound('Run'); } },
  {
    method: 'POST',
    path: '/benchmark-runs/:run_id/cancel',
    handle: (req) => {
      const r = runs.find((x) => x.run.id === req.params.run_id);
      if (!r) return notFound('Run');
      const view = runView(r);
      if (view.status !== 'running' && view.status !== 'pending') return fail(409, 'RUN_FINISHED', 'The run already finished');
      Object.assign(r.run, { status: 'cancelled', results: view.results, progress_pct: view.progress_pct, finished_at: new Date().toISOString() });
      return ok(r.run);
    },
  },
  {
    method: 'POST',
    path: '/benchmark-runs/:run_id/apply',
    handle: (req) => {
      const r = runs.find((x) => x.run.id === req.params.run_id);
      if (!r) return notFound('Run');
      const b = benchmarks.find((x) => x.id === r.run.benchmark_id);
      const d = b ? deploymentRecord(b.deployment_id) : undefined;
      if (!d || !b) return notFound('Deployment');
      const rec = (r.run.recommendation ?? {}) as { launch_flags?: Record<string, unknown>; replicas?: number; concurrency?: number };
      if (!rec.concurrency && !rec.replicas) return fail(409, 'NO_RECOMMENDATION', 'This run has no recommendation to apply');
      const body = bodyOf<Schemas['ApplyRecommendationRequest']>(req);
      if (body.apply_launch_flags !== false && rec.launch_flags) {
        d.launch_flags = { ...(d.launch_flags ?? {}), ...rec.launch_flags };
        restartInstances(d, liveInstances(d.id));
      }
      if (body.apply_traffic_config !== false && rec.concurrency) d.traffic_config = { ...(d.traffic_config ?? {}), max_concurrent_per_instance: rec.concurrency };
      if (body.apply_replicas && rec.replicas && rec.replicas !== d.replicas) scaleTo(d, Math.min(rec.replicas, Math.max(d.max_replicas, rec.replicas)));
      d.updated_at = new Date().toISOString();
      return ok(deploymentView(d));
    },
  },
  {
    method: 'GET',
    path: '/benchmark-runs/:run_id/export',
    handle: (req) => {
      const r = runs.find((x) => x.run.id === req.params.run_id);
      if (!r) return notFound('Run');
      return { raw: runsCsv(runView(r)), headers: { 'content-type': 'text/csv; charset=utf-8', 'content-disposition': `attachment; filename="benchmark-run-${r.run.id.slice(0, 8)}.csv"` } };
    },
  },
]));

export { MODEL_IDS };
