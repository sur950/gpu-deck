"""
Audit domain.

    from app.services.audit import audit
    audit.record("node.joined", resource_type="node", resource_id=node.id, ...)
"""

from app.services.audit.service import AuditService, audit, write_audit_event

__all__ = ["audit", "AuditService", "write_audit_event"]
