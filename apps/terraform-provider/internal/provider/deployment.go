package provider

// gpudeck_deployment: DeploymentSpec fields plus start/stop and optional waits.

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"
	"github.com/hashicorp/terraform-plugin-framework/diag"

	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/client"
)

// pollInterval is how often waits re-read a deployment (tests shorten it).
var pollInterval = 5 * time.Second

const defaultWaitSeconds = 600

func intOpt(name string, def any, desc string) field {
	return field{Name: name, Kind: kInt, Optional: true, Default: def, Description: desc}
}

var deploymentDef = resourceDef{
	Name: "deployment",
	Description: "A model deployment: engine, placement (spread/pack, GPU slices, TP/PP), replicas " +
		"and launch settings. Deleting is asynchronous; the provider waits until the deployment " +
		"is gone (up to `wait_timeout_seconds`).",
	Fields: []field{
		idField(),
		nameField("Deployment name (the model name clients call unless aliases are used)."),
		descriptionField(),
		{Name: "model_id", Kind: kString, Required: true, ForceNew: true, Description: "Model to serve."},
		keepNull(uuidOpt("model_version_id", "Model version (default: the model's default version).")),
		keepNull(uuidOpt("engine_version_id", "Engine version (default: auto-pick).")),
		uuidOpt("cluster_id", "Restrict placement to a cluster."),
		{Name: "priority_override", Kind: kString, Optional: true, Nullable: true, Enum: enumModelPriority, Description: "Override the model priority."},
		intOpt("replicas", 1, "Desired replicas."),
		intOpt("min_replicas", 0, "Lower bound for automation rules."),
		intOpt("max_replicas", 1, "Upper bound for automation rules."),
		intOpt("gpu_count", 1, "GPUs per replica."),
		{Name: "slice_vram_bytes", Kind: kInt, Optional: true, Nullable: true, Description: "GPU slice size per replica in bytes."},
		{Name: "slice_compute_pct", Kind: kInt, Optional: true, Nullable: true, Description: "GPU slice compute share (1-100)."},
		intOpt("tp_size", 1, "Tensor parallel size."),
		intOpt("pp_size", 1, "Pipeline parallel size."),
		{Name: "preset", Kind: kString, Optional: true, Nullable: true, Enum: enumPreset, Description: "Tuning preset."},
		{Name: "pd_split", Kind: kBool, Optional: true, Default: false, Description: "Prefill/decode split."},
		jsonOpt("pd_config", "Prefill/decode split settings."),
		jsonOpt("spec_decoding", "Speculative decoding settings."),
		{Name: "kv_sharing", Kind: kBool, Optional: true, Default: false, Description: "Shared KV cache."},
		jsonOpt("kv_sharing_config", "Shared KV cache settings."),
		jsonOpt("launch_flags", "Engine flags: {\"flag\": value}; true = bare flag."),
		{Name: "env_vars", Kind: kStringMap, Optional: true, Description: "Environment variables for the engine."},
		{Name: "tool_parser", Kind: kString, Optional: true, Nullable: true, KeepNull: true, Description: "Tool-call parser (default: the model's)."},
		{Name: "context_size", Kind: kInt, Optional: true, Nullable: true, Description: "Context size in tokens."},
		jsonOpt("control_vectors", "Control vectors (array of objects)."),
		{Name: "placement_strategy", Kind: kString, Optional: true, Computed: true, Enum: enumPlacement, Description: "spread or pack."},
		jsonOpt("node_selector", "Node label selector."),
		{Name: "device_vendor_filter", Kind: kStringList, Optional: true, Enum: enumDeviceVendor, Description: "Only place on these vendors."},
		keepNull(jsonOpt("traffic_config", "Load balancing / failover settings.")),
		{Name: "running", API: "desired_running", Kind: kBool, Optional: true, Computed: true, Custom: true,
			Description: "Desired run state; false stops all instances (default true)."},
		{Name: "wait_for_ready", Kind: kBool, Optional: true, Local: true,
			Description: "After create/update wait until status is `running` (default false)."},
		{Name: "wait_timeout_seconds", Kind: kInt, Optional: true, Local: true,
			Description: "Timeout for waits, including delete (default 600)."},
		roString("status", "pending, scheduling, deploying, running, degraded, scaling, stopped, failed or deleting."),
		roString("status_message", "Detail for the status."),
		{Name: "ready_replicas", Kind: kInt, ReadOnly: true, Description: "Replicas ready to serve."},
		roString("model_name", "Model name."),
		roString("engine_name", "Engine picked."),
		roString("engine_version", "Engine version picked."),
		createdAtField(),
		updatedAtField(),
	},
	PrepareBody: func(body map[string]any, plan values, create bool) {
		if create {
			body["start"] = plan.boolean("running", true)
		}
	},
	Create: func(ctx context.Context, api *client.API, body io.Reader) (*http.Response, error) {
		return api.CreateDeploymentWithBody(ctx, nil, client.ContentJSON, body)
	},
	Read: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.GetDeployment(ctx, id)
	},
	Update: func(ctx context.Context, api *client.API, id uuid.UUID, body io.Reader) (*http.Response, error) {
		return api.UpdateDeploymentWithBody(ctx, id, nil, client.ContentJSON, body)
	},
	Delete: func(ctx context.Context, api *client.API, id uuid.UUID) (*http.Response, error) {
		return api.DeleteDeployment(ctx, id, nil)
	},
	AfterCreate: func(ctx context.Context, api *client.API, id uuid.UUID, obj map[string]any, plan values) (map[string]any, error) {
		if !plan.boolean("wait_for_ready", false) || !plan.boolean("running", true) {
			return obj, nil
		}
		return waitDeploymentRunning(ctx, api, id, waitTimeout(plan))
	},
	AfterUpdate: func(ctx context.Context, api *client.API, id uuid.UUID, plan, state values) (map[string]any, error) {
		want := plan["running"]
		if known(want) && !want.Equal(state["running"]) {
			var status int
			var body []byte
			var err error
			if plan.boolean("running", true) {
				status, body, err = client.Raw(api.StartDeployment(ctx, id, nil))
			} else {
				status, body, err = client.Raw(api.StopDeployment(ctx, id, nil))
			}
			if err == nil {
				err = client.Check(status, body, http.StatusOK, http.StatusAccepted)
			}
			if err != nil {
				return nil, err
			}
		}
		if plan.boolean("wait_for_ready", false) && plan.boolean("running", true) {
			return waitDeploymentRunning(ctx, api, id, waitTimeout(plan))
		}
		return nil, nil
	},
	AfterDelete: func(ctx context.Context, api *client.API, id uuid.UUID, state values) diag.Diagnostics {
		var diags diag.Diagnostics
		deadline := time.Now().Add(waitTimeout(state))
		for {
			_, err := client.DecodeObject(client.Raw(api.GetDeployment(ctx, id)))
			if client.IsNotFound(err) {
				return diags
			}
			if time.Now().After(deadline) {
				diags.AddWarning("Deployment still deleting",
					fmt.Sprintf("deployment %s was not gone after %s; it is being removed in the background", id, waitTimeout(state)))
				return diags
			}
			if !sleepCtx(ctx, pollInterval) {
				diags.AddWarning("Stopped waiting for deployment deletion", ctx.Err().Error())
				return diags
			}
		}
	},
}

func waitTimeout(v values) time.Duration {
	return time.Duration(v.integer("wait_timeout_seconds", defaultWaitSeconds)) * time.Second
}

// waitDeploymentRunning polls until status is running; failed / stopped end the wait with an
// error, as does the timeout.
func waitDeploymentRunning(ctx context.Context, api *client.API, id uuid.UUID, timeout time.Duration) (map[string]any, error) {
	deadline := time.Now().Add(timeout)
	var last map[string]any
	for {
		obj, err := client.DecodeObject(client.Raw(api.GetDeployment(ctx, id)))
		if err != nil {
			return last, err
		}
		last = obj
		status, _ := obj["status"].(string)
		switch status {
		case "running":
			return obj, nil
		case "failed", "stopped":
			msg, _ := obj["status_message"].(string)
			return obj, fmt.Errorf("deployment %s is %s: %s", id, status, msg)
		}
		if time.Now().After(deadline) {
			return obj, fmt.Errorf("deployment %s still %q after %s", id, status, timeout)
		}
		if !sleepCtx(ctx, pollInterval) {
			return obj, ctx.Err()
		}
	}
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
