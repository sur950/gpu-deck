"""
Target selector evaluation (libs/contracts/rules/target-selector.schema.json).

All listed conditions are ANDed; lists inside a condition are ORed. An empty
selector matches every deployment. ``time_schedule`` ignores the selector.
"""

from __future__ import annotations

import fnmatch
from typing import Any, Dict, List

from app.core.logger import get_logger
from app.services.rules.inputs import DeploymentSnapshot

logger = get_logger(__name__)


def _ids(values: Any) -> set:
    return {str(v) for v in (values or [])}


def matches(selector: Dict[str, Any], deployment: DeploymentSnapshot) -> bool:
    """True when ``deployment`` satisfies every condition of ``selector``."""
    selector = selector or {}
    dep_id = str(deployment.id)

    if dep_id in _ids(selector.get("exclude_deployment_ids")):
        return False
    if selector.get("deployment_ids") and dep_id not in _ids(selector["deployment_ids"]):
        return False
    if selector.get("model_ids") and str(deployment.model_id) not in _ids(selector["model_ids"]):
        return False
    if selector.get("cluster_ids"):
        if deployment.cluster_id is None:
            return False
        if str(deployment.cluster_id) not in _ids(selector["cluster_ids"]):
            return False
    if selector.get("node_ids"):
        wanted = _ids(selector["node_ids"])
        if not any(str(n) in wanted for n in deployment.node_ids):
            return False
    if selector.get("model_types") and deployment.model_type not in selector["model_types"]:
        return False
    if selector.get("priorities") and deployment.priority not in selector["priorities"]:
        return False
    if selector.get("engines") and deployment.engine_name not in selector["engines"]:
        return False
    labels = selector.get("labels") or {}
    if labels:
        for key, value in labels.items():
            if deployment.node_labels.get(key) != value:
                return False
    glob = selector.get("name_glob")
    if glob and not fnmatch.fnmatchcase(deployment.name, glob):
        return False
    return True


def select_targets(
    selector: Dict[str, Any], deployments: List[DeploymentSnapshot]
) -> List[DeploymentSnapshot]:
    return [d for d in deployments if matches(selector, d)]
