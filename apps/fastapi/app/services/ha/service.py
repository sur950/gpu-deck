"""
High availability for the jobs process: Postgres lease leader election.

Leases (``ha_leases`` rows, see 001_init):

- ``jobs-leader``      — the swap manager drives swap operations only here.
- ``rules-evaluator``  — the rules tick (and time_schedule / prefetch) runs only here.

Protocol
--------
Every jobs process runs the ``ha.leases`` job every ``HA_LEASE_RENEW_SECONDS``
(10 s). It calls ``try_acquire`` for each lease: one atomic UPDATE that takes
the row when it is free or expired, or extends it when this process already
holds it. A successful *acquire* increments ``version``; that number is the
**fencing token**. A holder that stops renewing (crash, network partition,
GC pause) loses the lease after ``HA_LEASE_TTL_SECONDS`` (30 s) and another
replica takes it with ``version + 1``.

Fencing
-------
Holding the lease in memory is not enough: a paused ex-leader could wake up
and write. So every durable write of the swap manager is conditional on the
lease still being ours *with the same token* (``LeaseFence.sql_condition`` is
appended to the swap UPDATE; ``LeaseFence.check`` is consulted between swap
steps). A stale writer's UPDATE matches zero rows and its executor stops; the
new leader resumes the swap from the persisted step (``swap_operations``).
The rules evaluator checks its fence before every rule.

Multi-replica behaviour
-----------------------
Run any number of jobs processes (``python -m app.workers.scheduler``). All of
them run the lease job; exactly one holds each lease at a time. Non-leaders
skip ``rules.tick`` and ``swap.drive`` (they log at debug level). Admin API
replicas never take leases: their API calls only create rows (rules, swaps,
layouts) that the leader picks up. Failover time is at most TTL + one renew
interval (≈ 40 s by default). Agent commands are idempotent on their id, so a
swap step resumed by a new leader re-waits for the same command instead of
sending a duplicate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.ha.automation_metrics import bind_leader_gauge

logger = get_logger(__name__)

JOBS_LEADER = "jobs-leader"
RULES_EVALUATOR = "rules-evaluator"
MANAGED_LEASES = (JOBS_LEADER, RULES_EVALUATOR)


@dataclass
class HeldLease:
    name: str
    holder_id: str
    version: int
    renewed_monotonic: float

    def fresh(self, ttl_seconds: int) -> bool:
        return (time.monotonic() - self.renewed_monotonic) < ttl_seconds


class LeaderState:
    """Process-local view of the leases this process holds (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: Dict[str, HeldLease] = {}

    def set(self, lease: HeldLease) -> None:
        with self._lock:
            self._held[lease.name] = lease

    def drop(self, name: str) -> None:
        with self._lock:
            self._held.pop(name, None)

    def get(self, name: str) -> Optional[HeldLease]:
        with self._lock:
            return self._held.get(name)

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._held)

    def clear(self) -> None:
        with self._lock:
            self._held.clear()


leader_state = LeaderState()


def _holds_jobs_leader() -> bool:
    lease = leader_state.get(JOBS_LEADER)
    return lease is not None and lease.fresh(int(settings.HA_LEASE_TTL_SECONDS))


bind_leader_gauge(_holds_jobs_leader)

SessionFactory = Callable[[], Any]


def _default_session_factory():
    from app.db.postgres.connection import get_session_maker

    return get_session_maker()()


