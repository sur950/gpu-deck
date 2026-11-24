"""
Local path scanner for the model catalog.

Finds model candidates under a path on the control-plane host:

* **Ollama** (``~/.ollama/models``): ``manifests/<registry>/<ns>/<model>/<tag>``
  JSON -> layer ``application/vnd.ollama.image.model`` -> ``blobs/sha256-<hex>``
  (a plain GGUF file; the digest is its sha256).
* **LM Studio** (``~/.lmstudio/models/<publisher>/<model>/``) plus the bundled
  models under ``~/.lmstudio/.internal/bundled-models``.
* **Hugging Face cache** (``models--org--name/snapshots/<rev>/``; symlinks are
  resolved to the blob files).
* **MLX** directories (safetensors + ``config.json`` with a ``quantization`` block).
* **Diffusers pipelines** (``model_index.json`` at the root): one candidate with every
  component file (``transformer/…``, ``vae/…``, ``text_encoder/…``), never the parts.
* **Plain** GGUF files (sharded sets grouped) and safetensors / pickle directories.

Suggestions (name, family, type, quantization, context size) follow the same rules as the
node agent's scanner (``apps/node-agent/crates/gpudeck-agent/src/scan.rs``): quantization from
the GGUF file type, then the name (``FP8``, ``Q4_K_M``), then ``config.json``
``quantization_config``; context from ``max_position_embeddings`` (also in ``text_config``).

Everything here is synchronous file-system work; the service runs it in a
thread. Nothing is ever written.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from app.core.logger import get_logger
from app.services.catalog import detect
from app.services.catalog.gguf import GgufError, is_gguf, read_gguf

logger = get_logger(__name__)

OLLAMA_MODEL_MEDIA_TYPE = "application/vnd.ollama.image.model"
OLLAMA_PROJECTOR_MEDIA_TYPE = "application/vnd.ollama.image.projector"
_HF_CACHE_DIR_RE = re.compile(r"^models--([^-].*?)--(.+)$")
_SHARD_GROUP_RE = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.(gguf|safetensors)$", re.I)
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".cache", "blobs"}


@dataclass
class ScannedFile:
    path: str  # relative to the candidate root (or the file name for single files)
    size_bytes: int
    format: str
    sha256: Optional[str] = None
    is_required: bool = True
    lfs: bool = False
    absolute_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "sha256": self.sha256,
            "is_required": self.is_required,
            "lfs": self.lfs,
        }


@dataclass
class ScanCandidate:
    path: str  # absolute root: a file (gguf/blob) or a directory
    kind: str  # plain | ollama_blob | lmstudio | hf_cache | mlx_dir
    format: str
    files: List[ScannedFile]
    total_size_bytes: int
    suggested: Dict[str, Any] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "format": self.format,
            "files": [f.to_dict() for f in self.files],
            "total_size_bytes": self.total_size_bytes,
            "suggested": self.suggested,
        }


class LocalScanner:
    """Walk a path and return model candidates. See module docstring."""

    def __init__(self, max_depth: int = 4, max_files: int = 20000):
        self.max_depth = max_depth
        self.max_files = max_files
        self._visited = 0

    # ------------------------------------------------------------------ public

    def scan(self, path: str, recursive: bool = True) -> List[ScanCandidate]:
        root = Path(os.path.expanduser(path))
        if not root.exists():
            raise FileNotFoundError(path)
        self._visited = 0
        results: List[ScanCandidate] = []
        seen: Set[str] = set()

        if root.is_file():
            cand = self._scan_single_file(root)
            return [cand] if cand else []

        # Ollama store (root, or a manifests/blobs child, or a manifest file)
        ollama_root = self._find_ollama_root(root)
        if ollama_root is not None:
            results.extend(self._scan_ollama(ollama_root, only_under=root))
            if ollama_root == root or root.name in ("manifests", "blobs"):
                return self._dedupe(results, seen)

        # LM Studio root (~/.lmstudio or ~/.lmstudio/models)
        lms_root = self._find_lmstudio_root(root)
        if lms_root is not None:
            results.extend(self._scan_lmstudio(lms_root))
            return self._dedupe(results, seen)

        results.extend(self._walk(root, depth=0, recursive=recursive))
        return self._dedupe(results, seen)

    # ----------------------------------------------------------------- helpers

    def _scan_single_file(self, file: Path) -> Optional[ScanCandidate]:
        """A path to one file: Ollama blob/manifest, a GGUF, or a weight in a model dir."""
        ollama_root = self._find_ollama_root(file.parent)
        if ollama_root is not None:
            manifests = ollama_root / "manifests"
            if file.parent.name == "blobs":
                # Find the manifest whose model layer is this blob.
                for dirpath, _, filenames in os.walk(manifests):
                    for fname in filenames:
                        cand = self._ollama_candidate(ollama_root, manifests, Path(dirpath) / fname)
                        if cand and cand.path == str(file):
                            return cand
            else:
                cand = self._ollama_candidate(ollama_root, manifests, file)
                if cand:
                    return cand
        fmt = detect.detect_format(file.name)
        if fmt == "gguf" and is_gguf(file):
            cands = self._gguf_candidates(file.parent, [file], [file])
            for cand in cands:
                if cand.path in (str(file), str(file.parent)):
                    cand.path = str(file)
                    cand.files = [f for f in cand.files if f.path == file.name]
                    cand.total_size_bytes = sum(f.size_bytes for f in cand.files)
                    m = re.match(r"^sha256-([0-9a-f]{64})$", file.name)
                    if m:
                        cand.files[0].sha256 = m.group(1)
                    return cand
            return None
        if fmt in ("safetensors", "pickle"):
            for cand in self._candidates_in_dir(file.parent, recursive=False, depth=0):
                if cand.path == str(file.parent):
                    return cand
        return None

    def _dedupe(self, results: List[ScanCandidate], seen: Set[str]) -> List[ScanCandidate]:
        out = []
        for cand in results:
            if cand.path in seen:
                continue
            seen.add(cand.path)
            out.append(cand)
        out.sort(key=lambda c: c.path)
        return out

    def _tick(self) -> bool:
        self._visited += 1
        if self._visited > self.max_files:
            logger.warning(f"local scan stopped after {self.max_files} entries")
            return False
        return True

    @staticmethod
    def _size(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            return 0

    # ------------------------------------------------------------------ Ollama

    @staticmethod
    def _find_ollama_root(path: Path) -> Optional[Path]:
        for cand in [path, *path.parents]:
            if (cand / "manifests").is_dir() and (cand / "blobs").is_dir():
                return cand
        return None

    def _scan_ollama(self, root: Path, only_under: Optional[Path] = None) -> List[ScanCandidate]:
        out: List[ScanCandidate] = []
        manifests = root / "manifests"
        for dirpath, dirnames, filenames in os.walk(manifests):
            dirnames.sort()
            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                mpath = Path(dirpath) / fname
                if only_under is not None and only_under not in (
                    root,
                    manifests,
                    root / "blobs",
                ):
                    # A sub-path of the store (e.g. manifests/.../library/qwen3): only
                    # the manifests below it.
                    try:
                        mpath.relative_to(only_under)
                    except ValueError:
                        continue
                if not self._tick():
                    return out
                cand = self._ollama_candidate(root, manifests, mpath)
                if cand:
                    out.append(cand)
        return out

    def _ollama_candidate(
        self, root: Path, manifests: Path, mpath: Path
    ) -> Optional[ScanCandidate]:
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        layers = manifest.get("layers") or []
        model_layer = next(
            (ly for ly in layers if ly.get("mediaType") == OLLAMA_MODEL_MEDIA_TYPE),
            None,
        )
        if not model_layer:
            return None
        digest = str(model_layer.get("digest", ""))
        if not digest.startswith("sha256:"):
            return None
        sha = digest.split(":", 1)[1]
        blob = root / "blobs" / f"sha256-{sha}"
        if not blob.is_file():
            return None
        rel = mpath.relative_to(manifests).parts  # registry, ns, model, tag
        if len(rel) >= 4:
            ns, model, tag = rel[-3], rel[-2], rel[-1]
            name = f"{model}:{tag}" if ns == "library" else f"{ns}/{model}:{tag}"
            suggested_name = f"{model}:{tag}" if ns == "library" else f"{ns}-{model}:{tag}"
        else:
            name = "/".join(rel)
            suggested_name = detect.suggested_name(name)
        size = int(model_layer.get("size") or self._size(blob))
        files = [
            ScannedFile(
                path=blob.name,
                size_bytes=size,
                format="gguf",
                sha256=sha,
                absolute_path=str(blob),
            )
        ]
        for ly in layers:
            if ly.get("mediaType") == OLLAMA_PROJECTOR_MEDIA_TYPE:
                pd = str(ly.get("digest", "")).split(":", 1)[-1]
                pblob = root / "blobs" / f"sha256-{pd}"
                if pblob.is_file():
                    files.append(
                        ScannedFile(
                            path=pblob.name,
                            size_bytes=int(ly.get("size") or self._size(pblob)),
                            format="gguf",
                            sha256=pd,
                            absolute_path=str(pblob),
                        )
                    )
        info, config = self._gguf_config(blob)
        suggested = self._suggest(
            name_hint=suggested_name,
            repo_hint=name,
            info=info,
            config=config,
            is_file=True,
        )
        suggested["ollama_name"] = name
        return ScanCandidate(
            path=str(blob),
            kind="ollama_blob",
            format="gguf",
            files=files,
            total_size_bytes=sum(f.size_bytes for f in files),
            suggested=suggested,
            config=config,
        )

    # --------------------------------------------------------------- LM Studio

    @staticmethod
    def _find_lmstudio_root(path: Path) -> Optional[Path]:
        """``~/.lmstudio`` or ``~/.lmstudio/models`` -> the ``.lmstudio`` dir."""
        if path.name == ".lmstudio" and (path / "models").is_dir():
            return path
        if path.name == "models" and path.parent.name == ".lmstudio":
            return path.parent
        return None

    def _scan_lmstudio(self, lms_root: Path) -> List[ScanCandidate]:
        out: List[ScanCandidate] = []
        roots = [lms_root / "models", lms_root / ".internal" / "bundled-models"]
        for base in roots:
            if not base.is_dir():
                continue
            for publisher in sorted(p for p in base.iterdir() if p.is_dir()):
                if publisher.name.startswith("."):
                    continue
                for model_dir in sorted(p for p in publisher.iterdir() if p.is_dir()):
                    if not self._tick():
                        return out
                    for cand in self._candidates_in_dir(model_dir, recursive=True, depth=0):
                        cand.kind = "lmstudio"
                        cand.suggested.setdefault("publisher", publisher.name)
                        cand.suggested["lmstudio_name"] = f"{publisher.name}/{model_dir.name}"
                        out.append(cand)
        return out

    # ------------------------------------------------------------- generic walk

    def _walk(self, root: Path, depth: int, recursive: bool) -> List[ScanCandidate]:
        out: List[ScanCandidate] = []
        if not self._tick():
            return out
        hf = _HF_CACHE_DIR_RE.match(root.name)
        if hf:
            return self._scan_hf_cache_dir(root, hf.group(1), hf.group(2))
        if root.name == "hub" or any(_HF_CACHE_DIR_RE.match(p.name) for p in self._subdirs(root)):
            for sub in self._subdirs(root):
                m = _HF_CACHE_DIR_RE.match(sub.name)
                if m:
                    out.extend(self._scan_hf_cache_dir(sub, m.group(1), m.group(2)))
            if out:
                return out

        cands = self._candidates_in_dir(root, recursive=False, depth=depth)
        if cands:
            out.extend(cands)
            # A model directory is a leaf: do not descend further.
            if any(c.kind in ("mlx_dir", "plain") and c.path == str(root) for c in cands):
                return out
        if recursive and depth < self.max_depth:
            for sub in self._subdirs(root):
                if sub.name in _SKIP_DIRS or (sub.name.startswith(".") and depth > 0):
                    continue
                if (sub / "manifests").is_dir() and (sub / "blobs").is_dir():
                    out.extend(self._scan_ollama(sub))
                    continue
                if sub.name == ".lmstudio" and (sub / "models").is_dir():
                    out.extend(self._scan_lmstudio(sub))
                    continue
                out.extend(self._walk(sub, depth + 1, recursive))
        return out

    @staticmethod
    def _subdirs(root: Path) -> List[Path]:
        try:
            return sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return []

    def _scan_hf_cache_dir(self, repo_dir: Path, org: str, name: str) -> List[ScanCandidate]:
        snapshots = repo_dir / "snapshots"
        if not snapshots.is_dir():
            return []
        out = []
        for snap in self._subdirs(snapshots):
            for cand in self._candidates_in_dir(snap, recursive=True, depth=0):
                cand.kind = "hf_cache"
                cand.suggested["repo_id"] = f"{org}/{name}"
                cand.suggested["revision"] = snap.name
                cand.suggested["name"] = detect.suggested_name(name)
                out.append(cand)
        return out

    def _candidates_in_dir(self, d: Path, recursive: bool, depth: int) -> List[ScanCandidate]:
        """Candidates rooted at ``d`` itself: a safetensors/mlx/pickle dir, or GGUF files."""
        if (d / "model_index.json").is_file():
            # A diffusers pipeline: one candidate for the whole tree, never its components.
            cand = self._pipeline_candidate(d)
            return [cand] if cand else []
        try:
            entries = sorted(p for p in d.iterdir() if not p.name.startswith("."))
        except OSError:
            return []
        files = [p for p in entries if p.is_file() or p.is_symlink()]
        names = {p.name for p in files}
        out: List[ScanCandidate] = []

        config = self._read_json(d / "config.json") if "config.json" in names else {}
        weight_formats = {detect.detect_format(p.name) for p in files}
        weight_formats.discard("other")

        gguf_files = [p for p in files if p.name.lower().endswith(".gguf")]
        if gguf_files:
            out.extend(self._gguf_candidates(d, gguf_files, files))

        dir_formats = {f for f in weight_formats if f != "gguf"}
        if dir_formats:
            is_mlx = detect.is_mlx_config(config) or "mlx" in d.name.lower()
            fmt = detect.version_format(dir_formats, is_mlx=is_mlx)
            scanned = []
            for p in files:
                if p.name.lower().endswith(".gguf"):
                    continue
                ffmt = detect.detect_format(p.name)
                if ffmt == "other" and not detect.is_required_file(p.name, ffmt):
                    continue
                real = p.resolve() if p.is_symlink() else p
                sidx, scount = detect.shard_info(p.name)
                scanned.append(
                    ScannedFile(
                        path=p.name,
                        size_bytes=self._size(real),
                        format=ffmt,
                        is_required=detect.is_required_file(p.name, ffmt),
                        absolute_path=str(real),
                    )
                )
                _ = (sidx, scount)
            if scanned:
                shape = detect.shape_from_hf_config(config) if config else {}
                suggested = self._suggest(
                    name_hint=d.name,
                    repo_hint=str(d),
                    info=None,
                    config=config,
                    is_file=False,
                )
                if is_mlx:
                    suggested["type"] = suggested.get("type") or "chat"
                out.append(
                    ScanCandidate(
                        path=str(d),
                        kind="mlx_dir" if fmt == "mlx" else "plain",
                        format=fmt,
                        files=scanned,
                        total_size_bytes=sum(f.size_bytes for f in scanned),
                        suggested=suggested,
                        config=shape,
                    )
                )
        if recursive and depth < self.max_depth:
            for sub in self._subdirs(d):
                if sub.name in _SKIP_DIRS or sub.name.startswith("."):
                    continue
                out.extend(self._candidates_in_dir(sub, recursive=True, depth=depth + 1))
        return out

    def _pipeline_candidate(self, d: Path) -> Optional[ScanCandidate]:
        """A diffusers pipeline dir: every component file, paths relative to ``d``."""
        index = self._read_json(d / "model_index.json")
        cls = str(index.get("_class_name") or "DiffusionPipeline")
        files: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(d):
            rel_depth = len(Path(dirpath).relative_to(d).parts)
            dirnames[:] = sorted(
                n
                for n in dirnames
                if not n.startswith(".")
                and n not in _SKIP_DIRS
                and rel_depth < 3
                and not (Path(dirpath) / n).is_symlink()
            )
            for fname in sorted(filenames):
                if not fname.startswith("."):
                    files.append(Path(dirpath) / fname)
            self._visited += 1
        st_dirs = {
            str(p.parent.relative_to(d))
            for p in files
            if detect.detect_format(p.name) == "safetensors"
        }
        scanned: List[ScannedFile] = []
        for p in sorted(files):
            fmt = detect.detect_format(p.name)
            if fmt == "other" and not detect.is_required_file(p.name, fmt):
                continue
            rel = p.relative_to(d)
            required = detect.is_required_file(p.name, fmt) and not (
                fmt == "pickle" and str(rel.parent) in st_dirs
            )
            real = p.resolve() if p.is_symlink() else p
            scanned.append(
                ScannedFile(
                    path=rel.as_posix(),
                    size_bytes=self._size(real),
                    format=fmt,
                    is_required=required,
                    absolute_path=str(real),
                )
            )
        if not scanned:
            return None
        fmt = detect.version_format(
            {f.format for f in scanned if f.is_required and f.format != "other"}
        )
        suggested = self._suggest(
            name_hint=d.name, repo_hint=str(d), info=None, config={}, is_file=False
        )
        suggested["type"] = pipeline_type(cls, f"{d.name} {d}")
        suggested["family"] = suggested.get("family") or detect.detect_family(cls)
        suggested["pipeline_class"] = cls
        if not suggested.get("quantization"):
            for comp in ("transformer", "unet", "text_encoder"):
                q = quant_from_config(self._read_json(d / comp / "config.json"))
                if q:
                    suggested["quantization"] = q
                    break
        return ScanCandidate(
            path=str(d),
            kind="plain",
            format=fmt,
            files=scanned,
            total_size_bytes=sum(f.size_bytes for f in scanned),
            suggested=suggested,
            config={
                "pipeline": "diffusers",
                "pipeline_class": cls,
                "components": [
                    k for k, v in index.items() if not k.startswith("_") and isinstance(v, list)
                ],
            },
        )

    def _gguf_candidates(
        self, d: Path, gguf_files: List[Path], all_files: List[Path]
    ) -> List[ScanCandidate]:
        groups: Dict[str, List[Path]] = {}
        for p in gguf_files:
            m = _SHARD_GROUP_RE.match(p.name)
            key = m.group(1) if m else p.name
            groups.setdefault(key, []).append(p)
        out = []
        mmproj = [p for p in gguf_files if "mmproj" in p.name.lower()]
        for key, parts in sorted(groups.items()):
            if any("mmproj" in p.name.lower() for p in parts) and len(groups) > 1:
                continue  # projector belongs to another group
            parts = sorted(parts)
            first = parts[0]
            if not is_gguf(first.resolve()):
                continue
            scanned = []
            for p in parts:
                real = p.resolve()
                sidx, scount = detect.shard_info(p.name)
                scanned.append(
                    ScannedFile(
                        path=p.name,
                        size_bytes=self._size(real),
                        format="gguf",
                        absolute_path=str(real),
                    )
                )
                _ = (sidx, scount)
            for proj in mmproj:
                if proj not in parts:
                    real = proj.resolve()
                    scanned.append(
                        ScannedFile(
                            path=proj.name,
                            size_bytes=self._size(real),
                            format="gguf",
                            is_required=False,
                            absolute_path=str(real),
                        )
                    )
            info, config = self._gguf_config(first.resolve())
            suggested = self._suggest(
                name_hint=first.stem,
                repo_hint=str(d),
                info=info,
                config=config,
                is_file=True,
            )
            single = len(parts) == 1 and not mmproj
            out.append(
                ScanCandidate(
                    path=str(first if single else d),
                    kind="plain",
                    format="gguf",
                    files=scanned,
                    total_size_bytes=sum(f.size_bytes for f in scanned),
                    suggested=suggested,
                    config=config,
                )
            )
        return out

    # ------------------------------------------------------------- suggestions

    @staticmethod
    def _read_json(p: Path) -> Dict[str, Any]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _gguf_config(blob: Path):
        try:
            info = read_gguf(blob)
            return info, info.as_config()
        except (GgufError, OSError) as exc:
            logger.warning(f"gguf header unreadable for {blob}: {exc}")
            return None, {}

    @staticmethod
    def _suggest(
        *,
        name_hint: str,
        repo_hint: str,
        info,
        config: Dict[str, Any],
        is_file: bool,
    ) -> Dict[str, Any]:
        arch = info.architecture if info else (config.get("model_type") if config else None)
        model_name = info.name if info else None
        family = detect.detect_family(model_name, name_hint, repo_hint, architecture=arch)
        mtype = detect.detect_type(
            name=f"{model_name or ''} {name_hint} {repo_hint}",
            architecture=info.architecture if info else None,
            config=config if (config and "architectures" in config) else None,
        )
        quant = (info.quantization if info else None) or detect.detect_quantization(
            name_hint, repo_hint
        )
        if not quant and config:
            quant = quant_from_config(config)
        ctx = (info.context_length if info else None) or detect.context_from_config(config)
        return {
            "name": detect.suggested_name(name_hint),
            "family": family,
            "type": mtype,
            "quantization": quant,
            "context_size": ctx,
            "parameter_count": (info.parameter_count if info else config.get("parameter_count")),
        }


def _compressed_tensors_label(q: Dict[str, Any]) -> Optional[str]:
    groups = q.get("config_groups")
    if not isinstance(groups, dict):
        return None
    for g in groups.values():
        w = g.get("weights") if isinstance(g, dict) else None
        if isinstance(w, dict) and isinstance(w.get("num_bits"), int):
            bits, is_float = w["num_bits"], w.get("type") == "float"
            if is_float:
                return "FP8" if bits == 8 else ("FP4" if bits == 4 else f"FP{bits}")
            return f"INT{bits}"
    return None


def quant_from_config(config: Optional[Dict[str, Any]]) -> Optional[str]:
    """Quantization label from an HF ``config.json`` (same rules as the node agent)."""
    if not config:
        return None
    q = config.get("quantization_config") or config.get("quantization")
    if not isinstance(q, dict):
        text = config.get("text_config")
        q = text.get("quantization_config") if isinstance(text, dict) else None
    if not isinstance(q, dict):
        return None
    method = str(q.get("quant_method") or "").lower()
    label: Optional[str] = None
    if method in ("fp8", "fbgemm_fp8", "ptpc_fp8"):
        label = "FP8"
    elif method == "awq":
        label = "AWQ"
    elif method in ("gptq", "gptq_marlin"):
        label = "GPTQ"
    elif method == "mxfp4":
        label = "MXFP4"
    elif method == "bitsandbytes":
        if q.get("load_in_4bit"):
            label = "NF4" if q.get("bnb_4bit_quant_type") == "nf4" else "4BIT"
        else:
            label = "8BIT"
    elif method == "modelopt":
        algo = q.get("quant_algo")
        label = str(algo).upper() if algo else None
    elif method == "compressed-tensors":
        label = _compressed_tensors_label(q)
    if label:
        return label
    return f"{q['bits']}bit" if isinstance(q.get("bits"), int) else None


def pipeline_type(class_name: str, name: str) -> str:
    """``image`` / ``video`` / ``tts`` for a diffusers pipeline (same rules as the node agent)."""
    lower = f"{class_name} {name}".lower()
    if any(
        k in lower
        for k in ("video", "wan", "i2v", "t2v", "ltx", "cogvideo", "mochi", "hunyuanvideo")
    ):
        return "video"
    if "audio" in lower or "music" in lower:
        return "tts"
    return "image"


def complete_suggestions(
    suggested: Dict[str, Any], path: str, metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Fill what a node agent left unknown (``None`` / missing) in a scan candidate's
    ``suggested`` with the control plane's detection: family, type, quantization and context
    from the name hints (name, repo id, display / Ollama / LM Studio names, the path), the
    architecture and the raw ``metadata`` (GGUF header keys or a ``config.json`` subset).
    Values the agent sent are kept.
    """
    out = dict(suggested)
    meta = metadata or {}
    hints = [
        str(out[k])
        for k in ("display_name", "name", "repo_id", "ollama_name", "lmstudio_name")
        if out.get(k)
    ] + [path]
    gguf_arch = meta.get("general.architecture")
    arch = out.get("architecture") or gguf_arch or meta.get("model_type")
    if not out.get("family"):
        family = detect.detect_family(*hints, architecture=arch)
        if family:
            out["family"] = family
    if not out.get("type"):
        mtype = detect.detect_type(
            name=" ".join(hints),
            architecture=gguf_arch,
            config=meta if "architectures" in meta else None,
        )
        if mtype:
            out["type"] = mtype
    if not out.get("quantization"):
        quant = detect.detect_quantization(*hints) or quant_from_config(meta)
        if quant:
            out["quantization"] = quant
    if not out.get("context_size"):
        ctx = (meta.get(f"{gguf_arch}.context_length") if gguf_arch else None) or (
            detect.context_from_config(meta)
        )
        if ctx:
            out["context_size"] = ctx
    return out


def scan_path(path: str, recursive: bool = True, max_depth: int = 4, max_files: int = 20000):
    """Module-level convenience used by the service and tests."""
    return LocalScanner(max_depth=max_depth, max_files=max_files).scan(path, recursive=recursive)


def iter_candidate_files(cand: ScanCandidate) -> Iterable[ScannedFile]:
    return list(cand.files)
