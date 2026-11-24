package provider

// Generic CRUD resource driven by a resourceDef (field specs + calls into the generated client).

import (
	"context"
	"fmt"
	"io"
	"net/http"

	"github.com/google/uuid"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	rschema "github.com/hashicorp/terraform-plugin-framework/resource/schema"

	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/client"
)

type (
	createFn func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error)
	readFn   func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error)
	updateFn func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error)
	deleteFn func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error)
)

// resourceDef describes one gpudeck_* resource.
type resourceDef struct {
	Name        string // type name suffix: gpudeck_<Name>
	Description string
	Fields      []field

	Create createFn
	Read   readFn
	Update updateFn // nil: every configurable field must be ForceNew
	Delete deleteFn

	// Optional hooks.
	// ReadObject replaces Read (e.g. resources without a GET-by-id endpoint). Return
	// (nil, nil) when the object no longer exists.
	ReadObject func(ctx context.Context, api *client.API, id uuid.UUID, state values) (map[string]any, error)
	// PrepareBody adjusts a create/update body (create is true on create).
	PrepareBody func(body map[string]any, plan values, create bool)
	// AfterCreate runs after a successful create and may return a fresher object.
	AfterCreate func(ctx context.Context, api *client.API, id uuid.UUID, obj map[string]any, plan values) (map[string]any, error)
	// AfterUpdate runs after the PATCH (or instead of it when nothing changed) for changes that
	// need dedicated endpoints; it returns the fresh object or nil to re-read.
	AfterUpdate func(ctx context.Context, api *client.API, id uuid.UUID, plan, state values) (map[string]any, error)
	// AfterDelete waits for asynchronous deletion.
	AfterDelete func(ctx context.Context, api *client.API, id uuid.UUID, state values) diag.Diagnostics
	// Validate checks the configuration (known values only).
	Validate func(ctx context.Context, cfg values) diag.Diagnostics
}

type genericResource struct {
	def resourceDef
	api *client.API
}

var (
	_ resource.Resource                   = (*genericResource)(nil)
	_ resource.ResourceWithConfigure      = (*genericResource)(nil)
	_ resource.ResourceWithImportState    = (*genericResource)(nil)
	_ resource.ResourceWithValidateConfig = (*genericResource)(nil)
)

func newGeneric(def resourceDef) func() resource.Resource {
	return func() resource.Resource { return &genericResource{def: def} }
}

func (r *genericResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_" + r.def.Name
}

func (r *genericResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = rschema.Schema{
		Description:         r.def.Description,
		MarkdownDescription: r.def.Description,
		Attributes:          resourceAttributes(r.def.Fields),
	}
}

func (r *genericResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	if api := configureResource(req, resp); api != nil {
		r.api = api
	}
}

