package provider

// Declarative attribute specs shared by every gpudeck resource and data source.
//
// Each resource is a list of fields (kind + flags) instead of a hand-written model struct. The
// generic code in resource_generic.go turns the specs into a framework schema, builds JSON
// request bodies from plan values and maps API responses back into state with a few
// normalisation rules that keep plans quiet:
//
//   - after create/update, every attribute whose planned value is known is stored as planned;
//     only unknown (computed) values come from the API response,
//   - on refresh, an optional attribute that is null in state stays null when the API returns an
//     empty value or the field's server default,
//   - JSON attributes stay as written when the API returns a superset (server-filled defaults),
//   - timestamps that denote the same instant are not a change,
//   - write-only, create-only and provider-local attributes are never read back.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/netip"
	"reflect"
	"strconv"
	"time"

	"github.com/hashicorp/terraform-plugin-framework-jsontypes/jsontypes"
	"github.com/hashicorp/terraform-plugin-framework-validators/listvalidator"
	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/attr"
	dschema "github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	rschema "github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/boolplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/float64planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/int64planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/mapplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

type kind int

const (
	kString     kind = iota
	kInt             // int64
	kFloat           // float64
	kBool            //
	kJSON            // JSON object or array, stored as normalized JSON text
	kStringList      // list(string)
	kStringMap       // map(string)
	kObjectList      // list of nested objects described by Elem
)

// field describes one attribute and how it maps to the admin API.
type field struct {
	Name        string // Terraform attribute name
	API         string // JSON key when it differs from Name
	Kind        kind
	Description string
	Required    bool
	Optional    bool
	Computed    bool // with Optional: the server picks a value when unset
	Sensitive   bool
	ForceNew    bool // not updatable in place; a change replaces the resource
	ReadOnly    bool // computed by the API, never sent
	Stable      bool // computed value never changes after create
	CreateOnly  bool // sent on create only and never read back
	WriteOnly   bool // sent on create/update and never read back (secrets)
	Local       bool // provider-side setting: never sent, never read
	Custom      bool // read normally, sent only by resource hooks
	Nullable    bool // clearing sends JSON null
	Time        bool // RFC 3339 timestamp
	CIDR        bool // IP or CIDR; a bare address equals its /32 or /128
	UpdateOnly  bool // not accepted on create; applied with a PATCH right after
	KeepNull    bool // server-assigned value ignored while unset in configuration
	Default     any  // server default for an unset optional attribute
	Enum        []string
	Elem        []field // kObjectList element attributes
}

func (f field) key() string {
	if f.API != "" {
		return f.API
	}
	return f.Name
}

// sendable reports whether the generic create body includes the field.
func (f field) sendable() bool { return !f.ReadOnly && !f.Local && !f.Custom }

// creatable reports whether the create body includes the field.
func (f field) creatable() bool { return f.sendable() && !f.UpdateOnly }

// updatable reports whether a planned change goes into the PATCH body.
func (f field) updatable() bool {
	return f.sendable() && !f.ForceNew && !f.CreateOnly
}

func (f field) computed() bool { return f.ReadOnly || f.Computed }

func (f field) attrType() attr.Type {
	switch f.Kind {
	case kInt:
		return types.Int64Type
	case kFloat:
		return types.Float64Type
	case kBool:
		return types.BoolType
	case kJSON:
		return jsontypes.NormalizedType{}
	case kStringList:
		return types.ListType{ElemType: types.StringType}
	case kStringMap:
		return types.MapType{ElemType: types.StringType}
	case kObjectList:
		return types.ListType{ElemType: types.ObjectType{AttrTypes: elemTypes(f.Elem)}}
	default:
		return types.StringType
	}
}

func elemTypes(fields []field) map[string]attr.Type {
	out := make(map[string]attr.Type, len(fields))
	for _, f := range fields {
		out[f.Name] = f.attrType()
	}
	return out
}

func (f field) description() string {
	d := f.Description
	if len(f.Enum) > 0 {
		d += " One of: " + joinQuoted(f.Enum) + "."
	}
	if f.Default != nil && !f.Required {
		d += fmt.Sprintf(" Server default: `%v`.", f.Default)
	}
	if f.ForceNew && !f.ReadOnly {
		d += " Changing this replaces the resource."
	}
	if f.CreateOnly {
		d += " Only used when the resource is created."
	}
	return d
}

