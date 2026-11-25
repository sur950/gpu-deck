"""
Admin routes deferred to v2: metering, billing, price plans and traces.

These modules are not mounted. ``app/api/router_registry.py`` only walks ``app/api/v1/routes``,
so nothing here reaches the v1 admin API, ``libs/contracts/openapi/admin.yaml`` or the CLI.
The services, tables, migrations and Celery tasks behind them stay in place (usage events are
still recorded on the serving path), and ``tests/integration/test_metering_billing.py`` mounts
these routers on a test app so the code keeps working. To ship them in v2, move a module back to
``app/api/v1/routes`` and restore its paths in admin.yaml.
"""