func (r *genericResource) ValidateConfig(ctx context.Context, req resource.ValidateConfigRequest, resp *resource.ValidateConfigResponse) {
	if r.def.Validate == nil {
		return
	}
	cfg := loadValues(ctx, r.def.Fields, req.Config.GetAttribute, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	resp.Diagnostics.Append(r.def.Validate(ctx, cfg)...)
}

func (r *genericResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	if _, err := uuid.Parse(req.ID); err != nil {
		resp.Diagnostics.AddError("Invalid import ID", "expected the object's UUID: "+err.Error())
		return
	}
	resource.ImportStatePassthroughID(ctx, path.Root("id"), req, resp)
}

func (r *genericResource) ready(diags *diag.Diagnostics) bool {
	if r.api == nil {
		diags.AddError("Provider not configured",
			"The gpudeck provider has no API client; check the provider block.")
		return false
	}
	return true
}

func (r *genericResource) label() string { return "gpudeck_" + r.def.Name }

// ----- CRUD ----------------------------------------------------------------------------------

func (r *genericResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	if !r.ready(&resp.Diagnostics) {
		return
	}
	plan := loadValues(ctx, r.def.Fields, req.Plan.GetAttribute, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	body, followUp := map[string]any{}, map[string]any{}
	for _, f := range r.def.Fields {
		if !f.sendable() || !known(plan[f.Name]) {
			continue
		}
		v, err := toAPI(f, plan[f.Name])
		if err != nil {
			resp.Diagnostics.AddAttributeError(path.Root(f.Name), "Invalid value", err.Error())
			return
		}
		if f.UpdateOnly {
			followUp[f.key()] = v
			continue
		}
		body[f.key()] = v
	}
	if r.def.PrepareBody != nil {
		r.def.PrepareBody(body, plan, true)
	}
	reader, err := client.JSONBody(body)
	if err != nil {
		resp.Diagnostics.AddError("Unable to encode request", err.Error())
		return
	}
	obj, err := client.DecodeObject(client.Raw(r.def.Create(ctx, r.api, reader)))
	if err != nil {
		resp.Diagnostics.AddError("Unable to create "+r.label(), err.Error())
		return
	}
	id, err := objectID(obj)
	if err != nil {
		resp.Diagnostics.AddError("Unexpected create response for "+r.label(), err.Error())
		return
	}
	// Persist the id first so a failing hook leaves a tracked (tainted) resource behind.
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), id.String())...)
	if len(followUp) > 0 {
		reader, err := client.JSONBody(followUp)
		var patched map[string]any
		if err == nil {
			patched, err = client.DecodeObject(client.Raw(r.def.Update(ctx, r.api, id, reader)))
		}
		if patched != nil {
			obj = patched
		}
		if err != nil {
			r.writeApplied(ctx, plan, obj, &resp.State, &resp.Diagnostics)
			resp.Diagnostics.AddError("Created "+r.label()+" "+id.String()+" but could not apply settings", err.Error())
			return
		}
	}
	if r.def.AfterCreate != nil {
		fresh, err := r.def.AfterCreate(ctx, r.api, id, obj, plan)
		if fresh != nil {
			obj = fresh
		}
		if err != nil {
			r.writeApplied(ctx, plan, obj, &resp.State, &resp.Diagnostics)
			resp.Diagnostics.AddError("Created "+r.label()+" "+id.String()+" but it did not become ready", err.Error())
			return
		}
	}
	r.writeApplied(ctx, plan, obj, &resp.State, &resp.Diagnostics)
}

func (r *genericResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	if !r.ready(&resp.Diagnostics) {
		return
	}
	state := loadValues(ctx, r.def.Fields, req.State.GetAttribute, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	id, err := uuid.Parse(state.str("id"))
	if err != nil {
		resp.Diagnostics.AddError("Invalid id in state", err.Error())
		return
	}
	obj, err := r.fetch(ctx, id, state)
	if err != nil {
		readError(ctx, err, resp, r.label())
		return
	}
	if obj == nil {
		resp.State.RemoveResource(ctx)
		return
	}
	for _, f := range r.def.Fields {
		raw, present := obj[f.key()]
		resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root(f.Name),
			fromAPI(f, raw, present, state[f.Name]))...)
	}
}

func (r *genericResource) fetch(ctx context.Context, id uuid.UUID, state values) (map[string]any, error) {
	if r.def.ReadObject != nil {
		return r.def.ReadObject(ctx, r.api, id, state)
	}
	return client.DecodeObject(client.Raw(r.def.Read(ctx, r.api, id)))
}

