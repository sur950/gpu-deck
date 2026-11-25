"""
In-memory routing table (serving.md §5, §7).

Loaded from Redis key ``gpu-deck:routing:v1`` at boot, refreshed on the
``gpu-deck:routing:updated`` pub/sub channel and by a fallback poll. Nothing on
the request path touches the database: ``RoutingTable.resolve`` is pure
dictionary work plus the in-flight counters.

Load balancing (``traffic.strategy``): ``least_sessions`` (default; live
``active_sessions`` from the hub plus our own in-flight count), ``round_robin``
and ``weighted`` (traffic weight). Weight 0, ``accepting=false``,
``state != ready``, ``health != healthy`` and ejected instances are skipped.

Latency-aware selection (``least_sessions`` with ``SERVING_LATENCY_ROUTING``, the default):
every candidate gets an expected latency

    expected = rtt + SERVING_LATENCY_LOAD_PENALTY_MS * sessions / capacity / (weight / 100)

where ``rtt`` is this API layer's smoothed round trip to the replica's node
(``app.services.serving.latency``; a node not measured yet counts as the farthest measured
one), ``sessions`` = live active + waiting sessions + our in-flight requests and
``capacity`` = the replica's ``max_concurrent`` (else ``SERVING_LATENCY_DEFAULT_CAPACITY``).
Replicas within ``SERVING_LATENCY_TOLERANCE_MS`` of the best share the traffic: the least
loaded of them wins, ties broken at random in proportion to weight. The nearest replica
takes the traffic until its load penalty outweighs the extra RTT of the next one, then the
traffic spills over. Without any RTT measurement the classic least-sessions rule applies
unchanged; failover re-runs the same selection without the replicas already tried.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.serving.errors import ServingError
from app.services.serving.latency import rtt_ms as latency_rtt_ms

logger = get_logger(__name__)

ROUTING_KEY_PARTS = ("routing", "v1")
ROUTING_CHANNEL_PARTS = ("routing", "updated")

# Which model types an endpoint may route to (serving.md §5).
ENDPOINT_MODEL_TYPES: Dict[str, Set[str]] = {
    "chat/completions": {"chat", "thinking", "vision"},
    "responses": {"chat", "thinking", "vision"},
    "completions": {"chat", "thinking", "vision"},
    "messages": {"chat", "thinking", "vision"},
    "messages/count_tokens": {"chat", "thinking", "vision"},
    "messages/batches": {"chat", "thinking", "vision"},
    "api/chat": {"chat", "thinking", "vision"},
    "api/generate": {"chat", "thinking", "vision"},
    "embeddings": {"embedding"},
    "api/embed": {"embedding"},
    "rerank": {"rerank"},
    "audio/transcriptions": {"stt"},
    "audio/translations": {"stt"},
    "audio/speech": {"tts"},
    "images/generations": {"image"},
    "images/edits": {"image"},
    "videos": {"video"},
}


@dataclass
class Instance:
    instance_id: str
    node_id: str
    proxy_url: str
    served_model_name: str
    engine: str = "custom"
    state: str = "ready"
    health: str = "healthy"
    accepting: bool = True
    weight: int = 100
    active_sessions: int = 0
    waiting_sessions: int = 0
    max_concurrent: Optional[int] = None
    pd_role: Optional[str] = None
    direct: bool = False  # dev mode: proxy_url is the engine itself
    deployment_id: Optional[str] = None  # set when a model is served by several deployments

    @property
    def selectable(self) -> bool:
        return (
            self.state == "ready"
            and self.health == "healthy"
            and self.accepting
            and self.weight > 0
        )

    def engine_url(self, engine_path: str) -> str:
        path = engine_path.lstrip("/")
        base = self.proxy_url.rstrip("/")
        if self.direct:
            return f"{base}/{path}"
        return f"{base}/instances/{self.instance_id}/{path}"


@dataclass
class ModelEntry:
    name: str
    model_id: Optional[str]
    type: str
    deployment_id: Optional[str]
    family: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    priority: str = "normal"
    desired_running: bool = True
    traffic: Dict[str, Any] = field(default_factory=dict)
    native_formats: List[str] = field(default_factory=lambda: ["openai"])
    tool_parser: Optional[str] = None
    context_size: Optional[int] = None
    gpu_count: int = 1
    slice_compute_pct: Optional[int] = None
    cluster_id: Optional[str] = None
    engine: Optional[str] = None
    created_at: Optional[str] = None
    display_name: Optional[str] = None
    version: Dict[str, Any] = field(default_factory=dict)
    instances: List[Instance] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    # Per-deployment entry (addressed by the deployment name): the model it serves, and
    # whether /v1/models lists it (only when several deployments share the model name).
    deployment_name: Optional[str] = None
    model_name: Optional[str] = None
    listed: bool = True
    deployments: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def strategy(self) -> str:
        return str(self.traffic.get("strategy") or "least_sessions")

    @property
    def max_retries(self) -> int:
        try:
            return int(self.traffic.get("max_retries", 1))
        except (TypeError, ValueError):
            return 1

    @property
    def failover(self) -> bool:
        return bool(self.traffic.get("failover", True))

    @property
    def ready_instances(self) -> List[Instance]:
        return [i for i in self.instances if i.state == "ready"]

    @property
    def loading_instances(self) -> List[Instance]:
        return [i for i in self.instances if i.state in ("loading", "pending")]

    def speaks(self, fmt: str) -> bool:
        return fmt in self.native_formats


@dataclass
class RouteTarget:
    model: ModelEntry
    instance: Instance
    requested_name: str


def _parse_instance(raw: Dict[str, Any]) -> Instance:
    return Instance(
        instance_id=str(raw.get("instance_id") or ""),
        node_id=str(raw.get("node_id") or ""),
        proxy_url=str(raw.get("proxy_url") or ""),
        served_model_name=str(raw.get("served_model_name") or ""),
        engine=str(raw.get("engine") or "custom"),
        state=str(raw.get("state") or "ready"),
        health=str(raw.get("health") or "healthy"),
        accepting=bool(raw.get("accepting", True)),
        weight=int(raw.get("weight", 100) or 0),
        active_sessions=int(raw.get("active_sessions") or 0),
        waiting_sessions=int(raw.get("waiting_sessions") or 0),
        max_concurrent=raw.get("max_concurrent"),
        pd_role=raw.get("pd_role"),
        direct=bool(raw.get("direct", False)),
        deployment_id=raw.get("deployment_id"),
    )


def parse_model_entry(name: str, raw: Dict[str, Any]) -> ModelEntry:
    known = {
        "model_id",
        "type",
        "deployment_id",
        "family",
        "aliases",
        "priority",
        "desired_running",
        "traffic",
        "native_formats",
        "tool_parser",
        "context_size",
        "gpu_count",
        "slice_compute_pct",
        "cluster_id",
        "engine",
        "created_at",
        "display_name",
        "version",
        "instances",
        "deployment_name",
        "model_name",
        "listed",
        "deployments",
    }
    return ModelEntry(
        name=name,
        model_id=raw.get("model_id"),
        type=str(raw.get("type") or "chat"),
        deployment_id=raw.get("deployment_id"),
        family=raw.get("family"),
        aliases=list(raw.get("aliases") or []),
        priority=str(raw.get("priority") or "normal"),
        desired_running=bool(raw.get("desired_running", True)),
        traffic=dict(raw.get("traffic") or {}),
        native_formats=list(raw.get("native_formats") or ["openai"]),
        tool_parser=raw.get("tool_parser"),
        context_size=raw.get("context_size"),
        gpu_count=int(raw.get("gpu_count") or 1),
        slice_compute_pct=raw.get("slice_compute_pct"),
        cluster_id=raw.get("cluster_id"),
        engine=raw.get("engine"),
        created_at=raw.get("created_at"),
        display_name=raw.get("display_name"),
        version=dict(raw.get("version") or {}),
        instances=[_parse_instance(i) for i in raw.get("instances") or []],
        extra={k: v for k, v in raw.items() if k not in known},
        deployment_name=raw.get("deployment_name"),
        model_name=raw.get("model_name"),
        listed=bool(raw.get("listed", True)),
        deployments=list(raw.get("deployments") or []),
    )


class RoutingTable:
    """Process-wide routing state. ``load_from_dict`` replaces it atomically."""

    def __init__(self) -> None:
        self.version: int = 0
        self.generated_at: Optional[str] = None
        self.models: Dict[str, ModelEntry] = {}
        self.aliases: Dict[str, str] = {}
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.crl: Dict[str, Any] = {}
        self.ca_bundle_pem: Optional[str] = None
        self.loaded: bool = False
        self.loaded_at: float = 0.0
        self._ejected: Dict[str, float] = {}
        self._rr: Dict[str, int] = {}
        self._listeners: List[Any] = []

    # --- loading -------------------------------------------------------------

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        models: Dict[str, ModelEntry] = {}
        aliases: Dict[str, str] = {}
        for name, raw in (data.get("models") or {}).items():
            entry = parse_model_entry(name, raw or {})
            models[name.lower()] = entry
            for alias in entry.aliases:
                aliases[str(alias).lower()] = name.lower()
        self.models = models
        self.aliases = aliases
        self.nodes = dict(data.get("nodes") or {})
        self.crl = dict(data.get("crl") or {})
        self.ca_bundle_pem = data.get("ca_bundle_pem")
        self.version = int(data.get("version") or 0)
        self.generated_at = data.get("generated_at")
        self.loaded = True
        self.loaded_at = time.monotonic()
        for cb in list(self._listeners):
            try:
                cb(self)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"routing listener failed: {exc}")

    def load_from_json(self, raw: str) -> None:
        self.load_from_dict(json.loads(raw))

    def on_update(self, callback) -> None:
        self._listeners.append(callback)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "models": {
                m.name: {
                    "model_id": m.model_id,
                    "type": m.type,
                    "deployment_id": m.deployment_id,
                    "aliases": m.aliases,
                    "instances": [i.__dict__ for i in m.instances],
                }
                for m in self.models.values()
            },
            "nodes": self.nodes,
        }

    # --- lookups -------------------------------------------------------------

    def get(self, name: str) -> Optional[ModelEntry]:
        if not name:
            return None
        key = name.lower()
        entry = self.models.get(key)
        if entry is None:
            target = self.aliases.get(key)
            if target:
                entry = self.models.get(target)
        if entry is None:
            # Dev engine wildcard: a synthetic "*" entry answers to any model name.
            entry = self.models.get("*")
            if entry is not None:
                entry = _wildcard_copy(entry, name)
        return entry

    def list_models(self, include_unloaded: bool = False) -> List[ModelEntry]:
        out = []
        for entry in self.models.values():
            if entry.name == "*" or not entry.listed:
                continue
            if entry.ready_instances or include_unloaded:
                out.append(entry)
        return sorted(out, key=lambda e: e.name)

    def online_node_count(self) -> int:
        if self.nodes:
            # Connected nodes: drain is a scheduling state (a draining node keeps serving).
            connected = ("online", "draining", "drained")
            return sum(1 for n in self.nodes.values() if (n or {}).get("status") in connected)
        return sum(1 for m in self.models.values() for i in m.instances if i.selectable)

    # --- ejection ------------------------------------------------------------

    def eject(self, instance_id: str, seconds: Optional[int] = None) -> None:
        ttl = settings.SERVING_EJECT_SECONDS if seconds is None else seconds
        self._ejected[instance_id] = time.monotonic() + ttl
        logger.warning(f"instance {instance_id} ejected from routing for {ttl}s")

    def is_ejected(self, instance_id: str) -> bool:
        until = self._ejected.get(instance_id)
        if until is None:
            return False
        if until <= time.monotonic():
            self._ejected.pop(instance_id, None)
            return False
        return True

    # --- selection -----------------------------------------------------------

    def candidates(self, entry: ModelEntry, exclude: Iterable[str] = ()) -> List[Instance]:
        skip = set(exclude)
        return [
            i
            for i in entry.instances
            if i.selectable and i.instance_id not in skip and not self.is_ejected(i.instance_id)
        ]

    def pick(
        self, entry: ModelEntry, inflight: Dict[str, int], exclude: Iterable[str] = ()
    ) -> Optional[Instance]:
        cands = self.candidates(entry, exclude)
        if not cands:
            return None
        strategy = entry.strategy
        if strategy == "round_robin":
            idx = self._rr.get(entry.name, 0)
            self._rr[entry.name] = idx + 1
            return cands[idx % len(cands)]
        if strategy == "weighted":
            total = sum(max(i.weight, 0) for i in cands)
            if total <= 0:
                return cands[0]
            r = random.uniform(0, total)
            acc = 0.0
            for i in cands:
                acc += max(i.weight, 0)
                if r <= acc:
                    return i
            return cands[-1]

        # least_sessions (default): live sessions + our in-flight, scaled by weight
        # so weight still biases the choice among equally loaded instances.
        def load(i: Instance) -> float:
            sessions = i.active_sessions + i.waiting_sessions + inflight.get(i.instance_id, 0)
            return (sessions + 1) / max(i.weight, 1)

        if settings.SERVING_LATENCY_ROUTING and len(cands) > 1:
            nearest = self._pick_nearest(cands, inflight, load)
            if nearest is not None:
                return nearest
        best = min(cands, key=load)
        return best

    def rtt_of(self, node_id: str) -> Optional[float]:
        """This API layer's smoothed RTT to a node in ms (None = not measured)."""
        return latency_rtt_ms(node_id)

    def expected_latency_ms(self, inst: Instance, rtt: float, inflight: Dict[str, int]) -> float:
        sessions = inst.active_sessions + inst.waiting_sessions + inflight.get(inst.instance_id, 0)
        capacity = inst.max_concurrent or settings.SERVING_LATENCY_DEFAULT_CAPACITY
        share = max(inst.weight, 1) / 100.0
        penalty = float(settings.SERVING_LATENCY_LOAD_PENALTY_MS) * sessions
        return rtt + penalty / max(int(capacity or 1), 1) / share

    def _pick_nearest(
        self, cands: List[Instance], inflight: Dict[str, int], load
    ) -> Optional[Instance]:
        """Lowest expected latency (RTT + load penalty) within the tolerance band; None
        when no candidate's node has an RTT measurement."""
        rtts = {i.instance_id: self.rtt_of(i.node_id) for i in cands}
        known = [v for v in rtts.values() if v is not None]
        if not known:
            return None
        worst = max(known)
        scored = []
        for inst in cands:
            rtt = rtts[inst.instance_id]
            scored.append(
                (self.expected_latency_ms(inst, worst if rtt is None else rtt, inflight), inst)
            )
        best = min(score for score, _ in scored)
        band = [i for score, i in scored if score <= best + settings.SERVING_LATENCY_TOLERANCE_MS]
        if len(band) == 1:
            return band[0]
        lightest = min(load(i) for i in band)
        ties = [i for i in band if load(i) <= lightest + 1e-9]
        if len(ties) == 1:
            return ties[0]
        return random.choices(ties, weights=[max(i.weight, 1) for i in ties])[0]

    def resolve(self, requested: str, endpoint: str, inflight: Dict[str, int]) -> RouteTarget:
        """Resolve a model name for an endpoint to one instance, or raise ServingError."""
        entry = self.get(requested)
        if entry is None:
            raise ServingError.model_not_found(requested)
        allowed = ENDPOINT_MODEL_TYPES.get(endpoint)
        if allowed is not None and entry.type not in allowed:
            raise ServingError.model_type_mismatch(requested, entry.type, endpoint)
        instance = self.pick(entry, inflight)
        if instance is None:
            if entry.ready_instances:
                raise ServingError.no_instance_available(requested)
            raise ServingError.model_not_ready(requested)
        return RouteTarget(model=entry, instance=instance, requested_name=requested)


