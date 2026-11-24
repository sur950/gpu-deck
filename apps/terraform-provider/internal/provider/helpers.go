package provider

// Small shared helpers for resources and data sources.

import (
	"context"
	"fmt"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"

	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/client"
)

func pathRoot(name string) path.Path { return path.Root(name) }

// apiFrom extracts the configured client from resource/data-source ProviderData.
func apiFrom(data any, diags *diag.Diagnostics) *client.API {
	if data == nil {
		return nil
	}
	api, ok := data.(*client.API)
	if !ok {
		diags.AddError("Unexpected provider data", fmt.Sprintf("expected *client.API, got %T", data))
		return nil
	}
	return api
}

func configureResource(req resource.ConfigureRequest, resp *resource.ConfigureResponse) *client.API {
	return apiFrom(req.ProviderData, &resp.Diagnostics)
}

func configureDataSource(req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) *client.API {
	return apiFrom(req.ProviderData, &resp.Diagnostics)
}

// readError converts an API error during Read: 404 removes the resource from state.
func readError(ctx context.Context, err error, resp *resource.ReadResponse, what string) {
	if client.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	resp.Diagnostics.AddError("Unable to read "+what, err.Error())
}