func joinQuoted(values []string) string {
	out := ""
	for i, v := range values {
		if i > 0 {
			out += ", "
		}
		out += "`" + v + "`"
	}
	return out
}

func nullValue(f field) attr.Value {
	switch f.Kind {
	case kInt:
		return types.Int64Null()
	case kFloat:
		return types.Float64Null()
	case kBool:
		return types.BoolNull()
	case kJSON:
		return jsontypes.NewNormalizedNull()
	case kStringList:
		return types.ListNull(types.StringType)
	case kStringMap:
		return types.MapNull(types.StringType)
	case kObjectList:
		return types.ListNull(types.ObjectType{AttrTypes: elemTypes(f.Elem)})
	default:
		return types.StringNull()
	}
}

// ----- schema builders ---------------------------------------------------------------------

func resourceAttributes(fields []field) map[string]rschema.Attribute {
	out := make(map[string]rschema.Attribute, len(fields))
	for _, f := range fields {
		out[f.Name] = resourceAttribute(f)
	}
	return out
}

func resourceAttribute(f field) rschema.Attribute {
	required := f.Required
	optional := f.Optional && !f.Required
	computed := f.computed()
	keep := f.Stable || (f.Optional && f.Computed)
	desc := f.description()
	switch f.Kind {
	case kInt:
		var pm []planmodifier.Int64
		if keep {
			pm = append(pm, int64planmodifier.UseStateForUnknown())
		}
		if f.ForceNew && !f.ReadOnly {
			pm = append(pm, int64planmodifier.RequiresReplace())
		}
		return rschema.Int64Attribute{Required: required, Optional: optional, Computed: computed,
			Sensitive: f.Sensitive, Description: desc, MarkdownDescription: desc, PlanModifiers: pm}
	case kFloat:
		var pm []planmodifier.Float64
		if keep {
			pm = append(pm, float64planmodifier.UseStateForUnknown())
		}
		if f.ForceNew && !f.ReadOnly {
			pm = append(pm, float64planmodifier.RequiresReplace())
		}
		return rschema.Float64Attribute{Required: required, Optional: optional, Computed: computed,
			Sensitive: f.Sensitive, Description: desc, MarkdownDescription: desc, PlanModifiers: pm}
	case kBool:
		var pm []planmodifier.Bool
		if keep {
			pm = append(pm, boolplanmodifier.UseStateForUnknown())
		}
		if f.ForceNew && !f.ReadOnly {
			pm = append(pm, boolplanmodifier.RequiresReplace())
		}
		return rschema.BoolAttribute{Required: required, Optional: optional, Computed: computed,
			Sensitive: f.Sensitive, Description: desc, MarkdownDescription: desc, PlanModifiers: pm}
	case kStringList, kObjectList:
		var pm []planmodifier.List
		if keep {
			pm = append(pm, listplanmodifier.UseStateForUnknown())
		}
		if f.ForceNew && !f.ReadOnly {
			pm = append(pm, listplanmodifier.RequiresReplace())
		}
		if f.Kind == kObjectList {
			return rschema.ListNestedAttribute{Required: required, Optional: optional, Computed: computed,
				Sensitive: f.Sensitive, Description: desc, MarkdownDescription: desc, PlanModifiers: pm,
				NestedObject: rschema.NestedAttributeObject{Attributes: resourceAttributes(f.Elem)}}
		}
		var v []validator.List
		if len(f.Enum) > 0 {
			v = append(v, listvalidator.ValueStringsAre(stringvalidator.OneOf(f.Enum...)))
		}
		return rschema.ListAttribute{ElementType: types.StringType, Required: required, Optional: optional,
			Computed: computed, Sensitive: f.Sensitive, Description: desc, MarkdownDescription: desc,
			PlanModifiers: pm, Validators: v}
	case kStringMap:
		var pm []planmodifier.Map
		if keep {
			pm = append(pm, mapplanmodifier.UseStateForUnknown())
		}
		if f.ForceNew && !f.ReadOnly {
			pm = append(pm, mapplanmodifier.RequiresReplace())
		}
		return rschema.MapAttribute{ElementType: types.StringType, Required: required, Optional: optional,
			Computed: computed, Sensitive: f.Sensitive, Description: desc, MarkdownDescription: desc,
			PlanModifiers: pm}
	default: // kString, kJSON
		var pm []planmodifier.String
		if keep {
			pm = append(pm, stringplanmodifier.UseStateForUnknown())
		}
		if f.ForceNew && !f.ReadOnly {
			pm = append(pm, stringplanmodifier.RequiresReplace())
		}
		var v []validator.String
		if len(f.Enum) > 0 {
			v = append(v, stringvalidator.OneOf(f.Enum...))
		}
		a := rschema.StringAttribute{Required: required, Optional: optional, Computed: computed,
			Sensitive: f.Sensitive, Description: desc, MarkdownDescription: desc, PlanModifiers: pm,
			Validators: v}
		if f.Kind == kJSON {
			a.CustomType = jsontypes.NormalizedType{}
		}
		return a
	}
}

