package provider

// Data sources gpudeck_nodes, gpudeck_devices and gpudeck_engines: filtered list endpoints
// flattened into a computed list of objects.

import (
	"context"
	"net/http"

	"github.com/google/uuid"
	"github.com/hashicorp/terraform-plugin-framework-jsontypes/jsontypes"
	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/datasource"
	dschema "github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/client"
)

type listFn func(ctx context.Context, api *client.API, filters values, page int, diags *diag.Diagnostics) (*http.Response, error)

type dataSourceDef struct {
	Name        string
	Description string
	Collection  string // computed list attribute name
	Filters     []field
	Elem        []field
	List        listFn
	Paginated   bool
}

type genericDataSource struct {
	def dataSourceDef
	api *client.API
}

var (
	_ datasource.DataSource              = (*genericDataSource)(nil)
	_ datasource.DataSourceWithConfigure = (*genericDataSource)(nil)
)

func newGenericDataSource(def dataSourceDef) func() datasource.DataSource {
	return func() datasource.DataSource { return &genericDataSource{def: def} }
}

func (d *genericDataSource) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_" + d.def.Name
}

func (d *genericDataSource) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	attrs := dataAttributes(d.def.Filters, true)
	attrs["id"] = dschema.StringAttribute{Computed: true, Description: "Static identifier for the result."}
	attrs[d.def.Collection] = dschema.ListNestedAttribute{
		Computed:     true,
		Description:  "Matching objects.",
		NestedObject: dschema.NestedAttributeObject{Attributes: dataAttributes(d.def.Elem, false)},
	}
	resp.Schema = dschema.Schema{Description: d.def.Description, MarkdownDescription: d.def.Description, Attributes: attrs}
}

func (d *genericDataSource) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	if api := configureDataSource(req, resp); api != nil {
		d.api = api
	}
}

func (d *genericDataSource) Read(ctx context.Context, req datasource.ReadRequest, resp *datasource.ReadResponse) {
	if d.api == nil {
		resp.Diagnostics.AddError("Provider not configured", "The gpudeck provider has no API client.")
		return
	}
	filters := loadValues(ctx, d.def.Filters, req.Config.GetAttribute, &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	var items []map[string]any
	for page := 1; ; page++ {
		res, err := client.DecodeList(client.Raw(d.def.List(ctx, d.api, filters, page, &resp.Diagnostics)))
		if resp.Diagnostics.HasError() {
			return
		}
		if err != nil {
			resp.Diagnostics.AddError("Unable to list "+d.def.Collection, err.Error())
			return
		}
		items = append(items, res.Items...)
		if !d.def.Paginated || !res.HasMore || page >= 1000 {
			break
		}
	}
	objType := types.ObjectType{AttrTypes: elemTypes(d.def.Elem)}
	elems := make([]attr.Value, 0, len(items))
	for _, it := range items {
		attrs := make(map[string]attr.Value, len(d.def.Elem))
		for _, f := range d.def.Elem {
			attrs[f.Name] = plainValue(f, it[f.key()])
		}
		obj, diags := types.ObjectValue(objType.AttrTypes, attrs)
		resp.Diagnostics.Append(diags...)
		elems = append(elems, obj)
	}
	list, diags := types.ListValue(objType, elems)
	resp.Diagnostics.Append(diags...)
	for _, f := range d.def.Filters {
		resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root(f.Name), filters[f.Name])...)
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), d.def.Name)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root(d.def.Collection), list)...)
}

// filterUUID parses an optional UUID filter.
func filterUUID(v values, name string, diags *diag.Diagnostics) *uuid.UUID {
	s := v.str(name)
	if s == "" {
		return nil
	}
	id, err := uuid.Parse(s)
	if err != nil {
		diags.AddAttributeError(path.Root(name), "Invalid UUID", err.Error())
		return nil
	}
	return &id
}

func filterEnum[E ~string](v values, name string) *E {
	s := v.str(name)
	if s == "" {
		return nil
	}
	e := E(s)
	return &e
}

func jsonText(v attr.Value) string {
	if n, ok := v.(jsontypes.Normalized); ok && known(n) {
		return n.ValueString()
	}
	return ""
}