func (r *genericResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	if !r.ready(&resp.Diagnostics) {
		return
	}
	plan := loadValues(ctx, r.def.Fields, req.Plan.GetAttribute, &resp.Diagnostics)
	state := loadValues(ctx, r.def.Fields, req.State.GetAttribute, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	id, err := uuid.Parse(state.str("id"))
	if err != nil {
		resp.Diagnostics.AddError("Invalid id in state", err.Error())
		return
	}
	body := map[string]any{}
	for _, f := range r.def.Fields {
		pv, sv := plan[f.Name], state[f.Name]
		if !f.updatable() || pv.IsUnknown() || pv.Equal(sv) {
			continue
		}
		if pv.IsNull() {
			if cv, ok := clearValue(f, sv); ok {
				body[f.key()] = cv
			}
			continue
		}
		v, err := toAPI(f, pv)
		if err != nil {
			resp.Diagnostics.AddAttributeError(path.Root(f.Name), "Invalid value", err.Error())
			return
		}
		body[f.key()] = v
	}
	var obj map[string]any
	if len(body) > 0 {
		if r.def.Update == nil {
			resp.Diagnostics.AddError("Update not supported", r.label()+" cannot be updated in place")
			return
		}
		if r.def.PrepareBody != nil {
			r.def.PrepareBody(body, plan, false)
		}
		reader, err := client.JSONBody(body)
		if err != nil {
			resp.Diagnostics.AddError("Unable to encode request", err.Error())
			return
		}
		obj, err = client.DecodeObject(client.Raw(r.def.Update(ctx, r.api, id, reader)))
		if err != nil {
			resp.Diagnostics.AddError("Unable to update "+r.label(), err.Error())
			return
		}
	}
	if r.def.AfterUpdate != nil {
		fresh, err := r.def.AfterUpdate(ctx, r.api, id, plan, state)
		if err != nil {
			resp.Diagnostics.AddError("Unable to update "+r.label(), err.Error())
			return
		}
		if fresh != nil {
			obj = fresh
		}
	}
	if obj == nil {
		obj, err = r.fetch(ctx, id, state)
		if err != nil || obj == nil {
			if err == nil {
				err = fmt.Errorf("%s %s disappeared during update", r.label(), id)
			}
			resp.Diagnostics.AddError("Unable to read "+r.label()+" after update", err.Error())
			return
		}
	}
	r.writeApplied(ctx, plan, obj, &resp.State, &resp.Diagnostics)
}

func (r *genericResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	if !r.ready(&resp.Diagnostics) {
		return
	}
	state := loadValues(ctx, r.def.Fields, req.State.GetAttribute, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	id, err := uuid.Parse(state.str("id"))
	if err != nil {
		resp.Diagnostics.AddError("Invalid id in state", err.Error())
		return
	}
	status, body, err := client.Raw(r.def.Delete(ctx, r.api, id))
	if err == nil {
		err = client.Check(status, body, http.StatusOK, http.StatusAccepted, http.StatusNoContent)
	}
	if err != nil {
		if client.IsNotFound(err) {
			return
		}
		resp.Diagnostics.AddError("Unable to delete "+r.label(), err.Error())
		return
	}
	if r.def.AfterDelete != nil {
		resp.Diagnostics.Append(r.def.AfterDelete(ctx, r.api, id, state)...)
	}
}

// writeApplied stores the result of create/update: planned values where known, API values for
// everything the plan left unknown.
func (r *genericResource) writeApplied(ctx context.Context, plan values, obj map[string]any, st stateSetter, diags *diag.Diagnostics) {
	for _, f := range r.def.Fields {
		pv := plan[f.Name]
		if pv != nil && !pv.IsUnknown() {
			diags.Append(st.SetAttribute(ctx, path.Root(f.Name), pv)...)
			continue
		}
		raw, present := obj[f.key()]
		diags.Append(st.SetAttribute(ctx, path.Root(f.Name), fromAPI(f, raw, present, nil))...)
	}
}

type stateSetter interface {
	SetAttribute(ctx context.Context, p path.Path, v interface{}) diag.Diagnostics
}

func objectID(obj map[string]any) (uuid.UUID, error) {
	raw, ok := obj["id"].(string)
	if !ok {
		return uuid.Nil, fmt.Errorf("response has no id")
	}
	return uuid.Parse(raw)
}
