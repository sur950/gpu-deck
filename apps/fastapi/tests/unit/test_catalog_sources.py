"""
Catalog sources: Hugging Face and ModelScope clients against recorded API
responses (captured 2026-11-17 from the public APIs, trimmed) and the local
scanner against this Mac's real Ollama and
LM Studio directories (read only; skipped where absent).
"""

import json
import os
from pathlib import Path

import httpx
import pytest
from app.core.exceptions import ExternalServiceError, ResourceNotFoundError
from app.services.catalog import detect
from app.services.catalog.hf_client import HuggingFaceClient
from app.services.catalog.local_scanner import scan_path
from app.services.catalog.modelscope_client import ModelScopeClient

RECORDED = json.loads(
    '{"hf_search":[{"id":"Qwen/Qwen3-0.6B-GGUF","modelId":"Qwen/Qwen3-0.6B-GGUF","likes":113,"p'
    'rivate":false,"downloads":213632,"tags":["gguf","text-generation","base_model:Qwen/Qwen3-0'
    '.6B","base_model:quantized:Qwen/Qwen3-0.6B","license:apache-2.0","endpoints_compatible","r'
    'egion:us","conversational"],"pipeline_tag":"text-generation"},{"id":"Qwen/Qwen3-Embedding-'
    '0.6B-GGUF","modelId":"Qwen/Qwen3-Embedding-0.6B-GGUF","likes":594,"private":false,"downloa'
    'ds":173053,"tags":["gguf","arxiv:2506.05176","base_model:Qwen/Qwen3-0.6B-Base","base_model'
    ':quantized:Qwen/Qwen3-0.6B-Base","license:apache-2.0","endpoints_compatible","region:us","'
    'feature-extraction"]}],"hf_info":{"id":"Qwen/Qwen3-0.6B-GGUF","sha":"23749fefcc72300e3a2ad'
    '315e1317431b06b590a","pipeline_tag":"text-generation","tags":["gguf","text-generation","ba'
    'se_model:Qwen/Qwen3-0.6B","base_model:quantized:Qwen/Qwen3-0.6B","license:apache-2.0","end'
    'points_compatible","region:us","conversational"],"gated":false,"private":false,"config":{}'
    '},"hf_tree":[{"type":"file","oid":"e1d1e4f56dfb09e2f8179b74589e7533dff34072","size":1808,"'
    'path":".gitattributes"},{"type":"file","oid":"6f9739bb281a3d2ade8fb5f6dd6dde597a739c80","s'
    'ize":11544,"path":"LICENSE"},{"type":"file","oid":"11bf194c8059f3b03e4454376ac67813a1e0eb5'
    '2","size":639446688,"lfs":{"oid":"9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d'
    '043bb031","size":639446688,"pointerSize":134},"path":"Qwen3-0.6B-Q8_0.gguf"},{"type":"file'
    '","oid":"249fd4077b71e5870ffd09f9627982065925ceef","size":6236,"path":"README.md"},{"type"'
    ':"file","oid":"b79a3bdde8373ca566bd404904abbd5270d7825d","size":270,"path":"params"}],"ms_'
    'search":{"Code":200,"Data":{"Model":{"Models":[{"Path":"Qwen","Name":"Qwen3-Embedding-0.6B'
    '-GGUF","Tags":["gguf"],"Downloads":48289,"Stars":56,"LastUpdatedTime":1752602163,"Visibili'
    'ty":5,"Libraries":["gguf","pytorch"],"Tasks":[{"Name":"sentence-embedding"}]},{"Path":"ggm'
    'l-org","Name":"Qwen3-ASR-0.6B-GGUF","Tags":["gguf"],"Downloads":6392,"Stars":8,"LastUpdate'
    'dTime":1776057279,"Visibility":5,"Libraries":["gguf","pytorch"],"Tasks":[{"Name":""}]}],"T'
    'otalCount":2}}},"ms_info":{"Code":200,"Data":{"Path":"Qwen","Name":"Qwen3-0.6B-GGUF","Task'
    's":[{"Name":"text-generation"}],"Tags":["gguf"],"Downloads":113988,"Visibility":5,"Revisio'
    'n":"master"}},"ms_files":{"Code":200,"Data":{"Files":[{"Name":".gitattributes","Path":".gi'
    'tattributes","Type":"blob","Size":2133,"Sha256":"363242e25b4faa706b5f82edfcf4ca08d8fab6226'
    'f6531cd979ae63ec0ceebf5","IsLFS":false,"Revision":"6abe20cd0aed577f4d0b267935868ecae190aee'
    '9"},{"Name":"LICENSE","Path":"LICENSE","Type":"blob","Size":11544,"Sha256":"5de36594c10839'
    '788a8c589443a8ef9d8b8d17c65a1b5807206ae037fc36c6bd","IsLFS":false,"Revision":"09b810292b01'
    '8ad3e58d7d9e16482f07bc5a0b54"},{"Name":"Qwen3-0.6B-Q8_0.gguf","Path":"Qwen3-0.6B-Q8_0.gguf'
    '","Type":"blob","Size":639446688,"Sha256":"9465e63a22add5354d9bb4b99e90117043c712400766490'
    '7259bd16d043bb031","IsLFS":true,"Revision":"6abe20cd0aed577f4d0b267935868ecae190aee9"},{"N'
    'ame":"README.md","Path":"README.md","Type":"blob","Size":6236,"Sha256":"44adabae2153c3a7bb'
    '741b86fbffd48736bc95118831935fdfc4d3cc1854f2f5","IsLFS":false,"Revision":"32d6327dd2a5b42f'
    '7ce0fe5e6b6f25346b0ee8f9"},{"Name":"configuration.json","Path":"configuration.json","Type"'
    ':"blob","Size":48,"Sha256":"a78221ae93dd8977b26fcd1d106fa5ad7e7f1fd3a7d93bfd9d6fba5ed00ec4'
    'd2","IsLFS":false,"Revision":"a8b94ffce65863252f2247fbbb4ddaadfada59d2"},{"Name":"params",'
    '"Path":"params","Type":"blob","Size":270,"Sha256":"52610851456960402dc4ce4966dbea0eb83bb1d'
    '72c3eeeaefaafe09b4690ad9c","IsLFS":false,"Revision":"32d6327dd2a5b42f7ce0fe5e6b6f25346b0ee'
    '8f9"}]}}}'
)

