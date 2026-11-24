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
