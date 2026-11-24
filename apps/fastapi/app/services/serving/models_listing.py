"""
Model listings in the three formats, built from the in-memory routing table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core.settings import settings
from app.services.serving.errors import ServingError
from app.services.serving.routing import ModelEntry, get_routing_table
from app.services.serving.translate.anthropic import anthropic_models_list
from app.services.serving.translate.ollama import ollama_ps, ollama_show, ollama_tags


def _entries() -> List[ModelEntry]:
    return get_routing_table().list_models(include_unloaded=settings.SERVING_LIST_UNLOADED_MODELS)


def _created_epoch(entry: ModelEntry) -> int:
    if entry.created_at:
        try:
            return int(
                datetime.fromisoformat(str(entry.created_at).replace("Z", "+00:00")).timestamp()
            )
        except ValueError:
            pass
    return int(datetime.now(timezone.utc).timestamp())


def openai_model(entry: ModelEntry, name: str | None = None) -> Dict[str, Any]:
    return {
        "id": name or entry.name,
        "object": "model",
        "created": _created_epoch(entry),
        "owned_by": "gpudeck",
        "gpudeck": {
            "type": entry.type,
            "deployment_id": entry.deployment_id,
            "engine": entry.engine or (entry.instances[0].engine if entry.instances else None),
            "ready_replicas": len(entry.ready_instances),
            "aliases": entry.aliases,
            # Set on a per-deployment name: the model it serves.
            "model": entry.model_name,
            # On a model name served by several deployments: each one's addressable name.
            "deployments": entry.deployments if len(entry.deployments) > 1 else [],
        },
    }


def openai_models() -> Dict[str, Any]:
    data: List[Dict[str, Any]] = []
    for entry in _entries():
        data.append(openai_model(entry))
        for alias in entry.aliases:
            data.append(openai_model(entry, alias))
    return {"object": "list", "data": data}


def openai_model_by_name(name: str) -> Dict[str, Any]:
    entry = get_routing_table().get(name)
    if entry is None or (not entry.ready_instances and not settings.SERVING_LIST_UNLOADED_MODELS):
        raise ServingError.model_not_found(name)
    return openai_model(entry, name)


def anthropic_models() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return anthropic_models_list(_entries(), now)


def anthropic_model_by_name(name: str) -> Dict[str, Any]:
    entry = get_routing_table().get(name)
    if entry is None or (not entry.ready_instances and not settings.SERVING_LIST_UNLOADED_MODELS):
        raise ServingError(404, "not_found_error", f"model '{name}' not found")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "type": "model",
        "id": name,
        "display_name": entry.display_name or entry.name,
        "created_at": entry.created_at or now,
    }


def ollama_tags_payload() -> Dict[str, Any]:
    return ollama_tags(_entries())


def ollama_ps_payload() -> Dict[str, Any]:
    return ollama_ps(get_routing_table().list_models())


def ollama_show_payload(name: str) -> Dict[str, Any]:
    entry = get_routing_table().get(name)
    if entry is None:
        raise ServingError(404, "model_not_found", f"model '{name}' not found")
    return ollama_show(entry)
