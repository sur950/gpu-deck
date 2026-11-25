"""
Round-trip time statistics for one network path (pure, no I/O).

``RttStats`` keeps an exponentially weighted moving average (EWMA) of the samples, a jitter
estimate in the spirit of RFC 3550 (smoothed absolute difference between consecutive
samples) and the most recent samples for min / max / p50 / p95. It is shared by the API
layer (probes to every node over the path requests use) and the agent hub (WebSocket
ping / pong with every agent).
"""

from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional

from app.core.logger import get_logger

logger = get_logger(__name__)

RECENT_SAMPLES = 60


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile (``pct`` in 0..100); None for no values."""
    data: List[float] = sorted(float(v) for v in values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    rank = (len(data) - 1) * max(0.0, min(100.0, pct)) / 100.0
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return data[lo]
    return data[lo] + (data[hi] - data[lo]) * (rank - lo)


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 3)


class RttStats:
    """Smoothed RTT, jitter and recent samples of one path."""

    __slots__ = (
        "alpha",
        "ewma_ms",
        "jitter_ms",
        "last_ms",
        "samples",
        "failures",
        "consecutive_failures",
        "updated_at",
        "recent",
    )

    def __init__(self, alpha: float = 0.3) -> None:
        self.alpha = min(1.0, max(0.01, float(alpha)))
        self.ewma_ms: Optional[float] = None
        self.jitter_ms: float = 0.0
        self.last_ms: Optional[float] = None
        self.samples = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.updated_at: float = 0.0
        self.recent: Deque[float] = deque(maxlen=RECENT_SAMPLES)

    def add(self, rtt_ms: float, now: Optional[float] = None) -> None:
        """Record one round trip in milliseconds."""
        rtt = max(0.0, float(rtt_ms))
        if self.ewma_ms is None:
            self.ewma_ms = rtt
        else:
            self.ewma_ms = self.alpha * rtt + (1.0 - self.alpha) * self.ewma_ms
        if self.last_ms is not None:
            delta = abs(rtt - self.last_ms)
            self.jitter_ms += (delta - self.jitter_ms) * self.alpha
        self.last_ms = rtt
        self.samples += 1
        self.consecutive_failures = 0
        self.recent.append(rtt)
        self.updated_at = time.time() if now is None else now

    def fail(self) -> None:
        """A probe that got no answer (the smoothed value is kept until it goes stale)."""
        self.failures += 1
        self.consecutive_failures += 1

    def fresh(self, max_age_seconds: float, now: Optional[float] = None) -> bool:
        if self.ewma_ms is None or self.updated_at <= 0:
            return False
        current = time.time() if now is None else now
        return current - self.updated_at <= max_age_seconds

    def snapshot(self) -> Dict[str, Any]:
        """JSON-ready view (milliseconds, rounded to µs)."""
        recent = list(self.recent)
        return {
            "rtt_ms": _round(self.ewma_ms),
            "jitter_ms": _round(self.jitter_ms if self.samples > 1 else None),
            "last_ms": _round(self.last_ms),
            "min_ms": _round(min(recent) if recent else None),
            "max_ms": _round(max(recent) if recent else None),
            "p50_ms": _round(percentile(recent, 50)),
            "p95_ms": _round(percentile(recent, 95)),
            "samples": self.samples,
            "failures": self.failures,
            "updated_at": (
                datetime.fromtimestamp(self.updated_at, timezone.utc).isoformat()
                if self.updated_at
                else None
            ),
            "ts": self.updated_at or None,
        }
