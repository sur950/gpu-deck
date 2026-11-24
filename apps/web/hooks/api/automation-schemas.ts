/**
 * Bundled copy of the rule contract schemas (libs/contracts/rules/*.json).
 *
 * The rule builder renders its forms from these documents. `GET /rules/schemas`
 * serves the same files; the hook merges the server copy over this bundle so
 * the builder works offline / in mock mode and picks up contract updates
 * without a redeploy.
 */

import type { JsonSchema, RuleSchemas, RuleType } from '@/hooks/api/automation-api';
import ruleSchema from '../../../../libs/contracts/rules/rule.schema.json';
import targetSelectorSchema from '../../../../libs/contracts/rules/target-selector.schema.json';
import layoutSchema from '../../../../libs/contracts/rules/layout.schema.json';
import idleUnload from '../../../../libs/contracts/rules/idle_unload.schema.json';
import timeSchedule from '../../../../libs/contracts/rules/time_schedule.schema.json';
import lowTrafficShrink from '../../../../libs/contracts/rules/low_traffic_shrink.schema.json';
import highLoadGrow from '../../../../libs/contracts/rules/high_load_grow.schema.json';
import makeRoomByPriority from '../../../../libs/contracts/rules/make_room_by_priority.schema.json';
import autoHeal from '../../../../libs/contracts/rules/auto_heal.schema.json';

export const BUNDLED_RULE_SCHEMAS: RuleSchemas = {
  rule: ruleSchema as unknown as JsonSchema,
  targetSelector: targetSelectorSchema as unknown as JsonSchema,
  layout: layoutSchema as unknown as JsonSchema,
  params: {
    idle_unload: idleUnload as unknown as JsonSchema,
    time_schedule: timeSchedule as unknown as JsonSchema,
    low_traffic_shrink: lowTrafficShrink as unknown as JsonSchema,
    high_load_grow: highLoadGrow as unknown as JsonSchema,
    make_room_by_priority: makeRoomByPriority as unknown as JsonSchema,
    auto_heal: autoHeal as unknown as JsonSchema,
  } satisfies Record<RuleType, JsonSchema>,
};

/** Server copy over the bundle; a missing or empty server section keeps the bundled one. */
export function mergeRuleSchemas(server: RuleSchemas | undefined): RuleSchemas {
  if (!server) return BUNDLED_RULE_SCHEMAS;
  const params = { ...BUNDLED_RULE_SCHEMAS.params };
  for (const [type, schema] of Object.entries(server.params ?? {})) {
    if (schema && schema.properties) params[type] = schema;
  }
  return {
    rule: server.rule?.properties ? server.rule : BUNDLED_RULE_SCHEMAS.rule,
    targetSelector: server.targetSelector?.properties ? server.targetSelector : BUNDLED_RULE_SCHEMAS.targetSelector,
    layout: server.layout?.properties ? server.layout : BUNDLED_RULE_SCHEMAS.layout,
    params,
  };
}

/** Default params for a type, straight from the schema `default` values. */
export function schemaDefaults(schema: JsonSchema | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, prop] of Object.entries(schema?.properties ?? {})) {
    if (prop.default !== undefined) out[key] = prop.default;
    else if (prop.const !== undefined) out[key] = prop.const;
  }
  return out;
}