// dataAttributes builds data source attributes: filters are optional inputs, everything else
// is computed.
func dataAttributes(fields []field, filter bool) map[string]dschema.Attribute {
	out := make(map[string]dschema.Attribute, len(fields))
	for _, f := range fields {
		out[f.Name] = dataAttribute(f, filter)
	}
	return out
}

func dataAttribute(f field, filter bool) dschema.Attribute {
	optional := filter
	computed := !filter
	desc := f.description()
	switch f.Kind {
	case kInt:
		return dschema.Int64Attribute{Optional: optional, Computed: computed, Description: desc,
			MarkdownDescription: desc}
	case kFloat:
		return dschema.Float64Attribute{Optional: optional, Computed: computed, Description: desc,
			MarkdownDescription: desc}
	case kBool:
		return dschema.BoolAttribute{Optional: optional, Computed: computed, Description: desc,
			MarkdownDescription: desc}
	case kStringList:
		return dschema.ListAttribute{ElementType: types.StringType, Optional: optional,
			Computed: computed, Description: desc, MarkdownDescription: desc}
	case kStringMap:
		return dschema.MapAttribute{ElementType: types.StringType, Optional: optional,
			Computed: computed, Description: desc, MarkdownDescription: desc}
	case kObjectList:
		return dschema.ListNestedAttribute{Optional: optional, Computed: computed, Description: desc,
			MarkdownDescription: desc,
			NestedObject:        dschema.NestedAttributeObject{Attributes: dataAttributes(f.Elem, false)}}
	default:
		var v []validator.String
		if filter && len(f.Enum) > 0 {
			v = append(v, stringvalidator.OneOf(f.Enum...))
		}
		a := dschema.StringAttribute{Optional: optional, Computed: computed, Description: desc,
			MarkdownDescription: desc, Validators: v}
		if f.Kind == kJSON {
			a.CustomType = jsontypes.NormalizedType{}
		}
		return a
	}
}

// ----- reading framework values ------------------------------------------------------------

// values holds one attribute value per field name.
type values map[string]attr.Value

type getter func(ctx context.Context, p path.Path, target interface{}) diag.Diagnostics

func loadValues(ctx context.Context, fields []field, get getter, diags *diag.Diagnostics) values {
	out := make(values, len(fields))
	for _, f := range fields {
		var v attr.Value
		diags.Append(get(ctx, path.Root(f.Name), &v)...)
		if v == nil {
			v = nullValue(f)
		}
		out[f.Name] = v
	}
	return out
}

func known(v attr.Value) bool { return v != nil && !v.IsNull() && !v.IsUnknown() }

func (v values) str(name string) string {
	if s, ok := v[name].(types.String); ok && known(s) {
		return s.ValueString()
	}
	return ""
}

func (v values) boolean(name string, def bool) bool {
	if b, ok := v[name].(types.Bool); ok && known(b) {
		return b.ValueBool()
	}
	return def
}

func (v values) integer(name string, def int64) int64 {
	if i, ok := v[name].(types.Int64); ok && known(i) {
		return i.ValueInt64()
	}
	return def
}

// ----- framework value -> API JSON ---------------------------------------------------------

