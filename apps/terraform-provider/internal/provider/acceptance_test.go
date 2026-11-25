package provider

// Acceptance tests against a running GPU Deck admin API. They only run with TF_ACC=1 and
// GPUDECK_ENDPOINT plus GPUDECK_TOKEN or GPUDECK_USERNAME/GPUDECK_PASSWORD, for example:
//
//	TF_ACC=1 GPUDECK_ENDPOINT=http://localhost:8180 GPUDECK_TOKEN="$(session token)" \
//	  go test ./internal/provider -run TestAcc -v
//
// Prefer GPUDECK_TOKEN: with username/password every terraform command of every step logs in
// again and the login rate limit (10/min per IP) trips mid-test, leaving tfacc-* objects behind.
//
// Every object they create is named tfacc-* and destroyed at the end of the test.

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
)

func testAccPreCheck(t *testing.T) {
	t.Helper()
	if os.Getenv("GPUDECK_ENDPOINT") == "" {
		t.Fatal("GPUDECK_ENDPOINT must be set for acceptance tests")
	}
	if os.Getenv("GPUDECK_TOKEN") == "" && (os.Getenv("GPUDECK_USERNAME") == "" || os.Getenv("GPUDECK_PASSWORD") == "") {
		t.Fatal("GPUDECK_TOKEN or GPUDECK_USERNAME/GPUDECK_PASSWORD must be set for acceptance tests")
	}
}

func accName(kind string) string {
	return fmt.Sprintf("tfacc-%s-%d", kind, time.Now().UnixNano()%1_000_000)
}

func TestAccControlPlaneObjects(t *testing.T) {
	cluster, rule := accName("cluster"), accName("rule")
	cfg := func(desc string) string {
		return fmt.Sprintf(`
provider "gpudeck" {}

resource "gpudeck_cluster" "c" {
  name        = %[1]q
  type        = "bare_metal"
  description = %[3]q
  labels      = { owner = "tfacc" }
}

resource "gpudeck_join_token" "t" {
  cluster_id  = gpudeck_cluster.c.id
  ttl_minutes = 5
}

resource "gpudeck_rule" "idle" {
  name    = %[2]q
  type    = "idle_unload"
  params  = jsonencode({ idle_minutes = 60 })
  enabled = false
}

resource "gpudeck_ip_rule" "r" {
  action      = "block"
  scope       = "api_layer"
  cidr        = "198.51.100.7"
  description = "tfacc"
}

resource "gpudeck_api_key" "k" {
  name = %[1]q
}

resource "gpudeck_alert_channel" "a" {
  name    = %[1]q
  type    = "webhook"
  config  = jsonencode({ url = "https://example.invalid/hook" })
  enabled = false
}

data "gpudeck_nodes" "all" {}
data "gpudeck_devices" "all" {}
data "gpudeck_engines" "all" {}
`, cluster, rule, desc)
	}
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testFactories,
		Steps: []resource.TestStep{
			{
				Config: cfg("first"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttrSet("gpudeck_cluster.c", "id"),
					resource.TestCheckResourceAttrSet("gpudeck_join_token.t", "token"),
					resource.TestCheckResourceAttrSet("gpudeck_api_key.k", "key"),
					resource.TestCheckResourceAttrSet("data.gpudeck_engines.all", "engines.#"),
				),
			},
			{
				Config: cfg("second"),
				Check:  resource.TestCheckResourceAttr("gpudeck_cluster.c", "description", "second"),
			},
			{
				ResourceName:      "gpudeck_cluster.c",
				ImportState:       true,
				ImportStateVerify: true,
			},
		},
	})
}