QWEN_GGUF_SHA = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
OLLAMA = Path(os.path.expanduser("~/.ollama/models"))
LMSTUDIO = Path(os.path.expanduser("~/.lmstudio/models"))


def hf_transport(seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/api/models":
            return httpx.Response(200, json=RECORDED["hf_search"])
        if path == "/api/models/Qwen/Qwen3-0.6B-GGUF/revision/main":
            return httpx.Response(200, json=RECORDED["hf_info"])
        if path == "/api/models/Qwen/Qwen3-0.6B-GGUF/tree/main":
            return httpx.Response(200, json=RECORDED["hf_tree"])
        if path.startswith("/api/models/acme/pickle-only"):
            if "/tree/" in path:
                return httpx.Response(
                    200,
                    json=[
                        {"type": "file", "path": "config.json", "size": 600},
                        {
                            "type": "file",
                            "path": "pytorch_model.bin",
                            "size": 2000,
                            "lfs": {"oid": "c" * 64, "size": 2000},
                        },
                    ],
                )
            return httpx.Response(
                200,
                json={
                    "sha": "abc",
                    "pipeline_tag": "text-generation",
                    "tags": [],
                    "config": {},
                },
            )
        if path == "/acme/pickle-only/resolve/main/config.json":
            return httpx.Response(
                200,
                json={
                    "model_type": "llama",
                    "num_hidden_layers": 16,
                    "num_attention_heads": 32,
                    "num_key_value_heads": 8,
                    "hidden_size": 2048,
                    "max_position_embeddings": 131072,
                },
            )
        if path.startswith("/api/models/gated"):
            return httpx.Response(403, json={"error": "gated"})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def ms_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/dolphin/models" and request.method == "PUT":
            body = json.loads(request.content)
            assert body["Name"] == "Qwen3-0.6B-GGUF"
            return httpx.Response(200, json=RECORDED["ms_search"])
        if path == "/api/v1/models/Qwen/Qwen3-0.6B-GGUF":
            return httpx.Response(200, json=RECORDED["ms_info"])
        if path == "/api/v1/models/Qwen/Qwen3-0.6B-GGUF/repo/files":
            return httpx.Response(200, json=RECORDED["ms_files"])
        return httpx.Response(200, json={"Code": 10010205001, "Message": "model not exists"})

    return httpx.MockTransport(handler)


# ------------------------------------------------------------ Hugging Face


async def test_hf_search_recorded():
    seen = []
    client = HuggingFaceClient("https://huggingface.co", token="hf_x", transport=hf_transport(seen))
    rows = await client.search("Qwen3-0.6B-GGUF", author="Qwen", limit=2)
    assert [r["repo_id"] for r in rows] == [
        "Qwen/Qwen3-0.6B-GGUF",
        "Qwen/Qwen3-Embedding-0.6B-GGUF",
    ]
    assert rows[0]["formats"] == ["gguf"] and rows[0]["detected_type"] in (
        "chat",
        "thinking",
    )
    assert rows[1]["detected_type"] == "embedding"
    assert seen[0].headers["authorization"] == "Bearer hf_x"
    assert seen[0].url.params["sort"] == "downloads"
    only_emb = await client.search("Qwen3-0.6B-GGUF", model_type="embedding")
    assert [r["repo_id"] for r in only_emb] == ["Qwen/Qwen3-Embedding-0.6B-GGUF"]


async def test_hf_repo_files_hashes_and_suggestions():
    client = HuggingFaceClient("https://huggingface.co", transport=hf_transport())
    repo = await client.repo("Qwen/Qwen3-0.6B-GGUF")
    assert repo.revision == RECORDED["hf_info"]["sha"]
    gguf = next(f for f in repo.files if f.path.endswith(".gguf"))
    assert gguf.sha256 == QWEN_GGUF_SHA and gguf.size_bytes == 639446688 and gguf.lfs
    assert gguf.format == "gguf" and gguf.is_required
    readme = next(f for f in repo.files if f.path == "README.md")
    assert readme.format == "other" and not readme.is_required
    assert repo.formats == ["gguf"] and not repo.has_pickle_only_weights
    data = repo.to_dict()
    assert data["suggested"]["family"] == "qwen3"
    assert data["suggested"]["name"]


async def test_hf_pickle_only_and_raw_config_fetch():
    client = HuggingFaceClient("https://huggingface.co", transport=hf_transport())
    repo = await client.repo("acme/pickle-only")
    assert repo.has_pickle_only_weights
    assert repo.config["num_hidden_layers"] == 16
    shape = detect.shape_from_hf_config(repo.config)
    assert shape["n_layers"] == 16 and shape["n_kv_heads"] == 8 and shape["head_dim"] == 64


async def test_hf_errors():
    client = HuggingFaceClient("https://huggingface.co", transport=hf_transport())
    with pytest.raises(ResourceNotFoundError):
        await client.repo("nobody/nothing")
    with pytest.raises(ExternalServiceError):
        await client.repo("gated/model")


# ------------------------------------------------------------- ModelScope


async def test_modelscope_search_and_repo_recorded():
    client = ModelScopeClient("https://www.modelscope.cn", transport=ms_transport())
    rows = await client.search("Qwen3-0.6B-GGUF", limit=2)
    assert rows[0]["repo_id"] == "Qwen/Qwen3-Embedding-0.6B-GGUF"
    assert rows[0]["detected_type"] == "embedding" and "gguf" in rows[0]["formats"]
    repo = await client.repo("Qwen/Qwen3-0.6B-GGUF")
    gguf = next(f for f in repo.files if f.path.endswith(".gguf"))
    assert gguf.sha256 == QWEN_GGUF_SHA and gguf.size_bytes == 639446688
    assert repo.formats == ["gguf"]
    with pytest.raises(ResourceNotFoundError):
        await client.repo("nobody/nothing")


# ------------------------------------------------------------- local paths


@pytest.mark.skipif(not OLLAMA.exists(), reason="no ~/.ollama/models on this machine")
def test_scan_real_ollama_models():
    cands = {c.suggested.get("name"): c for c in scan_path(str(OLLAMA))}
    assert "qwen3:0.6b" in cands and "tinyllama:latest" in cands
    qwen = cands["qwen3:0.6b"]
    assert qwen.kind == "ollama_blob" and qwen.format == "gguf"
    blob = qwen.files[0]
    assert (
        "blobs/sha256-" in qwen.path
        or "sha256-" in blob.path
        or "sha256" in json.dumps(qwen.to_dict())
    )
    assert blob.sha256 and len(blob.sha256) == 64  # digest from the manifest, no hashing needed
    assert qwen.total_size_bytes > 400_000_000
    assert qwen.suggested.get("family") == "qwen3"
    assert cands["tinyllama:latest"].suggested.get("type") == "chat"


@pytest.mark.skipif(not LMSTUDIO.exists(), reason="no ~/.lmstudio/models on this machine")
def test_scan_real_lmstudio_models():
    cands = scan_path(str(LMSTUDIO))
    by_kind = {(c.format, c.suggested.get("name")): c for c in cands}
    mlx = next(c for c in cands if c.format == "mlx")
    assert "gemma-3-1b" in mlx.path and mlx.kind == "lmstudio"
    assert any(f.path.endswith("model.safetensors") for f in mlx.files)
    emb = next(c for c in cands if c.format == "gguf" and "nomic" in c.path)
    assert emb.suggested.get("type") == "embedding"
    assert len(by_kind) >= 2


@pytest.mark.skipif(not OLLAMA.exists(), reason="no ~/.ollama/models on this machine")
def test_scan_ollama_sub_path_only_returns_that_model():
    sub = OLLAMA / "manifests/registry.ollama.ai/library/qwen3"
    if not sub.exists():
        pytest.skip("qwen3 not pulled")
    assert [c.suggested.get("name") for c in scan_path(str(sub))] == ["qwen3:0.6b"]


def test_detect_helpers():
    assert detect.detect_format("model-00001-of-00002.safetensors") == "safetensors"
    assert detect.detect_format("pytorch_model.bin") == "pickle"
    assert detect.detect_format("x.gguf") == "gguf"
    assert detect.has_pickle_only_weights(["pickle", "other"])
    assert not detect.has_pickle_only_weights(["pickle", "safetensors"])
    assert detect.shard_info("model-00002-of-00005.safetensors") == (2, 5)
    assert detect.detect_family("Qwen/Qwen3-0.6B") == "qwen3"
    assert detect.detect_type(name="nomic-embed-text-v1.5") == "embedding"
    assert detect.detect_quantization("Qwen3-0.6B-Q8_0.gguf") == "Q8_0"
    assert detect.default_tool_parser("qwen3", "vllm") == "hermes"
    assert detect.suggested_name("Qwen/Qwen3-0.6B-GGUF")