class LeaseManager:
    """Acquire / renew / release leases for one holder id."""

    def __init__(
        self,
        holder_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        session_factory: Optional[SessionFactory] = None,
        state: Optional[LeaderState] = None,
    ) -> None:
        self.holder_id = holder_id or settings.HA_INSTANCE_ID
        self.ttl = int(ttl_seconds or settings.HA_LEASE_TTL_SECONDS)
        self.session_factory = session_factory or _default_session_factory
        self.state = state if state is not None else leader_state

    async def acquire_or_renew(self, name: str) -> Optional[int]:
        """Take or extend ``name``. Returns the fencing token, or None if someone else holds it."""
        from app.db.postgres.repositories.ha_lease_repository import HaLeaseRepository

        previous = self.state.get(name)
        try:
            async with self.session_factory() as session:
                version = await HaLeaseRepository(session).try_acquire(
                    name, self.holder_id, self.ttl
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - a DB blip must drop leadership, not crash
            logger.warning(f"ha: lease {name} acquire/renew failed: {exc}")
            self.state.drop(name)
            return None
        if version is None:
            if previous is not None:
                logger.warning(f"ha: lost lease {name} (holder {self.holder_id})")
            self.state.drop(name)
            return None
        if previous is None or previous.version != version:
            logger.info(f"ha: acquired lease {name} token={version} holder={self.holder_id}")
        self.state.set(HeldLease(name, self.holder_id, version, time.monotonic()))
        return version

    async def release(self, name: str) -> bool:
        from app.db.postgres.repositories.ha_lease_repository import HaLeaseRepository

        self.state.drop(name)
        try:
            async with self.session_factory() as session:
                released = await HaLeaseRepository(session).release(name, self.holder_id)
                await session.commit()
            if released:
                logger.info(f"ha: released lease {name}")
            return released
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ha: release of {name} failed: {exc}")
            return False

    async def tick(self, names=MANAGED_LEASES) -> Dict[str, Optional[int]]:
        return {name: await self.acquire_or_renew(name) for name in names}

    def is_leader(self, name: str) -> bool:
        """Fast in-memory check (the lease was renewed within the TTL)."""
        lease = self.state.get(name)
        return lease is not None and lease.fresh(self.ttl)

    def fence(self, name: str) -> Optional["LeaseFence"]:
        lease = self.state.get(name)
        if lease is None or not lease.fresh(self.ttl):
            return None
        return LeaseFence(name, lease.holder_id, lease.version, self.session_factory)

    async def status(self) -> List[Dict[str, Any]]:
        from app.db.postgres.repositories.ha_lease_repository import HaLeaseRepository

        out = []
        async with self.session_factory() as session:
            repo = HaLeaseRepository(session)
            for name in MANAGED_LEASES:
                row = await repo.current(name)
                if row is not None:
                    row["mine"] = row.get("holder_id") == self.holder_id
                    out.append(row)
        return out


class LeaseFence:
    """A fencing token: still valid only while (name, holder, version) holds an unexpired lease."""

    def __init__(
        self, name: str, holder_id: str, version: int, session_factory: SessionFactory
    ) -> None:
        self.name = name
        self.holder_id = holder_id
        self.version = version
        self.session_factory = session_factory

    def sql_condition(self) -> str:
        """SQL predicate for a fenced UPDATE (bind :fence_name, :fence_holder, :fence_version)."""
        return (
            "EXISTS (SELECT 1 FROM ha_leases WHERE name = :fence_name "
            "AND holder_id = :fence_holder AND version = :fence_version AND expires_at >= now())"
        )

    def params(self) -> Dict[str, Any]:
        return {
            "fence_name": self.name,
            "fence_holder": self.holder_id,
            "fence_version": self.version,
        }

    async def check(self) -> bool:
        from app.db.postgres.repositories.ha_lease_repository import HaLeaseRepository

        try:
            async with self.session_factory() as session:
                return await HaLeaseRepository(session).is_held_by(
                    self.name, self.holder_id, self.version
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ha: fence check for {self.name} failed: {exc}")
            return False

    def as_json(self) -> Dict[str, Any]:
        return {"lease": self.name, "holder_id": self.holder_id, "token": self.version}


_manager: Optional[LeaseManager] = None


def get_lease_manager() -> LeaseManager:
    global _manager
    if _manager is None:
        _manager = LeaseManager()
    return _manager
