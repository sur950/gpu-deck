package provider

// The ten gpudeck_* resources. Field lists follow the *Create / *Update / response schemas in
// libs/contracts/openapi/admin.yaml; requests go through the generated client (internal/client).

import (
	"context"
	"io"
	"net/http"

	"github.com/google/uuid"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"

	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/client"
	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/rulesschema"
)

// Enumerations from admin.yaml.
var (
	enumClusterType      = []string{"bare_metal", "vm", "kubernetes", "cloud_aws", "cloud_azure", "cloud_gcp", "cloud_do"}
	enumClusterStatus    = []string{"active", "disabled"}
	enumModelSource      = []string{"huggingface", "modelscope", "local_path"}
	enumModelType        = []string{"chat", "thinking", "vision", "embedding", "rerank", "stt", "tts", "image", "video"}
	enumModelPriority    = []string{"always_on", "normal", "evictable"}
	enumRuleType         = []string{"idle_unload", "time_schedule", "low_traffic_shrink", "high_load_grow", "make_room_by_priority", "auto_heal"}
	enumAlertChannelType = []string{"slack", "email", "webhook"}
	enumAlertSeverity    = []string{"info", "warning", "critical"}
	enumIPRuleAction     = []string{"allow", "block"}
	enumIPRuleScope      = []string{"admin", "api_layer"}
	enumPlacement        = []string{"spread", "pack"}
	enumPreset           = []string{"low_latency", "high_throughput"}
	enumDeviceVendor     = []string{"nvidia", "amd", "hygon", "metax", "moorethreads", "thead", "apple"}
	enumDeviceHealth     = []string{"healthy", "degraded", "unhealthy", "unknown"}
	enumNodeStatus       = []string{"pending", "online", "offline", "unhealthy", "draining", "drained", "removed"}
	enumNodeOS           = []string{"linux", "macos"}
)

// ----- common fields -------------------------------------------------------------------------

func idField() field {
	return field{Name: "id", Kind: kString, ReadOnly: true, Stable: true, Description: "Object UUID."}
}

func createdAtField() field {
	return field{Name: "created_at", Kind: kString, ReadOnly: true, Stable: true, Time: true,
		Description: "Creation time (RFC 3339)."}
}

func updatedAtField() field {
	return field{Name: "updated_at", Kind: kString, ReadOnly: true, Time: true,
		Description: "Last update time (RFC 3339)."}
}

func nameField(desc string) field {
	return field{Name: "name", Kind: kString, Required: true, Description: desc}
}

func descriptionField() field {
	return field{Name: "description", Kind: kString, Optional: true, Nullable: true,
		Description: "Free-form description."}
}

func uuidOpt(name, desc string) field {
	return field{Name: name, Kind: kString, Optional: true, Nullable: true, Description: desc}
}

// keepNull marks an optional attribute the server fills in when it is unset (auto-picked
// versions, detected family/parser, default traffic settings): while unset in the configuration
// the server's value is not tracked, so plans stay empty.
func keepNull(f field) field {
	f.KeepNull = true
	return f
}

func jsonOpt(name, desc string) field {
	return field{Name: name, Kind: kJSON, Optional: true, Description: desc +
		" JSON-encoded (use `jsonencode`)."}
}

func roString(name, desc string) field {
	return field{Name: name, Kind: kString, ReadOnly: true, Description: desc}
}

// ----- gpudeck_cluster -----------------------------------------------------------------------