// toAPI converts a known framework value into JSON-ready data.
func toAPI(f field, v attr.Value) (any, error) {
	switch tv := v.(type) {
	case types.String:
		return tv.ValueString(), nil
	case types.Int64:
		return tv.ValueInt64(), nil
	case types.Float64:
		return tv.ValueFloat64(), nil
	case types.Bool:
		return tv.ValueBool(), nil
	case jsontypes.Normalized:
		var out any
		if err := json.Unmarshal([]byte(tv.ValueString()), &out); err != nil {
			return nil, fmt.Errorf("%s: invalid JSON: %w", f.Name, err)
		}
		return out, nil
	case types.Map:
		out := map[string]any{}
		for k, e := range tv.Elements() {
			if s, ok := e.(types.String); ok && !s.IsNull() {
				out[k] = s.ValueString()
			}
		}
		return out, nil
	case types.List:
		out := []any{}
		for _, e := range tv.Elements() {
			switch ev := e.(type) {
			case types.String:
				out = append(out, ev.ValueString())
			case types.Object:
				obj := map[string]any{}
				attrs := ev.Attributes()
				for _, sub := range f.Elem {
					sv, ok := attrs[sub.Name]
					if !ok || !known(sv) || sub.ReadOnly {
						continue
					}
					conv, err := toAPI(sub, sv)
					if err != nil {
						return nil, err
					}
					obj[sub.key()] = conv
				}
				out = append(out, obj)
			}
		}
		return out, nil
	}
	return nil, fmt.Errorf("%s: unsupported value type %T", f.Name, v)
}

// clearValue is what an update sends when an attribute is removed from the configuration.
func clearValue(f field, prior attr.Value) (any, bool) {
	switch {
	case f.Default != nil:
		return f.Default, true
	case f.Nullable:
		return nil, true
	case f.Kind == kJSON:
		if n, ok := prior.(jsontypes.Normalized); ok && known(n) && len(n.ValueString()) > 0 &&
			n.ValueString()[0] == '[' {
			return []any{}, true
		}
		return map[string]any{}, true
	case f.Kind == kStringList || f.Kind == kObjectList:
		return []any{}, true
	case f.Kind == kStringMap:
		return map[string]any{}, true
	}
	return nil, false
}

// ----- API JSON -> framework value ---------------------------------------------------------

// fromAPI converts a response value into a framework value, using prior (state or plan) to
// apply the normalisation rules described at the top of this file.
func fromAPI(f field, raw any, present bool, prior attr.Value) attr.Value {
	if prior == nil || prior.IsUnknown() {
		prior = nullValue(f)
	}
	if f.WriteOnly || f.CreateOnly || f.Local {
		return prior
	}
	if !present {
		if !prior.IsNull() {
			return prior
		}
		return nullValue(f)
	}
	if f.KeepNull && prior.IsNull() {
		return prior
	}
	if !f.computed() && prior.IsNull() && (isEmpty(raw) || (f.Default != nil && jsonEqual(raw, f.Default))) {
		return nullValue(f)
	}
	switch f.Kind {
	case kJSON:
		if n, ok := prior.(jsontypes.Normalized); ok && known(n) {
			var p any
			if json.Unmarshal([]byte(n.ValueString()), &p) == nil && jsonSubset(p, raw) {
				return prior
			}
		}
	case kString:
		if ps, ok := prior.(types.String); ok && known(ps) {
			if s, ok := raw.(string); ok {
				if (f.Time && sameInstant(ps.ValueString(), s)) || (f.CIDR && sameCIDR(ps.ValueString(), s)) {
					return prior
				}
			}
		}
	case kObjectList:
		return objectListFromAPI(f, raw, prior)
	}
	return plainValue(f, raw)
}

func objectListFromAPI(f field, raw any, prior attr.Value) attr.Value {
	items, ok := raw.([]any)
	if !ok {
		return nullValue(f)
	}
	objType := types.ObjectType{AttrTypes: elemTypes(f.Elem)}
	var priorElems []attr.Value
	if l, ok := prior.(types.List); ok && known(l) {
		priorElems = l.Elements()
	}
	elems := make([]attr.Value, 0, len(items))
	for i, it := range items {
		m, _ := it.(map[string]any)
		var priorAttrs map[string]attr.Value
		if i < len(priorElems) {
			if o, ok := priorElems[i].(types.Object); ok && known(o) {
				priorAttrs = o.Attributes()
			}
		}
		attrs := make(map[string]attr.Value, len(f.Elem))
		for _, sub := range f.Elem {
			rv, present := m[sub.key()]
			attrs[sub.Name] = fromAPI(sub, rv, present, priorAttrs[sub.Name])
		}
		elems = append(elems, types.ObjectValueMust(objType.AttrTypes, attrs))
	}
	return types.ListValueMust(objType, elems)
}