def _wildcard_copy(entry: ModelEntry, name: str) -> ModelEntry:
    copy = ModelEntry(**{**entry.__dict__})
    copy.name = name
    copy.instances = [
        Instance(**{**i.__dict__, "served_model_name": i.served_model_name or name})
        for i in entry.instances
    ]
    return copy


def dev_engine_table() -> Optional[Dict[str, Any]]:
    """Synthetic routing table for SERVING_DEV_ENGINE_URL (local testing without agents)."""
    url = settings.SERVING_DEV_ENGINE_URL
    if not url:
        return None
    models: Dict[str, Any] = {}
    for name in settings.dev_engine_models() or ["*"]:
        models[name] = {
            "model_id": None,
            "type": settings.SERVING_DEV_ENGINE_TYPE,
            "deployment_id": None,
            "family": settings.SERVING_DEV_ENGINE_FAMILY,
            "aliases": [],
            "traffic": {
                "strategy": "least_sessions",
                "failover": False,
                "max_retries": 0,
            },
            "native_formats": settings.dev_engine_formats(),
            "engine": "custom",
            "gpu_count": 1,
            "instances": [
                {
                    "instance_id": f"dev-{name}",
                    "node_id": "dev",
                    "proxy_url": url,
                    "served_model_name": "" if name == "*" else name,
                    "engine": "custom",
                    "state": "ready",
                    "health": "healthy",
                    "accepting": True,
                    "weight": 100,
                    "direct": True,
                }
            ],
        }
    return {
        "version": int(time.time()),
        "generated_at": None,
        "models": models,
        "nodes": {"dev": {"name": "dev-engine", "proxy_url": url, "status": "online"}},
        "crl": {},
        "ca_bundle_pem": None,
    }


