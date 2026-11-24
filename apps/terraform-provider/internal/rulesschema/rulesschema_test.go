package rulesschema

import (
	"strings"
	"testing"
)

func TestTypesCoverAllRuleTypes(t *testing.T) {
	want := []string{"auto_heal", "high_load_grow", "idle_unload", "low_traffic_shrink", "make_room_by_priority", "time_schedule"}
	got := Types()
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("types %v, want %v", got, want)
	}
}

func TestValidateParams(t *testing.T) {
	if err := ValidateParams("idle_unload", `{"idle_minutes": 30}`); err != nil {
		t.Fatalf("valid params rejected: %v", err)
	}
	for name, doc := range map[string]string{
		"below minimum":   `{"idle_minutes": 0}`,
		"missing":         `{}`,
		"unknown field":   `{"idle_minutes": 5, "bogus": true}`,
		"bad time window": `{"idle_minutes": 5, "respect_time_windows": ["25:00-26:00"]}`,
	} {
		if err := ValidateParams("idle_unload", doc); err == nil {
			t.Errorf("%s: expected an error for %s", name, doc)
		}
	}
	if err := ValidateParams("nope", `{}`); err == nil || !strings.Contains(err.Error(), "unknown rule type") {
		t.Fatalf("unknown type: %v", err)
	}
	if err := ValidateParams("idle_unload", `{`); err == nil {
		t.Fatal("invalid JSON accepted")
	}
}
