"""
Update status of a node, derived from what its agent reports (``hello`` / ``inventory``
``agent_update``, kept in ``nodes.metadata.agent_update``), its capabilities, the target version
and its update runs. Pure functions: no I/O.

- ``managed``: Kubernetes DaemonSet (``capabilities.kubernetes``) - "Managed by Helm", never
  updated by GPU Deck;
- ``manual``: an agent that predates self-update (no ``stage_update`` / ``apply_update`` in
  ``capabilities.commands``) - update it once by hand, it updates itself from then on;
- ``unsupported``: the agent can update but this machine cannot (no update helper, binary not
  writable, ``update_helper = "off"``); ``reason`` says why;
- otherwise ``up_to_date`` / ``update_available`` / ``staging`` / ``staged`` / ``updating``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.logger import get_logger
from app.services.agent_updates.releases import compare
from app.services.nodes.files import supports

logger = get_logger(__name__)

CONNECTED = ("online", "draining", "drained")
UPDATE_COMMANDS = ("stage_update", "apply_update")

MANUAL_INSTRUCTIONS = (
    "This agent was installed before agents could update themselves. Update it once by "
    "re-running its install command (Nodes, Add node: the installer replaces the binary, adds "
    "the update helper and restarts the agent; running models keep serving). From then on it "
    "updates from here."
)
UNSUPPORTED_INSTRUCTIONS = (
    "Re-run the install command on the machine (it installs the root-owned update helper), or "
    "replace the binary by hand and restart the gpudeck-agent service; running models keep "
    "serving across the restart."
)
UNSIGNED_INSTRUCTIONS = (
    "No signed agent release is available to update to (no release signing key configured, or "
    "the release was published unsigned), so this control plane never updates agents by "
    "itself. Update this node by re-running its install command (Nodes, Add node; running "
    "models keep serving), or set up the release signing key (DEPLOYMENT.md, agent updates)."
)
MANAGED_INSTRUCTIONS = (
    "Managed by Helm: update the agent image of the DaemonSet (helm upgrade); GPU Deck never "
    "replaces it in place."
)


def reported(node: Any) -> Dict[str, Any]:
    meta = getattr(node, "metadata_", None) or {}
    value = meta.get("agent_update") if isinstance(meta, dict) else None
    return value if isinstance(value, dict) else {}


def active_marker(node: Any) -> Optional[Dict[str, Any]]:
    meta = getattr(node, "metadata_", None) or {}
    value = meta.get("agent_update_active") if isinstance(meta, dict) else None
    return value if isinstance(value, dict) and value.get("run_id") else None


def is_managed(node: Any) -> bool:
    caps = getattr(node, "capabilities", None) or {}
    return bool(caps.get("kubernetes")) or reported(node).get("mode") == "managed"


def supports_updates(node: Any) -> bool:
    caps = getattr(node, "capabilities", None) or {}
    return all(supports(caps, c) for c in UPDATE_COMMANDS)


def node_mode(node: Any) -> str:
    if is_managed(node):
        return "managed"
    if not supports_updates(node):
        return "manual"
    mode = reported(node).get("mode")
    return mode if mode in ("helper", "self", "unsupported") else "unsupported"


def node_target(node: Any) -> Optional[str]:
    """The build target the agent reported (release artifacts are per target)."""
    t = reported(node).get("target")
    return str(t) if t else None


def needs_update(node: Any, target_version: Optional[str]) -> bool:
    return (
        bool(target_version) and compare(getattr(node, "agent_version", None), target_version) < 0
    )


def node_info(
    node: Any,
    target_version: Optional[str],
    disabled_reason: Optional[str] = None,
    active_run: Any = None,
    last_run: Any = None,
    release_version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ``NodeAgentUpdate`` for one node. ``release_version`` is the control plane's own version:
    with no signed release to update to (no release key, or the release was published without a
    signature) a node behind it shows "update manually" and is never updated automatically.
    """
    rep = reported(node)
    mode = node_mode(node)
    current = getattr(node, "agent_version", None)
    available = needs_update(node, target_version)
    staged = rep.get("staged_version")
    info: Dict[str, Any] = {
        "current_version": current,
        "target_version": target_version,
        "mode": mode,
        "reason": None,
        "update_available": available,
        "can_update": False,
        "state": rep.get("state"),
        "staged_version": staged,
        "staged_at": rep.get("staged_at"),
        "previous_version": rep.get("previous_version"),
        "instructions": None,
        "active_run": active_run,
        "last_run": last_run,
    }
    if mode == "managed":
        info.update(
            status="managed",
            reason="Managed by Helm",
            instructions=MANAGED_INSTRUCTIONS,
        )
        return info
    if active_run is not None or active_marker(node):
        info["status"] = "updating"
        return info
    if target_version is None:
        if release_version and needs_update(node, release_version):
            info.update(
                status="manual",
                reason=disabled_reason,
                update_available=True,
                target_version=release_version,
                instructions=UNSIGNED_INSTRUCTIONS,
            )
        else:
            info.update(status="disabled", reason=disabled_reason)
        return info
    if not available:
        info["status"] = "up_to_date"
        return info
    if mode == "manual":
        info.update(
            status="manual",
            reason="this agent predates self-update",
            instructions=MANUAL_INSTRUCTIONS,
        )
        return info
    if mode == "unsupported":
        info.update(
            status="unsupported",
            reason=rep.get("reason") or "the agent reported no update mode",
            instructions=UNSUPPORTED_INSTRUCTIONS,
        )
        return info
    if rep.get("state") == "staging":
        info["status"] = "staging"
    elif staged == target_version:
        info["status"] = "staged"
    else:
        info["status"] = "update_available"
    info["can_update"] = getattr(node, "status", None) in CONNECTED
    if not info["can_update"]:
        info["reason"] = f"node is {getattr(node, 'status', 'unknown')}"
    return info


def count_key(info: Dict[str, Any], node_status: Optional[str]) -> str:
    """Bucket of ``AgentUpdateCounts`` a node falls into."""
    if node_status not in CONNECTED and info["status"] not in ("managed", "updating"):
        return "offline"
    status = info["status"]
    if status in ("staging", "update_available"):
        return "update_available"
    if status == "disabled":
        return "up_to_date"
    return status
