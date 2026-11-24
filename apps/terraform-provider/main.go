// terraform-provider-gpudeck serves the gpudeck Terraform provider over protocol v6.
//
// Build: go build -o terraform-provider-gpudeck .
// Dev override (~/.terraformrc):
//
//	provider_installation {
//	  dev_overrides { "registry.terraform.io/sur950/gpudeck" = "/path/to/apps/terraform-provider" }
//	  direct {}
//	}
package main

import (
	"context"
	"flag"
	"log"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"

	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/provider"
)

// version is set by -ldflags "-X main.version=1.2.3" in CI releases.
var version = "1.0.0"

func main() {
	var debug bool
	flag.BoolVar(&debug, "debug", false, "run with support for debuggers (delve)")
	flag.Parse()

	err := providerserver.Serve(context.Background(), provider.New(version), providerserver.ServeOpts{
		Address: "registry.terraform.io/sur950/gpudeck",
		Debug:   debug,
	})
	if err != nil {
		log.Fatal(err)
	}
}
