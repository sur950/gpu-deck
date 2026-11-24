"""
Serving API (data plane) routes.

One sub-package per wire format; each package's ``__init__`` declares the
explicit mount ``PREFIX`` the router registry applies to every module inside
it (serving.md §1, resolved by ``app.services.serving.formats``):

    openai/     -> /openai/v1     (bare compat: /v1)
    anthropic/  -> /anthropic/v1  (bare compat: /v1, /v1/messages* only)
    ollama/     -> /ollama/api    (bare compat: /api)

``app/api_layer_main.py`` mounts every router a second time at the bare
prefix when ``SERVING_ENABLE_BARE_PATHS`` (modules with ``BARE = False`` are
skipped; the shared bare ``/v1/models`` is dispatched by ``_compat.py``).

Routes authenticate with an API key (Authorization: Bearer / X-API-Key /
x-api-key), never with the admin session, and do NOT use @handle_exceptions:
``app.services.serving.endpoint.serve`` renders errors in the format's own
error shape.
"""
