"""
Topology service: ``GET /topology`` — clusters -> nodes -> devices
(with slices) -> instances, with live state from the live cache, plus the
cross-instance links of multi-GPU / multi-node deployments:

- ``pd_transfer``: prefill instance -> decode instance of a PD-split deployment
- ``tensor_parallel`` / ``pipeline_parallel``: instances of a deployment with
  ``tp_size`` / ``pp_size`` > 1 spread over several nodes (chained in order)
- ``kv_share``: instances of a deployment with ``kv_sharing`` (chained)

Instances without device ids are attached to the node's first device so they
stay visible.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.exceptions import ResourceNotFoundError
from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.cluster_repository import ClusterRepository
from app.db.postgres.repositories.device_repository import (
    DeviceRepository,
    DeviceSliceRepository,
)
from app.db.postgres.repositories.inventory_repository import InventoryRepository
from app.db.postgres.repositories.node_repository import NodeRepository
from app.schemas.fleet_schema import ClusterCounts, ClusterResponse
from app.services.metrics import live_cache
from app.services.nodes.service import decorate_devices, node_response, with_activity
from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


def build_links(
    instances: List[Dict[str, Any]], deployments: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    by_dep: Dict[str, List[Dict[str, Any]]] = {}
    for inst in instances:
        by_dep.setdefault(str(inst["deployment_id"]), []).append(inst)
    links: List[Dict[str, Any]] = []
    for dep_id, insts in by_dep.items():
        dep = deployments.get(dep_id) or {}
        insts = sorted(insts, key=lambda i: (str(i["node_id"]), str(i["id"])))
        if dep.get("pd_split"):
            prefill = [i for i in insts if i.get("pd_role") == "prefill"]
            decode = [i for i in insts if i.get("pd_role") == "decode"]
            for p in prefill:
                for d in decode:
                    links.append(
                        {
                            "from_instance_id": p["id"],
                            "to_instance_id": d["id"],
                            "kind": "pd_transfer",
                        }
                    )
        multi_node = len({str(i["node_id"]) for i in insts}) > 1
        chain_kinds = []
        if int(dep.get("tp_size") or 1) > 1 and multi_node:
            chain_kinds.append("tensor_parallel")
        if int(dep.get("pp_size") or 1) > 1 and multi_node:
            chain_kinds.append("pipeline_parallel")
        if dep.get("kv_sharing"):
            chain_kinds.append("kv_share")
        for kind in chain_kinds:
            for a, b in zip(insts, insts[1:]):
                links.append(
                    {
                        "from_instance_id": a["id"],
                        "to_instance_id": b["id"],
                        "kind": kind,
                    }
                )
    return links


class TopologyService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.clusters = ClusterRepository(session)
        self.nodes = NodeRepository(session)
        self.devices = DeviceRepository(session)
        self.slices = DeviceSliceRepository(session)
        self.inventory = InventoryRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "TopologyService":
        return TopologyService(db)

    async def get(
        self, cluster_id: Optional[uuid.UUID] = None, include_stopped: bool = False
    ) -> Dict[str, Any]:
        if cluster_id and await self.clusters.get_active(cluster_id) is None:
            raise ResourceNotFoundError(resource_type="Cluster")
        clusters = await self.clusters.list_all_active(cluster_id)
        counts = await self.clusters.counts_for([c.id for c in clusters])
        used = await self.clusters.vram_used_for([c.id for c in clusters])
        nodes = [n for c in clusters for n in await self.nodes.list_active(c.id)]
        node_counts = await self.nodes.counts_for([n.id for n in nodes])
        nodes_by_id = {n.id: n for n in nodes}
        device_rows = await self.devices.list_for_nodes([n.id for n in nodes])
        devices = await decorate_devices(self.devices, self.slices, device_rows, nodes_by_id)
        instances = await self.inventory.list_instances_for_nodes(
            [n.id for n in nodes], include_stopped=include_stopped
        )
        samples = await live_cache.get_many([i["id"] for i in instances], "engine")
        deployments = await self._deployment_shapes({str(i["deployment_id"]) for i in instances})

        devices_by_node: Dict[uuid.UUID, List[Any]] = {}
        for dev in devices:
            devices_by_node.setdefault(dev.node_id, []).append(dev)
        inst_by_device: Dict[str, List[Dict[str, Any]]] = {}
        for inst, sample in zip(instances, samples):
            fresh = sample if live_cache.is_fresh(sample) else {}
            topo_inst = {
                "id": inst["id"],
                "deployment_id": inst["deployment_id"],
                "deployment_name": inst["deployment_name"],
                "model_name": inst["model_name"],
                "engine": inst["engine_name"],
                "state": inst["state"],
                "health": inst["health"],
                "device_ids": inst["device_ids"],
                "traffic_weight": inst["traffic_weight"],
                "origin": inst.get("origin", "managed"),
                "active_sessions": (fresh or {}).get("active_sessions"),
                "waiting_sessions": (fresh or {}).get("waiting_sessions"),
            }
            targets = [str(d) for d in inst["device_ids"]]
            if not targets:
                node_devices = devices_by_node.get(inst["node_id"]) or []
                targets = [str(node_devices[0].id)] if node_devices else []
            for device_id in targets:
                inst_by_device.setdefault(device_id, []).append(topo_inst)

        node_items = {
            n.id: item
            for n, item in zip(
                nodes,
                await with_activity(
                    self.session, nodes, [node_response(n, node_counts.get(n.id)) for n in nodes]
                ),
            )
        }
        out_clusters = []
        for cluster in clusters:
            c = ClusterResponse.model_validate(cluster)
            c.counts = ClusterCounts(
                **{
                    **counts.get(cluster.id, {}),
                    "vram_used_bytes": used.get(cluster.id, 0),
                }
            )
            out_nodes = []
            for node in [n for n in nodes if n.cluster_id == cluster.id]:
                out_nodes.append(
                    {
                        "node": node_items[node.id],
                        "devices": [
                            {
                                "device": dev,
                                "instances": inst_by_device.get(str(dev.id), []),
                            }
                            for dev in devices_by_node.get(node.id, [])
                        ],
                    }
                )
            out_clusters.append({"cluster": c, "nodes": out_nodes})
        return {
            "generated_at": datetime.now(timezone.utc),
            "clusters": out_clusters,
            "links": build_links(instances, deployments),
        }

    async def _deployment_shapes(self, ids: set) -> Dict[str, Dict[str, Any]]:
        if not ids:
            return {}
        rows = (
            await self.session.execute(
                text(
                    "SELECT id::text AS id, tp_size, pp_size, pd_split, kv_sharing "
                    "FROM deployments WHERE id::text = ANY(:ids)"
                ),
                {"ids": list(ids)},
            )
        ).mappings()
        return {r["id"]: dict(r) for r in rows.all()}
