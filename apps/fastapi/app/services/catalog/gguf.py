"""
Minimal GGUF header reader.

Reads only the key/value metadata and tensor-info section of a GGUF file (v2
and v3), never the tensor data, so inspecting a multi-GB file costs a few KB
of I/O. Large metadata arrays (tokenizer vocab) are skipped by seeking.

Returned ``GgufInfo`` carries what the catalog needs: architecture, name,
quantization label (``general.file_type``), layer / head shape for KV-cache
estimates, context length and the parameter count summed from tensor dims.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional

from app.core.logger import get_logger

logger = get_logger(__name__)

GGUF_MAGIC = b"GGUF"

_T_UINT8, _T_INT8, _T_UINT16, _T_INT16, _T_UINT32, _T_INT32 = 0, 1, 2, 3, 4, 5
_T_FLOAT32, _T_BOOL, _T_STRING, _T_ARRAY, _T_UINT64, _T_INT64, _T_FLOAT64 = (
    6,
    7,
    8,
    9,
    10,
    11,
    12,
)

_SCALAR_FMT = {
    _T_UINT8: ("<B", 1),
    _T_INT8: ("<b", 1),
    _T_UINT16: ("<H", 2),
    _T_INT16: ("<h", 2),
    _T_UINT32: ("<I", 4),
    _T_INT32: ("<i", 4),
    _T_FLOAT32: ("<f", 4),
    _T_BOOL: ("<?", 1),
    _T_UINT64: ("<Q", 8),
    _T_INT64: ("<q", 8),
    _T_FLOAT64: ("<d", 8),
}

# llama.cpp LLAMA_FTYPE values -> quantization label.
FILE_TYPE_NAMES: Dict[int, str] = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "Q2_K_S",
    22: "IQ3_XS",
    23: "IQ3_XXS",
    24: "IQ1_S",
    25: "IQ4_NL",
    26: "IQ3_S",
    27: "IQ3_M",
    28: "IQ2_S",
    29: "IQ2_M",
    30: "IQ4_XS",
    31: "IQ1_M",
    32: "BF16",
    36: "TQ1_0",
    37: "TQ2_0",
    38: "MXFP4_MOE",
}

# Metadata arrays longer than this are skipped (vocab, merges, ...).
_MAX_ARRAY_ITEMS_KEPT = 64
# Keys worth keeping even when they are small arrays.
_KEEP_ARRAY_PREFIXES = ("general.tags", "general.languages")


@dataclass
class GgufInfo:
    """Parsed header of one GGUF file."""

    version: int
    architecture: Optional[str]
    name: Optional[str]
    file_type: Optional[int]
    quantization: Optional[str]
    n_layers: Optional[int]
    n_heads: Optional[int]
    n_kv_heads: Optional[int]
    head_dim: Optional[int]
    embedding_length: Optional[int]
    context_length: Optional[int]
    vocab_size: Optional[int]
    parameter_count: Optional[int]
    tensor_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_config(self) -> Dict[str, Any]:
        """Shape dict stored in ``model_versions.config`` for KV estimates."""
        return {
            "architecture": self.architecture,
            "model_name": self.name,
            "n_layers": self.n_layers,
            "n_heads": self.n_heads,
            "n_kv_heads": self.n_kv_heads,
            "head_dim": self.head_dim,
            "hidden_size": self.embedding_length,
            "max_position_embeddings": self.context_length,
            "vocab_size": self.vocab_size,
            "parameter_count": self.parameter_count,
            "quantization": self.quantization,
            "gguf_version": self.version,
        }


class GgufError(ValueError):
    """Raised for a file that is not a readable GGUF."""


def _read(f: BinaryIO, fmt: str, size: int):
    data = f.read(size)
    if len(data) != size:
        raise GgufError("truncated GGUF header")
    return struct.unpack(fmt, data)[0]


def _read_string(f: BinaryIO) -> str:
    length = _read(f, "<Q", 8)
    if length > 64 * 1024 * 1024:
        raise GgufError("GGUF string too long")
    data = f.read(length)
    if len(data) != length:
        raise GgufError("truncated GGUF string")
    return data.decode("utf-8", errors="replace")


def _skip_scalar(f: BinaryIO, vtype: int) -> None:
    if vtype == _T_STRING:
        length = _read(f, "<Q", 8)
        f.seek(length, 1)
        return
    fmt, size = _SCALAR_FMT.get(vtype, (None, None))
    if fmt is None:
        raise GgufError(f"unknown GGUF value type {vtype}")
    f.seek(size, 1)


def _read_value(f: BinaryIO, vtype: int, keep_arrays: bool):
    if vtype == _T_STRING:
        return _read_string(f)
    if vtype == _T_ARRAY:
        item_type = _read(f, "<I", 4)
        count = _read(f, "<Q", 8)
        if item_type == _T_ARRAY:
            raise GgufError("nested GGUF arrays are not supported")
        if not keep_arrays or count > _MAX_ARRAY_ITEMS_KEPT:
            if item_type == _T_STRING:
                for _ in range(count):
                    _skip_scalar(f, _T_STRING)
            else:
                _, size = _SCALAR_FMT[item_type]
                f.seek(size * count, 1)
            return {"__skipped_array__": True, "length": count}
        return [_read_value(f, item_type, keep_arrays) for _ in range(count)]
    fmt, size = _SCALAR_FMT.get(vtype, (None, None))
    if fmt is None:
        raise GgufError(f"unknown GGUF value type {vtype}")
    return _read(f, fmt, size)


def read_gguf(path: "str | Path", with_tensors: bool = True) -> GgufInfo:
    """Parse the header of ``path``. Raises ``GgufError`` for non-GGUF input."""
    p = Path(path)
    with p.open("rb") as f:
        magic = f.read(4)
        if magic != GGUF_MAGIC:
            raise GgufError(f"{p.name}: not a GGUF file")
        version = _read(f, "<I", 4)
        if version not in (2, 3):
            raise GgufError(f"{p.name}: unsupported GGUF version {version}")
        n_tensors = _read(f, "<Q", 8)
        n_kv = _read(f, "<Q", 8)
        if n_kv > 1_000_000 or n_tensors > 10_000_000:
            raise GgufError(f"{p.name}: implausible GGUF header counts")

        meta: Dict[str, Any] = {}
        for _ in range(n_kv):
            key = _read_string(f)
            vtype = _read(f, "<I", 4)
            keep = key.startswith(_KEEP_ARRAY_PREFIXES) or not key.startswith("tokenizer.")
            value = _read_value(f, vtype, keep_arrays=keep)
            if isinstance(value, dict) and value.get("__skipped_array__"):
                meta[key] = f"<array:{value['length']}>"
            else:
                meta[key] = value

        parameter_count: Optional[int] = None
        if with_tensors:
            total = 0
            try:
                for _ in range(n_tensors):
                    _read_string(f)  # tensor name
                    n_dims = _read(f, "<I", 4)
                    if n_dims > 8:
                        raise GgufError("bad tensor dims")
                    count = 1
                    for _ in range(n_dims):
                        count *= _read(f, "<Q", 8)
                    _read(f, "<I", 4)  # ggml type
                    _read(f, "<Q", 8)  # offset
                    total += count
                parameter_count = total
            except GgufError as exc:
                logger.warning(f"gguf tensor section unreadable for {p.name}: {exc}")

    arch = meta.get("general.architecture")
    arch = arch if isinstance(arch, str) else None

    def _int(key: str) -> Optional[int]:
        value = meta.get(key)
        return (
            int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        )

    n_heads = _int(f"{arch}.attention.head_count") if arch else None
    n_kv_heads = _int(f"{arch}.attention.head_count_kv") if arch else None
    embedding = _int(f"{arch}.embedding_length") if arch else None
    head_dim = _int(f"{arch}.attention.key_length") if arch else None
    if head_dim is None and n_heads and embedding:
        head_dim = embedding // n_heads
    file_type = _int("general.file_type")
    vocab = meta.get("tokenizer.ggml.tokens")
    vocab_size = None
    if isinstance(vocab, str) and vocab.startswith("<array:"):
        vocab_size = int(vocab[7:-1])
    elif isinstance(vocab, list):
        vocab_size = len(vocab)

    name = meta.get("general.name")
    return GgufInfo(
        version=version,
        architecture=arch,
        name=name if isinstance(name, str) else None,
        file_type=file_type,
        quantization=FILE_TYPE_NAMES.get(file_type) if file_type is not None else None,
        n_layers=_int(f"{arch}.block_count") if arch else None,
        n_heads=n_heads,
        n_kv_heads=n_kv_heads or n_heads,
        head_dim=head_dim,
        embedding_length=embedding,
        context_length=_int(f"{arch}.context_length") if arch else None,
        vocab_size=vocab_size,
        parameter_count=parameter_count,
        tensor_count=n_tensors,
        metadata=meta,
    )


def is_gguf(path: "str | Path") -> bool:
    """Cheap magic check."""
    try:
        with Path(path).open("rb") as f:
            return f.read(4) == GGUF_MAGIC
    except OSError:
        return False
