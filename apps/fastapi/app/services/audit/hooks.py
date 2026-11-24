"""
Audit lifecycle hooks.

On shutdown, wait for audit events still being handed to the broker (they are
enqueued on worker threads off the request path) so a graceful stop does not
drop the last few entries.
"""

from app.core.lifecycle import on_shutdown
from app.core.logger import get_logger
from app.services.audit import audit

logger = get_logger(__name__)


@on_shutdown("admin", "serving")
async def drain_audit_queue() -> None:
    await audit.drain()