// plainValue converts without any prior-based normalisation (data sources, computed values).
func plainValue(f field, raw any) attr.Value {
	if raw == nil {
		return nullValue(f)
	}
	switch f.Kind {
	case kInt:
		if n, ok := toFloat(raw); ok {
			return types.Int64Value(int64(n))
		}
		return types.Int64Null()
	case kFloat:
		if n, ok := toFloat(raw); ok {
			return types.Float64Value(n)
		}
		return types.Float64Null()
	case kBool:
		if b, ok := raw.(bool); ok {
			return types.BoolValue(b)
		}
		return types.BoolNull()
	case kJSON:
		buf, err := json.Marshal(raw)
		if err != nil {
			return jsontypes.NewNormalizedNull()
		}
		return jsontypes.NewNormalizedValue(string(buf))
	case kStringList:
		items, ok := raw.([]any)
		if !ok {
			return types.ListNull(types.StringType)
		}
		elems := make([]attr.Value, 0, len(items))
		for _, it := range items {
			elems = append(elems, types.StringValue(scalarString(it)))
		}
		return types.ListValueMust(types.StringType, elems)
	case kStringMap:
		m, ok := raw.(map[string]any)
		if !ok {
			return types.MapNull(types.StringType)
		}
		elems := make(map[string]attr.Value, len(m))
		for k, it := range m {
			elems[k] = types.StringValue(scalarString(it))
		}
		return types.MapValueMust(types.StringType, elems)
	case kObjectList:
		return objectListFromAPI(f, raw, nil)
	default:
		return types.StringValue(scalarString(raw))
	}
}

func scalarString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case nil:
		return ""
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(t)
	}
	buf, _ := json.Marshal(v)
	return string(buf)
}

func toFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case json.Number:
		f, err := t.Float64()
		return f, err == nil
	case string: // decimals serialised as strings
		f, err := strconv.ParseFloat(t, 64)
		return f, err == nil
	}
	return 0, false
}

func isEmpty(v any) bool {
	switch t := v.(type) {
	case nil:
		return true
	case string:
		return t == ""
	case []any:
		return len(t) == 0
	case map[string]any:
		return len(t) == 0
	}
	return false
}

// jsonEqual compares two values after a JSON round trip (so 1 == 1.0 and int == float64).
func jsonEqual(a, b any) bool {
	return reflect.DeepEqual(roundTrip(a), roundTrip(b))
}

func roundTrip(v any) any {
	buf, err := json.Marshal(v)
	if err != nil {
		return v
	}
	var out any
	if json.Unmarshal(buf, &out) != nil {
		return v
	}
	return out
}

// jsonSubset reports whether every key/value in want is present and equal in got (recursively
// for objects; arrays must match element by element).
func jsonSubset(want, got any) bool {
	switch w := want.(type) {
	case map[string]any:
		g, ok := got.(map[string]any)
		if !ok {
			return false
		}
		for k, wv := range w {
			gv, ok := g[k]
			if !ok || !jsonSubset(wv, gv) {
				return false
			}
		}
		return true
	case []any:
		g, ok := got.([]any)
		if !ok || len(g) != len(w) {
			return false
		}
		for i := range w {
			if !jsonSubset(w[i], g[i]) {
				return false
			}
		}
		return true
	}
	return jsonEqual(want, got)
}

func sameCIDR(a, b string) bool {
	pa, errA := parsePrefix(a)
	pb, errB := parsePrefix(b)
	return errA == nil && errB == nil && pa == pb
}

func parsePrefix(s string) (netip.Prefix, error) {
	if p, err := netip.ParsePrefix(s); err == nil {
		return p.Masked(), nil
	}
	addr, err := netip.ParseAddr(s)
	if err != nil {
		return netip.Prefix{}, err
	}
	return netip.PrefixFrom(addr, addr.BitLen()), nil
}

func sameInstant(a, b string) bool {
	ta, errA := time.Parse(time.RFC3339Nano, a)
	tb, errB := time.Parse(time.RFC3339Nano, b)
	return errA == nil && errB == nil && ta.Equal(tb)
}
