"""
Model detection helpers shared by the catalog sources.

Pure functions: weight format from a file name, model family from a name or
GGUF architecture, model type from HF pipeline tags / names / architectures,
quantization labels, and the default tool parser per (family, engine).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")
WEIGHT_SUFFIXES = (".gguf", ".safetensors") + PICKLE_SUFFIXES
AUX_REQUIRED_NAMES = (
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer.model",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.json",
    "merges.txt",
    "chat_template.json",
    "chat_template.jinja",
    "preprocessor_config.json",
    "processor_config.json",
    "model.safetensors.index.json",
    "pytorch_model.bin.index.json",
)
IGNORED_NAMES = (".gitattributes", "README.md", "LICENSE", "NOTICE", ".DS_Store")

_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.(gguf|safetensors)$")

# Ordered: the first matching pattern wins.
_FAMILY_PATTERNS = [
    ("qwen3", r"qwen3|qwen-3"),
    ("qwen2", r"qwen2|qwen-2|qwq"),
    ("qwen", r"qwen"),
    ("llama4", r"llama-?4|llama4"),
    ("llama3", r"llama-?3|llama3"),
    ("llama", r"llama|tinyllama|vicuna|alpaca"),
    ("gemma3", r"gemma-?3|gemma3"),
    ("gemma", r"gemma"),
    ("mistral", r"mistral|mixtral|codestral|devstral|magistral|ministral"),
    ("deepseek", r"deepseek"),
    ("phi", r"phi-?\d|phi"),
    ("glm", r"glm|chatglm"),
    ("kimi", r"kimi"),
    ("granite", r"granite"),
    ("internlm", r"internlm"),
    ("hermes", r"hermes"),
    ("gpt_oss", r"gpt-oss|gpt_oss"),
    ("nomic", r"nomic"),
    ("bge", r"bge"),
    ("e5", r"\be5\b|e5-"),
    ("whisper", r"whisper"),
    ("kokoro", r"kokoro"),
    ("cosyvoice", r"cosyvoice"),
    ("stable_diffusion", r"stable-?diffusion|sdxl|sd-?\d|sd3"),
    ("flux", r"flux"),
    ("wan", r"\bwan\d|wan2"),
    ("hunyuan", r"hunyuan"),
]

# GGUF general.architecture -> family
_ARCH_FAMILY = {
    "qwen3": "qwen3",
    "qwen3moe": "qwen3",
    "qwen2": "qwen2",
    "qwen2moe": "qwen2",
    "qwen2vl": "qwen2",
    "llama": "llama",
    "llama4": "llama4",
    "gemma3": "gemma3",
    "gemma3n": "gemma3",
    "gemma2": "gemma",
    "gemma": "gemma",
    "mistral3": "mistral",
    "deepseek2": "deepseek",
    "deepseek": "deepseek",
    "phi3": "phi",
    "phi2": "phi",
    "glm4": "glm",
    "chatglm": "glm",
    "granite": "granite",
    "internlm2": "internlm",
    "nomic-bert": "nomic",
    "nomic-bert-moe": "nomic",
    "bert": "bge",
    "jina-bert-v2": "jina",
    "gpt-oss": "gpt_oss",
}

_EMBEDDING_ARCHS = {
    "bert",
    "nomic-bert",
    "nomic-bert-moe",
    "jina-bert-v2",
    "xlm-roberta",
}

_PIPELINE_TYPE = {
    "text-generation": "chat",
    "text2text-generation": "chat",
    "conversational": "chat",
    "image-text-to-text": "vision",
    "image-to-text": "vision",
    "visual-question-answering": "vision",
    "feature-extraction": "embedding",
    "sentence-similarity": "embedding",
    "text-ranking": "rerank",
    "automatic-speech-recognition": "stt",
    "text-to-speech": "tts",
    "text-to-audio": "tts",
    "text-to-image": "image",
    "image-to-image": "image",
    "text-to-video": "video",
    "image-to-video": "video",
}

_QUANT_RE = re.compile(
    r"(?i)(?<![a-z0-9])(IQ[1-4]_[A-Z]+|Q[2-8]_K_[SML]|Q[2-8]_K|Q[2-8]_[01]|Q[2-8]|"
    r"F16|BF16|F32|FP8|FP16|INT4|INT8|AWQ|GPTQ|NF4|4BIT|8BIT|3BIT|6BIT|MXFP4)(?![a-z0-9])"
)

# Default tool-call parser per engine and family. None = engine has no parser flag.
_TOOL_PARSERS: Dict[str, Dict[str, Optional[str]]] = {
    "llama_cpp": {"*": "jinja"},
    "vllm": {
        "qwen3": "hermes",
        "qwen2": "hermes",
        "qwen": "hermes",
        "hermes": "hermes",
        "llama4": "llama4_pythonic",
        "llama3": "llama3_json",
        "llama": "llama3_json",
        "mistral": "mistral",
        "gemma3": "pythonic",
        "gemma": "pythonic",
        "deepseek": "deepseek_v3",
        "phi": "phi4_mini_json",
        "glm": "glm45",
        "kimi": "kimi_k2",
        "granite": "granite",
        "internlm": "internlm",
        "gpt_oss": "openai",
        "*": "hermes",
    },
    "sglang": {
        "qwen3": "qwen25",
        "qwen2": "qwen25",
        "qwen": "qwen25",
        "llama4": "pythonic",
        "llama3": "llama3",
        "llama": "llama3",
        "mistral": "mistral",
        "gemma3": "pythonic",
        "gemma": "pythonic",
        "deepseek": "deepseekv3",
        "glm": "glm45",
        "kimi": "kimi_k2",
        "gpt_oss": "gpt-oss",
        "*": "qwen25",
    },
    "tensorrt_llm": {
        "qwen3": "qwen3",
        "qwen2": "hermes",
        "qwen": "hermes",
        "llama3": "llama3",
        "llama": "llama3",
        "mistral": "mistral",
        "deepseek": "deepseek_v3",
        "*": "hermes",
    },
    "mlx": {"*": None},
    "custom": {"*": None},
}


def detect_format(path: str) -> str:
    """``gguf`` / ``safetensors`` / ``pickle`` / ``other`` from a file name."""
    lower = path.lower()
    if lower.endswith(".gguf"):
        return "gguf"
    if lower.endswith(".safetensors"):
        return "safetensors"
    if lower.endswith(PICKLE_SUFFIXES):
        return "pickle"
    return "other"


def is_weight_file(path: str) -> bool:
    return detect_format(path) in ("gguf", "safetensors", "pickle")


def is_required_file(path: str, fmt: str) -> bool:
    """Weights plus the tokenizer/config side-cars an engine needs."""
    name = PurePosixPath(path).name
    if name in IGNORED_NAMES:
        return False
    if fmt in ("gguf", "safetensors", "pickle"):
        return True
    if name in AUX_REQUIRED_NAMES:
        return True
    return name.endswith(
        (".json", ".jinja", ".txt", ".model", ".tiktoken")
    ) and not name.startswith("README")


def is_mlx_config(config: Optional[Dict[str, Any]]) -> bool:
    """mlx-lm converted models carry a top-level ``quantization`` block."""
    if not config:
        return False
    quant = config.get("quantization") or config.get("quantization_config")
    if isinstance(quant, dict) and ("group_size" in quant or quant.get("quant_method") == "mlx"):
        return True
    return False


def version_format(file_formats: Iterable[str], is_mlx: bool = False) -> str:
    """Format of a version from its files (mlx wins when the config says so)."""
    formats = set(file_formats)
    if is_mlx and "safetensors" in formats:
        return "mlx"
    for fmt in ("gguf", "safetensors", "mlx", "pickle"):
        if fmt in formats:
            return fmt
    return "other"


def has_pickle_only_weights(file_formats: Iterable[str]) -> bool:
    formats = set(file_formats)
    return "pickle" in formats and not ({"gguf", "safetensors", "mlx"} & formats)


def shard_info(path: str) -> tuple[Optional[int], Optional[int]]:
    """``(index, count)`` for ``x-00001-of-00004.gguf`` style shards."""
    m = _SHARD_RE.search(path)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def detect_family(*hints: Optional[str], architecture: Optional[str] = None) -> Optional[str]:
    """Family from GGUF architecture first, then name/repo hints."""
    if architecture:
        fam = _ARCH_FAMILY.get(architecture.lower())
        if fam:
            return fam
    for hint in hints:
        if not hint:
            continue
        lower = hint.lower()
        for family, pattern in _FAMILY_PATTERNS:
            if re.search(pattern, lower):
                return family
    return None


def detect_type(
    *,
    pipeline_tag: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    name: Optional[str] = None,
    architecture: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Best-effort model type; None when nothing matches."""
    tags = [t.lower() for t in (tags or [])]
    lower_name = (name or "").lower()
    if architecture and architecture.lower() in _EMBEDDING_ARCHS:
        return "rerank" if "rerank" in lower_name else "embedding"
    if architecture and "whisper" in architecture.lower():
        return "stt"
    if pipeline_tag and pipeline_tag in _PIPELINE_TYPE:
        base = _PIPELINE_TYPE[pipeline_tag]
        if base == "chat" and _looks_thinking(lower_name, tags):
            return "thinking"
        return base
    if config:
        archs = [str(a).lower() for a in (config.get("architectures") or [])]
        joined = " ".join(archs)
        if "forconditionalgeneration" in joined and ("vl" in joined or "vision" in joined):
            return "vision"
        if "whisper" in joined:
            return "stt"
        if "forsequenceclassification" in joined:
            return "rerank"
        if archs and all(a.endswith("model") and "causallm" not in a for a in archs):
            return "embedding"
        if "causallm" in joined or "forconditionalgeneration" in joined:
            return "thinking" if _looks_thinking(lower_name, tags) else "chat"
    if "rerank" in lower_name:
        return "rerank"
    if "embed" in lower_name or "bge-" in lower_name or "e5-" in lower_name:
        return "embedding"
    if "whisper" in lower_name or "parakeet" in lower_name:
        return "stt"
    if "tts" in lower_name or "kokoro" in lower_name or "cosyvoice" in lower_name:
        return "tts"
    if any(k in lower_name for k in ("stable-diffusion", "sdxl", "flux", "sd3")):
        return "image"
    if any(k in lower_name for k in ("wan2", "hunyuanvideo", "text-to-video", "t2v")):
        return "video"
    if "-vl" in lower_name or "vision" in lower_name or "llava" in lower_name:
        return "vision"
    if _looks_thinking(lower_name, tags):
        return "thinking"
    if architecture or "instruct" in lower_name or "chat" in lower_name or "-it" in lower_name:
        return "chat"
    return None