const listPageSize = 500

// ----- gpudeck_nodes -----------------------------------------------------------------------

var nodesDataSourceDef = dataSourceDef{
	Name:        "nodes",
	Description: "GPU machines registered with the control plane.",
	Collection:  "nodes",
	Paginated:   true,
	Filters: []field{
		{Name: "cluster_id", Kind: kString, Description: "Only nodes in this cluster."},
		{Name: "status", Kind: kString, Enum: enumNodeStatus, Description: "Only nodes with this status."},
		{Name: "os", Kind: kString, Enum: enumNodeOS, Description: "Only nodes with this OS."},
		{Name: "search", Kind: kString, Description: "Substring match on the name."},
		{Name: "labels", Kind: kStringList, Description: "`key=value` label filters (all must match)."},
	},
	Elem: []field{
		{Name: "id", Kind: kString, Description: "Node UUID."},
		{Name: "cluster_id", Kind: kString, Description: "Cluster UUID."},
		{Name: "name", Kind: kString, Description: "Node name."},
		{Name: "hostname", Kind: kString, Description: "Hostname."},
		{Name: "ip_address", Kind: kString, Description: "IP address."},
		{Name: "proxy_address", Kind: kString, Description: "Agent proxy address."},
		{Name: "proxy_port", Kind: kInt, Description: "Agent proxy port."},
		{Name: "os", Kind: kString, Description: "Operating system."},
		{Name: "arch", Kind: kString, Description: "CPU architecture."},
		{Name: "status", Kind: kString, Description: "Node status."},
		{Name: "agent_version", Kind: kString, Description: "Node agent version."},
		{Name: "labels", Kind: kStringMap, Description: "Labels."},
		{Name: "cpu_cores", Kind: kInt, Description: "CPU cores."},
		{Name: "memory_total_bytes", Kind: kInt, Description: "System memory."},
		{Name: "device_count", Kind: kInt, Description: "Accelerators."},
		{Name: "instance_count", Kind: kInt, Description: "Running instances."},
		{Name: "last_heartbeat_at", Kind: kString, Description: "Last heartbeat."},
	},
	List: func(ctx context.Context, api *client.API, f values, page int, diags *diag.Diagnostics) (*http.Response, error) {
		params := &client.ListNodesParams{
			Page:      ptr(page),
			PageSize:  ptr(listPageSize),
			ClusterId: filterUUID(f, "cluster_id", diags),
			Status:    filterEnum[client.NodeStatus](f, "status"),
			Os:        filterEnum[client.NodeOs](f, "os"),
		}
		if s := f.str("search"); s != "" {
			params.Search = &s
		}
		if l, ok := f["labels"].(types.List); ok && known(l) {
			labels := []string{}
			diags.Append(l.ElementsAs(ctx, &labels, false)...)
			params.Label = &labels
		}
		if diags.HasError() {
			return nil, nil
		}
		return api.ListNodes(ctx, params)
	},
}

// ----- gpudeck_devices ---------------------------------------------------------------------

