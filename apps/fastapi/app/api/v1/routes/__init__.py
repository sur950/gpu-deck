"""
Admin API routes — one file per resource, auto-discovered (see app/api/router_registry.py).

Each file defines ``router = APIRouter()`` with full resource paths in the
decorators and the decorator stack ``@router.X -> @rate_limit -> @handle_exceptions``.
Routes handle HTTP only; DB, Redis and business logic live in services.
"""
