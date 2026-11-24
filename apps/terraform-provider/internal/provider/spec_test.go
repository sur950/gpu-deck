package provider

import (
	"testing"

	"github.com/hashicorp/terraform-plugin-framework-jsontypes/jsontypes"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

func TestJSONSubset(t *testing.T) {
	cases := []struct {
		want, got any
		ok        bool
	}{
		{map[string]any{"a": 1.0}, map[string]any{"a": 1.0, "b": 2.0}, true},
		{map[string]any{"a": 1.0}, map[string]any{"a": 2.0}, false},
		{map[string]any{"a": map[string]any{"x": "y"}}, map[string]any{"a": map[string]any{"x": "y", "z": 1.0}}, true},
		{[]any{1.0, 2.0}, []any{1.0, 2.0}, true},
		{[]any{1.0}, []any{1.0, 2.0}, false},
		{map[string]any{"a": 1.0}, []any{}, false},
	}
	for i, c := range cases {
		if got := jsonSubset(c.want, c.got); got != c.ok {
			t.Errorf("case %d: jsonSubset = %v, want %v", i, got, c.ok)
		}
	}
}

func TestSameCIDRAndInstant(t *testing.T) {
	if !sameCIDR("10.0.0.1", "10.0.0.1/32") || !sameCIDR("10.0.0.0/8", "10.1.2.3/8") || sameCIDR("10.0.0.1", "10.0.0.0/24") {
		t.Fatal("sameCIDR")
	}
	if !sameCIDR("2001:db8::1", "2001:db8::1/128") {
		t.Fatal("sameCIDR v6")
	}
	if !sameInstant("2027-01-01T00:00:00+00:00", "2027-01-01T00:00:00Z") || sameInstant("2027-01-01T00:00:00Z", "2027-01-01T00:00:01Z") {
		t.Fatal("sameInstant")
	}
}

func TestFromAPINormalisation(t *testing.T) {
	opt := field{Name: "labels", Kind: kStringMap, Optional: true}
	if v := fromAPI(opt, map[string]any{}, true, types.MapNull(types.StringType)); !v.IsNull() {
		t.Fatalf("empty map with null prior must stay null, got %v", v)
	}
	def := field{Name: "replicas", Kind: kInt, Optional: true, Default: 1}
	if v := fromAPI(def, 1.0, true, types.Int64Null()); !v.IsNull() {
		t.Fatalf("server default with null prior must stay null, got %v", v)
	}
	if v := fromAPI(def, 3.0, true, types.Int64Null()); v.(types.Int64).ValueInt64() != 3 {
		t.Fatalf("drift from default must show, got %v", v)
	}
	js := field{Name: "params", Kind: kJSON, Required: true}
	prior := jsontypes.NewNormalizedValue(`{"idle_minutes":30}`)
	if v := fromAPI(js, map[string]any{"idle_minutes": 30.0, "timezone": "UTC"}, true, prior); !v.Equal(prior) {
		t.Fatalf("superset JSON must keep prior, got %v", v)
	}
	if v := fromAPI(js, map[string]any{"idle_minutes": 10.0}, true, prior); v.Equal(prior) {
		t.Fatal("changed JSON must be read back")
	}
	wo := field{Name: "secret", Kind: kString, Optional: true, WriteOnly: true}
	if v := fromAPI(wo, nil, false, types.StringValue("s")); v.(types.String).ValueString() != "s" {
		t.Fatal("write-only keeps prior")
	}
	absent := field{Name: "key", Kind: kString, ReadOnly: true, Stable: true}
	if v := fromAPI(absent, nil, false, types.StringValue("k")); v.(types.String).ValueString() != "k" {
		t.Fatal("absent field keeps prior")
	}
}

func TestClearValue(t *testing.T) {
	if v, ok := clearValue(field{Kind: kInt, Default: 1}, nil); !ok || v != 1 {
		t.Fatal("default")
	}
	if v, ok := clearValue(field{Kind: kString, Nullable: true}, nil); !ok || v != nil {
		t.Fatal("nullable")
	}
	if v, ok := clearValue(field{Kind: kJSON}, jsontypes.NewNormalizedValue(`[1]`)); !ok || len(v.([]any)) != 0 {
		t.Fatal("json array")
	}
	if _, ok := clearValue(field{Kind: kString}, nil); ok {
		t.Fatal("non-nullable string cannot be cleared")
	}
}