var devicesDataSourceDef = dataSourceDef{
	Name:        "devices",
	Description: "Accelerators (GPUs, Apple silicon) reported by node agents.",
	Collection:  "devices",
	Paginated:   true,
	Filters: []field{
		{Name: "cluster_id", Kind: kString, Description: "Only devices in this cluster."},
		{Name: "node_id", Kind: kString, Description: "Only devices on this node."},
		{Name: "vendor", Kind: kString, Enum: enumDeviceVendor, Description: "Only this vendor."},
		{Name: "health", Kind: kString, Enum: enumDeviceHealth, Description: "Only this health state."},
		{Name: "free_vram_min_bytes", Kind: kInt, Description: "Only devices with at least this much free VRAM."},
	},
	Elem: []field{
		{Name: "id", Kind: kString, Description: "Device UUID."},
		{Name: "node_id", Kind: kString, Description: "Node UUID."},
		{Name: "node_name", Kind: kString, Description: "Node name."},
		{Name: "cluster_id", Kind: kString, Description: "Cluster UUID."},
		{Name: "vendor", Kind: kString, Description: "Vendor."},
		{Name: "index", Kind: kInt, Description: "Index on the node."},
		{Name: "uuid", Kind: kString, Description: "Vendor device UUID."},
		{Name: "name", Kind: kString, Description: "Model name."},
		{Name: "vram_total_bytes", Kind: kInt, Description: "Total VRAM (unified memory on Apple)."},
		{Name: "vram_reserved_bytes", Kind: kInt, Description: "VRAM reserved by slices and instances."},
		{Name: "is_unified_memory", Kind: kBool, Description: "Unified memory device."},
		{Name: "driver_version", Kind: kString, Description: "Driver version."},
		{Name: "compute_capability", Kind: kString, Description: "Compute capability."},
		{Name: "health", Kind: kString, Description: "Health."},
		{Name: "labels", Kind: kStringMap, Description: "Labels."},
	},
	List: func(ctx context.Context, api *client.API, f values, page int, diags *diag.Diagnostics) (*http.Response, error) {
		params := &client.ListDevicesParams{
			Page:      ptr(page),
			PageSize:  ptr(listPageSize),
			ClusterId: filterUUID(f, "cluster_id", diags),
			NodeId:    filterUUID(f, "node_id", diags),
			Vendor:    filterEnum[client.DeviceVendor](f, "vendor"),
			Health:    filterEnum[client.DeviceHealth](f, "health"),
		}
		if i, ok := f["free_vram_min_bytes"].(types.Int64); ok && known(i) {
			params.FreeVramMinBytes = ptr(int(i.ValueInt64()))
		}
		if diags.HasError() {
			return nil, nil
		}
		return api.ListDevices(ctx, params)
	},
}

// ----- gpudeck_engines ---------------------------------------------------------------------

var enginesDataSourceDef = dataSourceDef{
	Name:        "engines",
	Description: "Inference engines in the registry (llama.cpp, vLLM, SGLang, MLX, ...).",
	Collection:  "engines",
	Filters: []field{
		{Name: "enabled", Kind: kBool, Description: "Only enabled (true) or disabled (false) engines."},
		{Name: "model_type", Kind: kString, Enum: enumModelType, Description: "Only engines that serve this model type."},
		{Name: "vendor", Kind: kString, Enum: enumDeviceVendor, Description: "Only engines that support this vendor."},
	},
	Elem: []field{
		{Name: "id", Kind: kString, Description: "Engine UUID."},
		{Name: "name", Kind: kString, Description: "Engine name."},
		{Name: "display_name", Kind: kString, Description: "Display name."},
		{Name: "description", Kind: kString, Description: "Description."},
		{Name: "runtime", Kind: kString, Description: "native or container."},
		{Name: "is_builtin", Kind: kBool, Description: "Shipped with GPU Deck."},
		{Name: "is_enabled", Kind: kBool, Description: "Enabled."},
		{Name: "supported_model_types", Kind: kStringList, Description: "Model types."},
		{Name: "supported_vendors", Kind: kStringList, Description: "Device vendors."},
		{Name: "supported_os", Kind: kStringList, Description: "Operating systems."},
		{Name: "supported_formats", Kind: kStringList, Description: "Model file formats."},
	},
	List: func(ctx context.Context, api *client.API, f values, _ int, _ *diag.Diagnostics) (*http.Response, error) {
		params := &client.ListEnginesParams{
			ModelType: filterEnum[client.ModelType](f, "model_type"),
			Vendor:    filterEnum[client.DeviceVendor](f, "vendor"),
		}
		if b, ok := f["enabled"].(types.Bool); ok && known(b) {
			params.Enabled = ptr(b.ValueBool())
		}
		return api.ListEngines(ctx, params)
	},
}

func NewNodesDataSource() datasource.DataSource { return newGenericDataSource(nodesDataSourceDef)() }
func NewDevicesDataSource() datasource.DataSource {
	return newGenericDataSource(devicesDataSourceDef)()
}
func NewEnginesDataSource() datasource.DataSource {
	return newGenericDataSource(enginesDataSourceDef)()
}