var clusterDef = resourceDef{
	Name:        "cluster",
	Description: "A cluster of GPU machines (bare metal, VM, Kubernetes or cloud).",
	Fields: []field{
		idField(),
		nameField("Cluster name (unique)."),
		{Name: "type", Kind: kString, Required: true, Enum: enumClusterType, Description: "Where the machines run."},
		descriptionField(),
		{Name: "region", Kind: kString, Optional: true, Nullable: true, Description: "Region or site label."},
		uuidOpt("credential_id", "Vault credential used to provision machines (cloud / Kubernetes)."),
		uuidOpt("price_plan_id", "Price plan for usage on this cluster (nodes may override)."),
		jsonOpt("config", "Provider settings: kubernetes {namespace, kubeconfig_credential_id}, cloud {vpc, subnet, instance_types, ssh_key_credential_id}."),
		{Name: "egress_allowlist", Kind: kStringList, Optional: true, Description: "host:port destinations nodes may reach besides the control plane."},
		{Name: "labels", Kind: kStringMap, Optional: true, Description: "Labels."},
		{Name: "status", Kind: kString, Optional: true, Computed: true, UpdateOnly: true, Enum: enumClusterStatus, Description: "active or disabled."},
		createdAtField(),
		updatedAtField(),
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreateClusterWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetCluster(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdateClusterWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeleteCluster(ctx, id, nil)
	},
}

// ----- gpudeck_price_plan --------------------------------------------------------------------

func priceField(name, desc string) field {
	return field{Name: name, Kind: kFloat, Optional: true, Default: 0, Description: desc}
}

var pricePlanDef = resourceDef{
	Name:        "price_plan",
	Description: "USD prices per GPU minute and per 1k tokens, attached to clusters or nodes.",
	Fields: []field{
		idField(),
		nameField("Plan name (unique)."),
		descriptionField(),
		priceField("usd_per_gpu_minute", "USD per GPU minute of instance uptime."),
		priceField("usd_per_1k_input_tokens", "USD per 1k input tokens."),
		priceField("usd_per_1k_output_tokens", "USD per 1k output tokens."),
		priceField("usd_per_1k_cached_tokens", "USD per 1k cached input tokens."),
		{Name: "is_default", Kind: kBool, Optional: true, Default: false, Description: "Use this plan where no cluster/node plan is set."},
		roString("currency", "Always USD."),
		createdAtField(),
		updatedAtField(),
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreatePricePlanWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetPricePlan(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdatePricePlanWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeletePricePlan(ctx, id, nil)
	},
}

// ----- gpudeck_join_token --------------------------------------------------------------------

var joinTokenDef = resourceDef{
	Name: "join_token",
	Description: "Token that lets machines join a cluster (`gpudeck-agent join`): one-time by " +
		"default, or a multi-use enrolment token (`max_uses` > 1) for Kubernetes DaemonSets and " +
		"node pools. Destroying the resource revokes the token if it can still be used. A token " +
		"revoked outside Terraform is created again on the next apply.",
	Fields: []field{
		idField(),
		{Name: "cluster_id", Kind: kString, Required: true, ForceNew: true, Description: "Cluster the machine joins."},
		{Name: "node_name_hint", Kind: kString, Optional: true, ForceNew: true, Description: "Name to give the node."},
		{Name: "labels", Kind: kStringMap, Optional: true, ForceNew: true, Description: "Labels applied to the node on join."},
		{Name: "ttl_minutes", Kind: kInt, Optional: true, ForceNew: true, CreateOnly: true, Default: 15, Description: "Token lifetime in minutes (1-1440 for a one-time token, up to 525600 for a multi-use one)."},
		{Name: "max_uses", Kind: kInt, Optional: true, ForceNew: true, Default: 1, Description: "Machines the token may enrol (1 = one-time token; node_name_hint must be empty when > 1)."},
		{Name: "use_count", Kind: kInt, ReadOnly: true, Description: "Machines that joined with the token so far."},
		{Name: "token", Kind: kString, ReadOnly: true, Stable: true, Sensitive: true, Description: "The token (only known to the run that created it)."},
		{Name: "join_command", Kind: kString, ReadOnly: true, Stable: true, Sensitive: true, Description: "Ready-to-run agent join command (contains the token)."},
		{Name: "ca_fingerprint_sha256", Kind: kString, ReadOnly: true, Stable: true, Description: "SHA-256 fingerprint of the internal CA the agent should pin."},
		{Name: "token_prefix", Kind: kString, ReadOnly: true, Stable: true, Description: "Non-secret token prefix."},
		{Name: "expires_at", Kind: kString, ReadOnly: true, Stable: true, Time: true, Description: "Expiry time."},
		roString("used_at", "When the token was used up (its last allowed join)."),
		roString("used_by_node_id", "Node created by the most recent join."),
		{Name: "install_command", Kind: kString, ReadOnly: true, Stable: true, Sensitive: true, Description: "One-line installer for a GPU machine; it talks only to the agent hub (contains the token)."},
		createdAtField(),
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreateJoinTokenWithBody(ctx, nil, client.ContentJSON, body)
	},
	ReadObject: readJoinToken,
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		resp, err := api.RevokeJoinToken(ctx, id, nil)
		if err == nil && (resp.StatusCode == http.StatusConflict || resp.StatusCode == http.StatusGone) {
			// already used or expired: nothing left to revoke
			resp.StatusCode = http.StatusNoContent
		}
		return resp, err
	},
}

// readJoinToken finds the token in GET /nodes/join-tokens (there is no GET by id).
func readJoinToken(ctx context.Context, api *client.API, id uuid.UUID, state values) (map[string]any, error) {
	params := &client.ListJoinTokensParams{ActiveOnly: ptr(false), PageSize: ptr(500)}
	if cid, err := uuid.Parse(state.str("cluster_id")); err == nil {
		params.ClusterId = &cid
	}
	for page := 1; page <= 100; page++ {
		params.Page = ptr(page)
		res, err := client.DecodeList(client.Raw(api.ListJoinTokens(ctx, params)))
		if err != nil {
			return nil, err
		}
		for _, item := range res.Items {
			if item["id"] == id.String() {
				if item["revoked_at"] != nil {
					return nil, nil
				}
				return item, nil
			}
		}
		if !res.HasMore {
			break
		}
	}
	return nil, nil
}

// ----- gpudeck_model -------------------------------------------------------------------------

var modelDef = resourceDef{
	Name:        "model",
	Description: "A registered model (Hugging Face, ModelScope or a local path such as Ollama / LM Studio files).",
	Fields: []field{
		idField(),
		{Name: "name", Kind: kString, Required: true, Description: "Unique name served to clients (`[A-Za-z0-9._:-]+`)."},
		{Name: "display_name", Kind: kString, Optional: true, Nullable: true, Description: "Display name."},
		{Name: "source", Kind: kString, Required: true, ForceNew: true, Enum: enumModelSource, Description: "Where the files come from."},
		{Name: "repo_id", Kind: kString, Required: true, ForceNew: true, Description: "Repository id (org/name) or absolute local path."},
		{Name: "family", Kind: kString, Optional: true, Nullable: true, KeepNull: true, Description: "Model family (qwen3, gemma3, ...; default: detected)."},
		{Name: "type", Kind: kString, Required: true, Enum: enumModelType, Description: "Model type."},
		{Name: "priority", Kind: kString, Optional: true, Computed: true, Enum: enumModelPriority, Description: "Eviction priority."},
		descriptionField(),
		{Name: "tool_parser_default", Kind: kString, Optional: true, Nullable: true, KeepNull: true, Description: "Default tool-call parser (default: detected)."},
		{Name: "trust_remote_code", Kind: kBool, Optional: true, Default: false, Description: "Allow remote code (vLLM/SGLang)."},
		{Name: "allow_pickle", Kind: kBool, Optional: true, Default: false, Description: "Allow pickle weight files."},
		{Name: "default_context_size", Kind: kInt, Optional: true, Nullable: true, Description: "Default context size in tokens."},
		jsonOpt("default_launch_flags", "Engine flags applied to every deployment of the model."),
		{Name: "aliases", Kind: kStringList, Optional: true, Description: "Extra names clients may use."},
		{Name: "tags", Kind: kStringList, Optional: true, Description: "Tags."},
		jsonOpt("metadata", "Arbitrary metadata."),
		{Name: "initial_version", Kind: kJSON, Optional: true, CreateOnly: true, Description: "ModelVersionCreate object for the first version (default: version `main` with all files). JSON-encoded."},
		{Name: "version_count", Kind: kInt, ReadOnly: true, Description: "Number of versions."},
		{Name: "deployment_count", Kind: kInt, ReadOnly: true, Description: "Number of deployments using the model."},
		createdAtField(),
		updatedAtField(),
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.RegisterModelWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetModel(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdateModelWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeleteModel(ctx, id, nil)
	},
}

// ----- gpudeck_rule --------------------------------------------------------------------------

var ruleDef = resourceDef{
	Name: "rule",
	Description: "Automation rule (idle unload, time schedule, low-traffic shrink, high-load grow, " +
		"make room by priority, auto-heal). `params` is validated against the rule schema at plan time.",
	Fields: []field{
		idField(),
		nameField("Rule name."),
		descriptionField(),
		{Name: "type", Kind: kString, Required: true, ForceNew: true, Enum: enumRuleType, Description: "Rule type."},
		{Name: "params", Kind: kJSON, Required: true, Description: "Parameters for the rule type (libs/contracts/rules/<type>.schema.json). JSON-encoded."},
		jsonOpt("target", "Target selector (deployments, models, nodes, labels)."),
		{Name: "enabled", Kind: kBool, Optional: true, Default: true, Description: "Evaluate the rule."},
		{Name: "dry_run", Kind: kBool, Optional: true, Default: false, Description: "Record what the rule would do without acting."},
		{Name: "cooldown_seconds", Kind: kInt, Optional: true, Default: 300, Description: "Minimum seconds between firings."},
		{Name: "evaluation_interval_seconds", Kind: kInt, Optional: true, Default: 30, Description: "Evaluation interval."},
		{Name: "priority", Kind: kInt, Optional: true, Default: 100, Description: "Lower runs first when rules conflict."},
		{Name: "last_evaluated_at", Kind: kString, ReadOnly: true, Time: true, Description: "Last evaluation."},
		{Name: "last_fired_at", Kind: kString, ReadOnly: true, Time: true, Description: "Last firing."},
		{Name: "fire_count", Kind: kInt, ReadOnly: true, Description: "Times fired."},
		createdAtField(),
		updatedAtField(),
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreateRuleWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetRule(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdateRuleWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeleteRule(ctx, id, nil)
	},
	Validate: func(_ context.Context, cfg values) diag.Diagnostics {
		var diags diag.Diagnostics
		ruleType, params := cfg.str("type"), jsonText(cfg["params"])
		if ruleType == "" || params == "" {
			return diags // unknown until apply
		}
		if err := rulesschema.ValidateParams(ruleType, params); err != nil {
			diags.AddAttributeError(path.Root("params"), "Invalid rule params", err.Error())
		}
		return diags
	},
}

// ----- gpudeck_alert_channel -----------------------------------------------------------------

var alertChannelDef = resourceDef{
	Name:        "alert_channel",
	Description: "Slack, email or webhook destination for alerts.",
	Fields: []field{
		idField(),
		nameField("Channel name."),
		{Name: "type", Kind: kString, Required: true, ForceNew: true, Enum: enumAlertChannelType, Description: "Channel type."},
		{Name: "config", Kind: kJSON, Required: true, Description: "Channel settings (slack {channel}, email {to, from}, webhook {url, headers}). JSON-encoded."},
		{Name: "credential_id", Kind: kString, Optional: true, Computed: true, Description: "Vault credential holding the secret (set by the server when `secret` is given)."},
		{Name: "secret", Kind: kString, Optional: true, WriteOnly: true, Nullable: true, Sensitive: true, Description: "Webhook URL with token / SMTP password / bot token, stored in the vault and never read back from the API; Terraform keeps the configured value in state (sensitive)."},
		{Name: "enabled", Kind: kBool, Optional: true, Default: true, Description: "Deliver alerts."},
		{Name: "min_severity", Kind: kString, Optional: true, Computed: true, Enum: enumAlertSeverity, Description: "Lowest severity delivered."},
		{Name: "event_filter", Kind: kStringList, Optional: true, Description: "Event types delivered (empty = all)."},
		{Name: "last_test_at", Kind: kString, ReadOnly: true, Time: true, Description: "Last test delivery."},
		{Name: "last_test_ok", Kind: kBool, ReadOnly: true, Description: "Whether the last test succeeded."},
		roString("last_error", "Last delivery error."),
		createdAtField(),
		updatedAtField(),
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreateAlertChannelWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetAlertChannel(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdateAlertChannelWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeleteAlertChannel(ctx, id, nil)
	},
}

// ----- gpudeck_api_key -----------------------------------------------------------------------

var apiKeyDef = resourceDef{
	Name: "api_key",
	Description: "API key for the serving API (used by your AI gateway or any client). The key is only returned " +
		"when created; it is kept in state as a sensitive value.",
	Fields: []field{
		idField(),
		nameField("Key name."),
		descriptionField(),
		{Name: "expires_at", Kind: kString, Optional: true, Nullable: true, Time: true, Description: "Expiry time (RFC 3339); null = never."},
		jsonOpt("metadata", "Arbitrary metadata."),
		{Name: "key", Kind: kString, ReadOnly: true, Stable: true, Sensitive: true, Description: "The full key (`gd-...`)."},
		{Name: "key_prefix", Kind: kString, ReadOnly: true, Stable: true, Description: "Non-secret key prefix."},
		roString("status", "active, expired, revoked or suspended."),
		{Name: "last_used_at", Kind: kString, ReadOnly: true, Time: true, Description: "Last use."},
		{Name: "total_requests", Kind: kInt, ReadOnly: true, Description: "Requests made with the key."},
		createdAtField(),
		updatedAtField(),
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreateApiKeyWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetApiKey(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdateApiKeyWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeleteApiKey(ctx, id, nil)
	},
}

// ----- gpudeck_ip_rule -----------------------------------------------------------------------

var ipRuleDef = resourceDef{
	Name: "ip_rule",
	Description: "IP allow/block rule for the admin API (`admin`) or the serving API (`api_layer`). " +
		"Block wins; any allow rule turns its scope into allow-list mode.",
	Fields: []field{
		idField(),
		{Name: "action", Kind: kString, Required: true, Enum: enumIPRuleAction, Description: "allow or block."},
		{Name: "scope", Kind: kString, Required: true, Enum: enumIPRuleScope, Description: "Which API the rule applies to."},
		{Name: "cidr", Kind: kString, Required: true, CIDR: true, Description: "IP address or CIDR."},
		descriptionField(),
		{Name: "enabled", Kind: kBool, Optional: true, Default: true, Description: "Rule is active."},
		{Name: "expires_at", Kind: kString, Optional: true, Nullable: true, Time: true, Description: "Expiry time (RFC 3339)."},
		createdAtField(),
		updatedAtField(),
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreateIpRuleWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetIpRule(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdateIpRuleWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeleteIpRule(ctx, id, nil)
	},
}

// ----- gpudeck_layout ------------------------------------------------------------------------

var layoutDef = resourceDef{
	Name: "layout",
	Description: "Named GPU layout (for example Day / Night): which deployments run where, with " +
		"replicas, slices and traffic weights. Apply it with `gpudeck layouts apply` or a " +
		"time_schedule rule.",
	Fields: []field{
		idField(),
		nameField("Layout name."),
		descriptionField(),
		{Name: "unlisted_policy", Kind: kString, Optional: true, Computed: true, Enum: []string{"keep", "unload"}, Description: "What happens to deployments not listed when the layout is applied."},
		{Name: "items", Kind: kObjectList, Required: true, Description: "Placements.", Elem: []field{
			{Name: "deployment_id", Kind: kString, Required: true, Description: "Deployment."},
			{Name: "node_id", Kind: kString, Optional: true, Description: "Pin to a node."},
			{Name: "device_ids", Kind: kStringList, Optional: true, Description: "Pin to devices."},
			{Name: "replicas", Kind: kInt, Optional: true, Default: 1, Description: "Replicas (0-64)."},
			{Name: "slice_vram_bytes", Kind: kInt, Optional: true, Description: "GPU slice size in bytes."},
			{Name: "slice_compute_pct", Kind: kInt, Optional: true, Description: "GPU slice compute share (1-100)."},
			{Name: "traffic_weight", Kind: kInt, Optional: true, Default: 100, Description: "Traffic weight (0-1000)."},
			{Name: "position", Kind: kInt, Optional: true, KeepNull: true, Description: "Order (defaults to list order)."},
		}},
		{Name: "is_active", Kind: kBool, ReadOnly: true, Description: "Layout currently applied."},
		{Name: "applied_at", Kind: kString, ReadOnly: true, Time: true, Description: "Last apply."},
		createdAtField(),
		updatedAtField(),
	},
	PrepareBody: func(body map[string]any, _ values, _ bool) {
		items, _ := body["items"].([]any)
		for _, it := range items {
			m, ok := it.(map[string]any)
			if !ok {
				continue
			}
			vram, hasVRAM := m["slice_vram_bytes"]
			pct, hasPct := m["slice_compute_pct"]
			delete(m, "slice_vram_bytes")
			delete(m, "slice_compute_pct")
			if hasVRAM {
				slice := map[string]any{"vram_bytes": vram}
				if hasPct {
					slice["compute_pct"] = pct
				}
				m["slice"] = slice
			}
		}
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreateLayoutWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetLayout(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdateLayoutWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeleteLayout(ctx, id, nil)
	},
}

// ----- constructors used by provider.Resources -------------------------------------------------

func NewClusterResource() resource.Resource      { return newGeneric(clusterDef)() }
func NewPricePlanResource() resource.Resource    { return newGeneric(pricePlanDef)() }
func NewJoinTokenResource() resource.Resource    { return newGeneric(joinTokenDef)() }
func NewModelResource() resource.Resource        { return newGeneric(modelDef)() }
func NewDeploymentResource() resource.Resource   { return newGeneric(deploymentDef)() }
func NewLayoutResource() resource.Resource       { return newGeneric(layoutDef)() }
func NewRuleResource() resource.Resource         { return newGeneric(ruleDef)() }
func NewAlertChannelResource() resource.Resource { return newGeneric(alertChannelDef)() }
func NewAPIKeyResource() resource.Resource       { return newGeneric(apiKeyDef)() }
func NewIPRuleResource() resource.Resource       { return newGeneric(ipRuleDef)() }

func ptr[T any](v T) *T { return &v }
