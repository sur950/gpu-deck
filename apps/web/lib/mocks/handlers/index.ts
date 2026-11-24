/**
 * Mock handler manifest — every `lib/mocks/handlers/*.ts` file is loaded.
 *
 * Adding a feature's mocks: create `lib/mocks/handlers/<feature>.ts` that
 * calls `registerMockHandlers('<feature>', [...])` at module level. Nothing
 * else to edit — this file discovers it (Turbopack `import.meta.glob`, eager,
 * so registration runs as soon as this module is evaluated).
 *
 * Load order does not matter: the dashboards' read-only stand-ins for
 * resources another feature owns (`GET /nodes`, `/deployments`, `/swaps`, …)
 * are registered with `fallback: true`, and any owning handler for the same
 * request wins over them (see registry.ts).
 *
 * Fixtures shared across features live in `lib/mocks/fixtures/` — import the
 * fleet (`IDS`, `NODES`, `DEVICES`, `DEPLOYMENTS`, `INSTANCES`) from
 * `fixtures/fleet.ts` so ids line up between screens.
 */

const modules = import.meta.glob(['./*.ts', '!./index.ts'], { eager: true });

/** Handler files that were loaded (for the console banner and debugging). */
export const MOCK_HANDLER_FILES: string[] = Object.keys(modules).map((key) => key.replace(/^\.\//, '').replace(/\.ts$/, ''));
