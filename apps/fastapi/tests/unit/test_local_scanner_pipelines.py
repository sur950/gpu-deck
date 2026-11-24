"""
Local scanner parity with the node agent's scanner: diffusers pipelines are one candidate,
safetensors suggestions (FP8 from the name and ``quantization_config``, context from
``text_config``).
"""

import json
from pathlib import Path

from app.core.logger import get_logger
from app.services.catalog.local_scanner import (
    complete_suggestions,
    pipeline_type,
    quant_from_config,
    scan_path,
)

logger = get_logger(__name__)


def _write(p: Path, body) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body if isinstance(body, bytes) else json.dumps(body).encode())


def test_diffusers_pipeline_is_one_candidate(tmp_path: Path):
    snap = tmp_path / "hub/models--Qwen--Qwen-Image-Edit-2511/snapshots/r1"
    blobs = tmp_path / "hub/models--Qwen--Qwen-Image-Edit-2511/blobs"
    files = {
        "model_index.json": {
            "_class_name": "QwenImageEditPlusPipeline",
            "transformer": ["diffusers", "QwenImageTransformer2DModel"],
            "vae": ["diffusers", "AutoencoderKLQwenImage"],
        },
        "transformer/config.json": {},
        "transformer/diffusion_pytorch_model-00001-of-00002.safetensors": b"t1",
        "transformer/diffusion_pytorch_model-00002-of-00002.safetensors": b"t2",
        "transformer/diffusion_pytorch_model.bin": b"pk",
        "vae/config.json": {},
        "vae/diffusion_pytorch_model.safetensors": b"v",
        "text_encoder/config.json": {"architectures": ["Qwen2_5_VLForConditionalGeneration"]},
        "text_encoder/model.safetensors": b"te",
        "README.md": b"x",
    }
    for i, (rel, body) in enumerate(files.items()):
        blob = blobs / f"b{i}"
        _write(blob, body)
        link = snap / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(blob)
    cands = scan_path(str(tmp_path / "hub"))
    assert len(cands) == 1, [c.path for c in cands]
    c = cands[0]
    assert c.kind == "hf_cache" and c.path == str(snap)
    assert c.format == "safetensors"
    assert c.suggested["type"] == "image"
    assert c.suggested["pipeline_class"] == "QwenImageEditPlusPipeline"
    paths = {f.path for f in c.files}
    assert "transformer/diffusion_pytorch_model-00001-of-00002.safetensors" in paths
    assert "vae/diffusion_pytorch_model.safetensors" in paths
    assert "model_index.json" in paths and "README.md" not in paths
    pk = next(f for f in c.files if f.path.endswith(".bin"))
    assert pk.is_required is False


def test_safetensors_fp8_suggestions(tmp_path: Path):
    snap = tmp_path / "hub/models--Qwen--Qwen3.8-27B-FP8/snapshots/abc123"
    _write(
        snap / "config.json",
        {
            "architectures": ["Qwen3_5ForConditionalGeneration"],
            "model_type": "qwen3_5",
            "text_config": {"max_position_embeddings": 262144},
            "quantization_config": {"quant_method": "fp8"},
        },
    )
    _write(snap / "model-00001-of-00002.safetensors", b"a")
    _write(snap / "model-00002-of-00002.safetensors", b"b")
    (c,) = scan_path(str(tmp_path / "hub"))
    assert c.suggested["quantization"] == "FP8"
    assert c.suggested["context_size"] == 262144
    assert c.suggested["family"] == "qwen3"
    assert c.suggested["type"] == "chat"
    # FP8 from the config alone.
    plain = tmp_path / "m/ckpt"
    _write(
        plain / "config.json",
        {
            "architectures": ["LlamaForCausalLM"],
            "quantization_config": {
                "quant_method": "compressed-tensors",
                "config_groups": {"g": {"weights": {"num_bits": 8, "type": "float"}}},
            },
        },
    )
    _write(plain / "model.safetensors", b"x")
    (c,) = scan_path(str(tmp_path / "m"))
    assert c.suggested["quantization"] == "FP8"


def test_quant_and_pipeline_helpers():
    assert quant_from_config({"quantization_config": {"quant_method": "awq", "bits": 4}}) == "AWQ"
    assert quant_from_config({"quantization": {"bits": 4, "group_size": 64}}) == "4bit"
    assert quant_from_config({"torch_dtype": "bfloat16"}) is None
    assert pipeline_type("WanPipeline", "Wan2.2-T2V") == "video"
    assert pipeline_type("FluxPipeline", "FLUX.1-dev") == "image"


def test_agent_candidates_with_null_suggestions_are_completed():
    from app.services.catalog.service import _candidate_from_agent

    path = "/opt/models/hub/models--Qwen--Qwen3.8-27B-FP8/snapshots/abc123"
    raw = {
        "path": path,
        "kind": "hf_cache",
        "format": "safetensors",
        "files": [
            {
                "path": "model.safetensors",
                "absolute_path": f"{path}/model.safetensors",
                "size_bytes": 1,
                "format": "safetensors",
            }
        ],
        "total_size_bytes": 1,
        # An older agent: family / type / quantization / context left null.
        "suggested": {
            "name": "qwen3.8-27b-fp8",
            "family": None,
            "type": None,
            "quantization": None,
            "context_size": None,
            "architecture": "qwen3_5",
            "repo_id": "Qwen/Qwen3.8-27B-FP8",
        },
        "metadata": {
            "architectures": ["Qwen3_5ForConditionalGeneration"],
            "model_type": "qwen3_5",
            "text_config": {"max_position_embeddings": 262144},
            "quantization_config": {"quant_method": "fp8"},
        },
    }
    c = _candidate_from_agent(raw)
    assert c.suggested["family"] == "qwen3"
    assert c.suggested["type"] == "chat"
    assert c.suggested["quantization"] == "FP8"
    assert c.suggested["context_size"] == 262144
    assert c.config["quantization"] == "FP8" and c.config["context_size"] == 262144


def test_complete_suggestions_keeps_agent_values_and_reads_gguf_keys():
    out = complete_suggestions(
        {"name": "tiny", "family": "llama", "type": None, "quantization": None},
        "/m/tiny.gguf",
        {
            "general.architecture": "qwen3",
            "qwen3.context_length": 40960,
            "general.name": "Qwen3 0.6B",
        },
    )
    assert out["family"] == "llama", "agent value kept"
    assert out["type"] == "chat" and out["context_size"] == 40960
    assert out.get("quantization") is None
