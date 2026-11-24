---
page_title: "gpudeck_rule Resource"
---

# gpudeck_rule (Resource)

Automation rule (idle unload, time schedule, low-traffic shrink, high-load grow, make room by priority, auto-heal). `params` is validated against the rule schema at plan time.

## Example Usage

```terraform
resource "gpudeck_rule" "idle_unload" {
  name = "unload idle models"
  type = "idle_unload"
  params = jsonencode({
    idle_minutes       = 30
    min_loaded_minutes = 10
  })
  target = jsonencode({
    priorities = ["evictable"]
  })
  cooldown_seconds = 600
}

# Switch to the night layout at 20:00 every day (India time).
resource "gpudeck_rule" "night" {
  name = "night layout"
  type = "time_schedule"
  params = jsonencode({
    layout_id = gpudeck_layout.night.id
    at        = "20:00"
    timezone  = "Asia/Kolkata"
  })
}
```

## Schema

### Required

- `name` (String) Rule name.
- `params` (String, JSON) Parameters for the rule type (libs/contracts/rules/<type>.schema.json). JSON-encoded.
- `type` (String) Rule type. One of: `idle_unload`, `time_schedule`, `low_traffic_shrink`, `high_load_grow`, `make_room_by_priority`, `auto_heal`. Changing this replaces the resource.

### Optional

- `cooldown_seconds` (Number) Minimum seconds between firings. Server default: `300`.
- `description` (String) Free-form description.
- `dry_run` (Boolean) Record what the rule would do without acting. Server default: `false`.
- `enabled` (Boolean) Evaluate the rule. Server default: `true`.
- `evaluation_interval_seconds` (Number) Evaluation interval. Server default: `30`.
- `priority` (Number) Lower runs first when rules conflict. Server default: `100`.
- `target` (String, JSON) Target selector (deployments, models, nodes, labels). JSON-encoded (use `jsonencode`).

### Read-Only

- `created_at` (String) Creation time (RFC 3339).
- `fire_count` (Number) Times fired.
- `id` (String) Object UUID.
- `last_evaluated_at` (String) Last evaluation.
- `last_fired_at` (String) Last firing.
- `updated_at` (String) Last update time (RFC 3339).

## Import

```shell
# Import by UUID.
terraform import gpudeck_rule.example 00000000-0000-0000-0000-000000000000
```

Attributes equal to their server default are left unset after import.