_table: Optional[RoutingTable] = None


def get_routing_table() -> RoutingTable:
    global _table
    if _table is None:
        _table = RoutingTable()
    return _table


class RoutingRefresher:
    """Loads the table from Redis, subscribes to updates and polls as a fallback."""

    def __init__(self, table: RoutingTable) -> None:
        self.table = table
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def load_once(self) -> bool:
        dev = dev_engine_table()
        if dev is not None:
            self.table.load_from_dict(dev)
            return True
        from app.db.redis.client import get_redis, redis_key

        try:
            client = await get_redis()
            raw = await client.get(redis_key(*ROUTING_KEY_PARTS))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"routing table load failed: {exc}")
            return False
        if raw:
            try:
                self.table.load_from_json(raw)
                return True
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error(f"routing table JSON invalid: {exc}")
                return False
        if not self.table.loaded:
            # No control plane push yet — start empty but "loaded" so health reports the state.
            self.table.load_from_dict({"version": 0, "models": {}, "nodes": {}})
        return False

    async def start(self) -> None:
        await self.load_once()
        if dev_engine_table() is not None:
            return
        self._task = asyncio.create_task(self._run(), name="routing-refresher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        from app.db.redis.client import get_redis, redis_key

        channel = redis_key(*ROUTING_CHANNEL_PARTS)
        poll = max(1, settings.ROUTING_TABLE_POLL_SECONDS)
        pubsub = None
        while not self._stop.is_set():
            try:
                if pubsub is None:
                    client = await get_redis()
                    pubsub = client.pubsub()
                    await pubsub.subscribe(channel)
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll)
                if message is not None:
                    await self.load_once()
                elif time.monotonic() - self.table.loaded_at >= poll:
                    await self.load_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"routing refresher error: {exc}")
                pubsub = None
                await asyncio.sleep(poll)
        if pubsub is not None:
            try:
                await pubsub.close()
            except Exception:  # noqa: BLE001
                pass
