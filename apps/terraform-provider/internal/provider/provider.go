// Package provider implements the gpudeck Terraform provider (terraform-plugin-framework).
package provider

import (
	"context"
	"os"
	"time"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/client"
)

// Ensure the implementation satisfies the framework interfaces.
var _ provider.Provider = &gpudeckProvider{}

type gpudeckProvider struct {
	version string
}

type providerModel struct {
	Endpoint           types.String `tfsdk:"endpoint"`
	Username           types.String `tfsdk:"username"`
	Password           types.String `tfsdk:"password"`
	Token              types.String `tfsdk:"token"`
	InsecureSkipVerify types.Bool   `tfsdk:"insecure_skip_verify"`
	TimeoutSeconds     types.Int64  `tfsdk:"timeout_seconds"`
}

// New returns the provider factory used by main.go and the tests.
func New(version string) func() provider.Provider {
	return func() provider.Provider {
		return &gpudeckProvider{version: version}
	}
}

func (p *gpudeckProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = "gpudeck"
	resp.Version = p.version
}

func (p *gpudeckProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Manage a GPU Deck control plane (clusters, models, deployments, rules, " +
			"layouts, alerts, API keys, IP rules) through its admin API.",
		Attributes: map[string]schema.Attribute{
			"endpoint": schema.StringAttribute{
				Optional:    true,
				Description: "Admin API endpoint, e.g. https://gpudeck.example.com (env GPUDECK_ENDPOINT).",
			},
			"username": schema.StringAttribute{
				Optional:    true,
				Description: "Admin username (env GPUDECK_USERNAME). Used with password to log in.",
			},
			"password": schema.StringAttribute{
				Optional:    true,
				Sensitive:   true,
				Description: "Admin password (env GPUDECK_PASSWORD).",
			},
			"token": schema.StringAttribute{
				Optional:  true,
				Sensitive: true,
				Description: "Existing session token sent as `Authorization: Bearer` (env GPUDECK_TOKEN). " +
					"Takes precedence over username/password.",
			},
			"insecure_skip_verify": schema.BoolAttribute{
				Optional:    true,
				Description: "Skip TLS certificate verification (env GPUDECK_INSECURE=1). Not for production.",
			},
			"timeout_seconds": schema.Int64Attribute{
				Optional:    true,
				Description: "HTTP timeout per request (default 60).",
			},
		},
	}
}

func (p *gpudeckProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var cfg providerModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() {
		return
	}
	c := client.Config{
		Endpoint:           firstNonEmpty(cfg.Endpoint.ValueString(), os.Getenv("GPUDECK_ENDPOINT")),
		Username:           firstNonEmpty(cfg.Username.ValueString(), os.Getenv("GPUDECK_USERNAME")),
		Password:           firstNonEmpty(cfg.Password.ValueString(), os.Getenv("GPUDECK_PASSWORD")),
		Token:              firstNonEmpty(cfg.Token.ValueString(), os.Getenv("GPUDECK_TOKEN")),
		InsecureSkipVerify: cfg.InsecureSkipVerify.ValueBool() || isTruthy(os.Getenv("GPUDECK_INSECURE")),
	}
	if !cfg.TimeoutSeconds.IsNull() && cfg.TimeoutSeconds.ValueInt64() > 0 {
		c.Timeout = time.Duration(cfg.TimeoutSeconds.ValueInt64()) * time.Second
	}
	if c.Endpoint == "" {
		resp.Diagnostics.AddAttributeError(pathRoot("endpoint"), "Missing endpoint",
			"Set the endpoint attribute or the GPUDECK_ENDPOINT environment variable.")
		return
	}
	if c.Token == "" && (c.Username == "" || c.Password == "") {
		resp.Diagnostics.AddError("Missing credentials",
			"Set token (GPUDECK_TOKEN) or username and password (GPUDECK_USERNAME / GPUDECK_PASSWORD).")
		return
	}
	api, err := client.New(ctx, c)
	if err != nil {
		resp.Diagnostics.AddError("Unable to connect to GPU Deck", err.Error())
		return
	}
	resp.DataSourceData = api
	resp.ResourceData = api
}

func (p *gpudeckProvider) Resources(_ context.Context) []func() resource.Resource {
	return []func() resource.Resource{
		NewClusterResource,
		NewJoinTokenResource,
		NewModelResource,
		NewDeploymentResource,
		NewLayoutResource,
		NewRuleResource,
		NewAlertChannelResource,
		NewAPIKeyResource,
		NewIPRuleResource,
	}
}

func (p *gpudeckProvider) DataSources(_ context.Context) []func() datasource.DataSource {
	return []func() datasource.DataSource{
		NewNodesDataSource,
		NewDevicesDataSource,
		NewEnginesDataSource,
	}
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if v != "" {
			return v
		}
	}
	return ""
}

func isTruthy(v string) bool {
	switch v {
	case "1", "true", "TRUE", "yes", "on":
		return true
	}
	return false
}
