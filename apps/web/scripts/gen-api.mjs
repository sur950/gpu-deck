#!/usr/bin/env node
/**
 * Generate the typed admin API client from the shared OpenAPI contract.
 *
 *   pnpm gen:api            write lib/api/generated/admin.ts
 *   pnpm gen:api --check    exit 1 when the committed file is stale (CI)
 *
 * Reads  ../../libs/contracts/openapi/admin.yaml
 * Writes lib/api/generated/admin.ts (committed, imported by lib/api/*.ts)
 *
 * When the contract does not exist yet the placeholder in
 * lib/api/generated/admin.ts is left untouched and the script exits 0, so
 * `pnpm build` never depends on the contract being present.
 *
 * Request bodies and defaults: openapi-typescript treats a property with a
 * schema `default` as always present (`defaultNonNullable`). That is right for
 * responses (the server fills the default) but wrong for request bodies — a
 * PATCH `DeploymentUpdate` would have to send `replicas`, `gpu_count`, … on
 * every call and silently reset them. For component schemas that are only ever
 * used as request input (request bodies / parameters, never in a response),
 * defaulted properties that the schema does not list in `required` are emitted
 * as optional. Response shapes are unchanged.
 */
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, '..');
const spec = resolve(appRoot, '../../libs/contracts/openapi/admin.yaml');
const out = resolve(appRoot, 'lib/api/generated/admin.ts');
const checkOnly = process.argv.includes('--check');

if (!existsSync(spec)) {
  console.warn(`[gen:api] ${spec} not found — keeping the placeholder at lib/api/generated/admin.ts`);
  process.exit(0);
}

const require = createRequire(import.meta.url);
const { default: openapiTS, astToString } = await import('openapi-typescript');
const ts = require('typescript');
// openapi-typescript's own YAML parser (a dependency of it, not of this app).
const { parseYaml } = createRequire(require.resolve('openapi-typescript'))('@redocly/openapi-core');

const SCHEMA_PREFIX = '#/components/schemas/';

/** Names of component schemas referenced (directly or through other schemas) from `node`. */
function collectRefs(node, into) {
  if (Array.isArray(node)) {
    for (const item of node) collectRefs(item, into);
  } else if (node && typeof node === 'object') {
    for (const [key, value] of Object.entries(node)) {
      if (key === '$ref' && typeof value === 'string' && value.startsWith(SCHEMA_PREFIX)) into.add(value.slice(SCHEMA_PREFIX.length));
      else collectRefs(value, into);
    }
  }
  return into;
}

function closure(names, schemas) {
  const seen = new Set();
  const stack = [...names];
  while (stack.length) {
    const name = stack.pop();
    if (seen.has(name)) continue;
    seen.add(name);
    for (const ref of collectRefs(schemas[name] ?? {}, new Set())) stack.push(ref);
  }
  return seen;
}

/** Component schemas used only as request input (never inside a response). */
function requestOnlySchemas(doc) {
  const schemas = doc.components?.schemas ?? {};
  const inputs = new Set();
  const outputs = collectRefs(doc.components?.responses ?? {}, new Set());
  for (const item of Object.values(doc.paths ?? {})) {
    for (const op of Object.values(item ?? {})) {
      if (!op || typeof op !== 'object' || Array.isArray(op)) continue;
      collectRefs(op.requestBody, inputs);
      collectRefs(op.parameters, inputs);
      collectRefs(op.responses, outputs);
    }
  }
  const outputClosure = closure(outputs, schemas);
  return new Set([...closure(inputs, schemas)].filter((name) => !outputClosure.has(name)));
}

/** `required` of a component schema, including inline allOf / anyOf / oneOf members. */
function requiredOf(schema) {
  if (!schema || typeof schema !== 'object') return [];
  const members = ['allOf', 'anyOf', 'oneOf'].flatMap((k) => (Array.isArray(schema[k]) ? schema[k] : []));
  return [...(schema.required ?? []), ...members.flatMap((m) => (m && !m.$ref ? requiredOf(m) : []))];
}

const source = await readFile(spec, 'utf8');
const doc = parseYaml(source);
const inputOnly = requestOnlySchemas(doc);

const ast = await openapiTS(doc, {
  // snake_case stays as the contract defines it; lib/api/*.ts maps to camelCase.
  alphabetize: true,
  exportType: true,
  // Free-form objects (labels, node_selector, launch_flags…) are `Record<string, unknown>`, not `Record<string, never>`.
  emptyObjectsUnknown: true,
  transformProperty(property, schemaObject, options) {
    if (property.questionToken || !schemaObject || typeof schemaObject !== 'object' || !('default' in schemaObject)) return undefined;
    const path = options.path ?? '';
    if (!path.startsWith(SCHEMA_PREFIX)) return undefined;
    // Only direct properties of a component schema: #/components/schemas/<Name>/<prop>
    const parts = path.slice(SCHEMA_PREFIX.length).split('/');
    if (parts.length !== 2 || !inputOnly.has(parts[0])) return undefined;
    const required = requiredOf(doc.components.schemas[parts[0]]);
    if (required.includes(parts[1].replace(/~1/g, '/').replace(/~0/g, '~'))) return undefined;
    return ts.factory.updatePropertySignature(property, property.modifiers, property.name, ts.factory.createToken(ts.SyntaxKind.QuestionToken), property.type);
  },
});

const header = [
  '/**',
  ' * GENERATED FILE — do not edit by hand.',
  ' * Source: libs/contracts/openapi/admin.yaml',
  ' * Regenerate with `pnpm gen:api` (apps/web).',
  ' */',
  '',
].join('\n');

const generated = header + astToString(ast);

if (checkOnly) {
  const current = existsSync(out) ? await readFile(out, 'utf8') : '';
  if (current !== generated) {
    console.error('[gen:api] lib/api/generated/admin.ts is out of date with libs/contracts/openapi/admin.yaml — run `pnpm gen:api`');
    process.exit(1);
  }
  console.log('[gen:api] lib/api/generated/admin.ts is up to date');
  process.exit(0);
}

await writeFile(out, generated, 'utf8');
console.log(`[gen:api] wrote ${out} (${inputOnly.size} request-only schemas: defaulted fields optional)`);