def _looks_thinking(lower_name: str, tags: List[str]) -> bool:
    return any(k in lower_name for k in ("-r1", "qwq", "thinking", "reason")) or "reasoning" in tags


def detect_quantization(*hints: Optional[str]) -> Optional[str]:
    for hint in hints:
        if not hint:
            continue
        m = _QUANT_RE.search(hint)
        if m:
            return m.group(1).upper()
    return None


def default_tool_parser(family: Optional[str], engine_name: str) -> Optional[str]:
    table = _TOOL_PARSERS.get(engine_name)
    if table is None:
        return None
    if family and family in table:
        return table[family]
    return table.get("*")


def suggested_name(repo_id: str) -> str:
    """Serving-name suggestion from a repo id or path (``^[A-Za-z0-9._:-]+$``)."""
    leaf = repo_id.rstrip("/").split("/")[-1]
    leaf = re.sub(r"\.gguf$|\.safetensors$", "", leaf, flags=re.I)
    cleaned = re.sub(r"[^A-Za-z0-9._:-]+", "-", leaf).strip("-.")
    return cleaned.lower() or "model"


def context_from_config(config: Optional[Dict[str, Any]]) -> Optional[int]:
    if not config:
        return None
    for key in ("max_position_embeddings", "n_positions", "seq_length", "max_seq_len"):
        value = config.get(key)
        if isinstance(value, int) and value > 0:
            return value
    text_cfg = config.get("text_config")
    if isinstance(text_cfg, dict):
        return context_from_config(text_cfg)
    return None


def shape_from_hf_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a transformers config.json into the KV-estimate shape dict."""
    src = config.get("text_config") if isinstance(config.get("text_config"), dict) else config
    n_layers = src.get("num_hidden_layers") or src.get("n_layer") or src.get("num_layers")
    n_heads = src.get("num_attention_heads") or src.get("n_head")
    n_kv = src.get("num_key_value_heads") or n_heads
    hidden = src.get("hidden_size") or src.get("n_embd")
    head_dim = src.get("head_dim")
    if head_dim is None and hidden and n_heads:
        head_dim = int(hidden) // int(n_heads)
    return {
        "architecture": (config.get("architectures") or [None])[0],
        "model_type": config.get("model_type"),
        "n_layers": n_layers,
        "n_heads": n_heads,
        "n_kv_heads": n_kv,
        "head_dim": head_dim,
        "hidden_size": hidden,
        "max_position_embeddings": context_from_config(config),
        "vocab_size": src.get("vocab_size"),
        "torch_dtype": config.get("torch_dtype"),
        "quantization": (config.get("quantization") or config.get("quantization_config")),
        "num_experts": src.get("num_experts") or src.get("num_local_experts"),
    }
