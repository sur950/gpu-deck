package provider

// Unit tests: the real provider over protocol v6 against the in-memory admin API
// (fake_api_test.go). terraform-plugin-testing runs `terraform` from PATH; every step also
// checks that a follow-up plan is empty, which exercises the read normalisation rules.

import (
	"fmt"
	"regexp"
	"testing"
	"time"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"
)

var testFactories = map[string]func() (tfprotov6.ProviderServer, error){
	"gpudeck": providerserver.NewProtocol6WithError(New("test")()),
}

func init() { pollInterval = 10 * time.Millisecond }

func unitTest(t *testing.T, steps ...resource.TestStep) {
	t.Helper()
	resource.UnitTest(t, resource.TestCase{ProtoV6ProviderFactories: testFactories, Steps: steps})
}

func TestProviderSchemaValid(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t, resource.TestStep{
		Config: api.providerBlock() + `data "gpudeck_engines" "all" {}`,
		Check:  resource.TestCheckResourceAttr("data.gpudeck_engines.all", "engines.#", "3"),
	})
}

func TestProviderBadCredentials(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t, resource.TestStep{
		Config: fmt.Sprintf(`
provider "gpudeck" {
  endpoint = %q
  username = "admin"
  password = "wrong"
}
data "gpudeck_engines" "all" {}
`, api.URL()),
		ExpectError: regexp.MustCompile(`INVALID_CREDENTIALS`),
	})
}

func TestProviderTokenAuth(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t, resource.TestStep{
		Config: fmt.Sprintf(`
provider "gpudeck" {
  endpoint = "%s/api/v1"
  token    = %q
}
data "gpudeck_nodes" "all" {}
`, api.URL(), fakeToken),
		Check: resource.TestCheckResourceAttr("data.gpudeck_nodes.all", "nodes.#", "3"),
	})
	if api.called("POST /auth/login") != 0 {
		t.Fatal("token auth must not log in")
	}
}

func TestClusterLifecycle(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t,
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_cluster" "lab" {
  name             = "lab"
  type             = "bare_metal"
  labels           = { zone = "office" }
  egress_allowlist = ["huggingface.co:443"]
  config           = jsonencode({ max_nodes = 4 })
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttrSet("gpudeck_cluster.lab", "id"),
				resource.TestCheckResourceAttr("gpudeck_cluster.lab", "status", "active"),
				resource.TestCheckResourceAttr("gpudeck_cluster.lab", "labels.zone", "office"),
				resource.TestCheckNoResourceAttr("gpudeck_cluster.lab", "description"),
			),
		},
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_cluster" "lab" {
  name        = "lab"
  type        = "bare_metal"
  description = "office rack"
  status      = "disabled"
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttr("gpudeck_cluster.lab", "status", "disabled"),
				resource.TestCheckResourceAttr("gpudeck_cluster.lab", "description", "office rack"),
				func(*terraform.State) error {
					body := api.lastBody("PATCH clusters")
					if body["status"] != "disabled" || body["description"] != "office rack" {
						return fmt.Errorf("unexpected PATCH body %v", body)
					}
					// removed attributes are cleared
					if fmt.Sprint(body["labels"]) != "map[]" || fmt.Sprint(body["egress_allowlist"]) != "[]" {
						return fmt.Errorf("labels/egress not cleared: %v", body)
					}
					if _, sent := body["name"]; sent {
						return fmt.Errorf("unchanged name must not be sent: %v", body)
					}
					return nil
				},
			),
		},
		resource.TestStep{
			ResourceName:      "gpudeck_cluster.lab",
			ImportState:       true,
			ImportStateVerify: true,
		},
	)
}

func TestClusterCreateWithStatusUsesFollowUpPatch(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t, resource.TestStep{
		Config: api.providerBlock() + `
resource "gpudeck_cluster" "c" {
  name   = "spare"
  type   = "vm"
  status = "disabled"
}`,
		Check: func(*terraform.State) error {
			if _, sent := api.lastBody("POST clusters")["status"]; sent {
				return fmt.Errorf("status must not be sent on create")
			}
			if api.lastBody("PATCH clusters")["status"] != "disabled" {
				return fmt.Errorf("status not applied with a follow-up PATCH")
			}
			return nil
		},
	})
}

func TestClusterRecreatedWhenDeletedOutOfBand(t *testing.T) {
	api := newFakeAPI(t)
	cfg := api.providerBlock() + `
resource "gpudeck_cluster" "c" {
  name = "gone"
  type = "vm"
}`
	unitTest(t,
		resource.TestStep{Config: cfg},
		resource.TestStep{
			PreConfig: func() { api.remove("clusters", api.firstID("clusters")) },
			Config:    cfg,
			Check: func(*terraform.State) error {
				if n := api.called("POST /clusters"); n != 2 {
					return fmt.Errorf("expected the cluster to be created twice, got %d", n)
				}
				return nil
			},
		},
	)
}

func TestPricePlanDefaultsStayNull(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t,
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_price_plan" "p" {
  name               = "standard"
  usd_per_gpu_minute = 0.05
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttr("gpudeck_price_plan.p", "usd_per_gpu_minute", "0.05"),
				resource.TestCheckNoResourceAttr("gpudeck_price_plan.p", "usd_per_1k_input_tokens"),
				resource.TestCheckNoResourceAttr("gpudeck_price_plan.p", "is_default"),
				resource.TestCheckResourceAttr("gpudeck_price_plan.p", "currency", "USD"),
			),
		},
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_price_plan" "p" {
  name                    = "standard"
  usd_per_1k_input_tokens = 0.001
}`,
			Check: func(*terraform.State) error {
				body := api.lastBody("PATCH price-plans")
				if body["usd_per_gpu_minute"] != float64(0) || body["usd_per_1k_input_tokens"] != 0.001 {
					return fmt.Errorf("unexpected PATCH body %v", body)
				}
				return nil
			},
		},
	)
}

func TestPricePlanDriftDetected(t *testing.T) {
	api := newFakeAPI(t)
	cfg := api.providerBlock() + `
resource "gpudeck_price_plan" "p" {
  name = "standard"
}`
	unitTest(t,
		resource.TestStep{Config: cfg},
		resource.TestStep{
			PreConfig: func() {
				api.mutate("price-plans", api.firstID("price-plans"), func(o map[string]any) {
					o["usd_per_gpu_minute"] = 9.0
				})
			},
			Config: cfg,
			Check: func(*terraform.State) error {
				if api.lastBody("PATCH price-plans")["usd_per_gpu_minute"] != float64(0) {
					return fmt.Errorf("drifted price not reset to the default")
				}
				return nil
			},
		},
	)
}

func TestJoinTokenCreateReadRevoke(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t,
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_join_token" "t" {
  cluster_id     = "11111111-1111-4111-8111-111111111111"
  node_name_hint = "mac-studio"
  ttl_minutes    = 30
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttr("gpudeck_join_token.t", "token", "gdj_secret_token"),
				resource.TestCheckResourceAttr("gpudeck_join_token.t", "ttl_minutes", "30"),
				resource.TestCheckResourceAttrSet("gpudeck_join_token.t", "expires_at"),
				resource.TestMatchResourceAttr("gpudeck_join_token.t", "join_command", regexp.MustCompile(`^gpudeck-agent join`)),
			),
		},
	)
	if api.called("DELETE /nodes/join-tokens/") != 1 {
		t.Fatal("destroy must revoke the token")
	}
	if api.called("GET /nodes/join-tokens") == 0 {
		t.Fatal("read must use the list endpoint")
	}
}

func TestMultiUseEnrolmentToken(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t,
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_join_token" "pool" {
  cluster_id  = "11111111-1111-4111-8111-111111111111"
  max_uses    = 50
  ttl_minutes = 43200
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttr("gpudeck_join_token.pool", "max_uses", "50"),
				resource.TestCheckResourceAttr("gpudeck_join_token.pool", "use_count", "0"),
				resource.TestMatchResourceAttr("gpudeck_join_token.pool", "install_command", regexp.MustCompile(`/agent/v1/install\.sh`)),
			),
		},
	)
}

func TestModelDeploymentLayoutChain(t *testing.T) {
	api := newFakeAPI(t)
	base := api.providerBlock() + `
resource "gpudeck_model" "qwen" {
  name    = "qwen3-0.6b"
  source  = "local_path"
  repo_id = "/models/qwen3-0.6b.gguf"
  type    = "chat"
  tags    = ["small"]
}
`
	unitTest(t,
		resource.TestStep{
			Config: base + `
resource "gpudeck_deployment" "qwen" {
  name           = "qwen3"
  model_id       = gpudeck_model.qwen.id
  replicas       = 1
  launch_flags   = jsonencode({ "--ctx-size" = 8192 })
  env_vars       = { LLAMA_LOG = "info" }
  wait_for_ready = true
}

resource "gpudeck_layout" "day" {
  name = "day"
  items = [{
    deployment_id     = gpudeck_deployment.qwen.id
    slice_vram_bytes  = 8589934592
    slice_compute_pct = 50
  }]
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttr("gpudeck_model.qwen", "priority", "normal"),
				resource.TestCheckResourceAttr("gpudeck_deployment.qwen", "status", "running"),
				resource.TestCheckResourceAttr("gpudeck_deployment.qwen", "running", "true"),
				resource.TestCheckResourceAttr("gpudeck_layout.day", "unlisted_policy", "keep"),
				resource.TestCheckResourceAttr("gpudeck_layout.day", "items.0.slice_compute_pct", "50"),
				func(*terraform.State) error {
					if api.lastBody("POST deployments")["start"] != true {
						return fmt.Errorf("deployment create must send start=true")
					}
					items := api.lastBody("POST layouts")["items"].([]any)
					item := items[0].(map[string]any)
					slice, ok := item["slice"].(map[string]any)
					if !ok || slice["vram_bytes"] != float64(8589934592) || slice["compute_pct"] != float64(50) {
						return fmt.Errorf("layout slice not encoded: %v", item)
					}
					if _, flat := item["slice_vram_bytes"]; flat {
						return fmt.Errorf("flat slice fields leaked into the body: %v", item)
					}
					return nil
				},
			),
		},
		resource.TestStep{
			Config: base + `
resource "gpudeck_deployment" "qwen" {
  name         = "qwen3"
  model_id     = gpudeck_model.qwen.id
  replicas     = 1
  launch_flags = jsonencode({ "--ctx-size" = 8192 })
  env_vars     = { LLAMA_LOG = "info" }
  running      = false
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttr("gpudeck_deployment.qwen", "running", "false"),
				resource.TestCheckResourceAttr("gpudeck_deployment.qwen", "status", "stopped"),
				func(*terraform.State) error {
					if api.called("/stop") != 1 {
						return fmt.Errorf("stop endpoint not called")
					}
					return nil
				},
			),
		},
		resource.TestStep{
			ResourceName:      "gpudeck_deployment.qwen",
			ImportState:       true,
			ImportStateVerify: true,
			// replicas = 1 equals the server default, which import leaves unset
			// server-filled values (engine/model version, parser, traffic) are not tracked
			// while unset in the configuration, but import reads them
			ImportStateVerifyIgnore: []string{"wait_for_ready", "wait_timeout_seconds", "status", "ready_replicas", "updated_at", "replicas",
				"engine_version_id", "model_version_id", "tool_parser", "traffic_config"},
		},
	)
	if api.called("DELETE /deployments/") != 1 || len(api.store["deployments"]) != 0 {
		t.Fatal("deployment not deleted (and waited for)")
	}
}

func TestRuleParamsValidatedAndDefaultsIgnored(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t,
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_rule" "idle" {
  name   = "idle"
  type   = "idle_unload"
  params = jsonencode({ idle_minutes = 0 })
}`,
			ExpectError: regexp.MustCompile(`Invalid rule params`),
		},
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_rule" "idle" {
  name   = "idle"
  type   = "idle_unload"
  params = jsonencode({ idle_minutes = 30 })
  target = jsonencode({ deployment_ids = [] })
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttr("gpudeck_rule.idle", "params", `{"idle_minutes":30}`),
				resource.TestCheckNoResourceAttr("gpudeck_rule.idle", "enabled"),
				resource.TestCheckResourceAttr("gpudeck_rule.idle", "fire_count", "0"),
			),
		},
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_rule" "idle" {
  name    = "idle"
  type    = "idle_unload"
  params  = jsonencode({ idle_minutes = 30 })
  target  = jsonencode({ deployment_ids = [] })
  enabled = false
}`,
			Check: func(*terraform.State) error {
				if api.lastBody("PATCH rules")["enabled"] != false {
					return fmt.Errorf("enabled=false not sent")
				}
				return nil
			},
		},
	)
}

func TestRuleUnknownType(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t, resource.TestStep{
		Config: api.providerBlock() + `
resource "gpudeck_rule" "x" {
  name   = "x"
  type   = "nope"
  params = jsonencode({})
}`,
		ExpectError: regexp.MustCompile(`value must be one of`),
	})
}

func TestAlertChannelSecretIsWriteOnly(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t,
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_alert_channel" "ops" {
  name   = "ops"
  type   = "webhook"
  config = jsonencode({ url = "https://hooks.example.com/x" })
  secret = "tok"
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttrSet("gpudeck_alert_channel.ops", "credential_id"),
				resource.TestCheckResourceAttr("gpudeck_alert_channel.ops", "secret", "tok"),
				resource.TestCheckResourceAttr("gpudeck_alert_channel.ops", "min_severity", "warning"),
			),
		},
		resource.TestStep{
			ResourceName:            "gpudeck_alert_channel.ops",
			ImportState:             true,
			ImportStateVerify:       true,
			ImportStateVerifyIgnore: []string{"secret"},
		},
	)
}

func TestAPIKeyShownOnce(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t,
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_api_key" "gw" {
  name       = "gateway"
  expires_at = "2027-01-01T00:00:00+00:00"
}`,
			Check: resource.ComposeAggregateTestCheckFunc(
				resource.TestCheckResourceAttr("gpudeck_api_key.gw", "key", "gd-abcdef-secret"),
				resource.TestCheckResourceAttr("gpudeck_api_key.gw", "status", "active"),
			),
		},
		resource.TestStep{
			PreConfig: func() {
				// the server normalises the timestamp; same instant must not be a diff
				api.mutate("api-keys", api.firstID("api-keys"), func(o map[string]any) {
					o["expires_at"] = "2027-01-01T00:00:00Z"
				})
			},
			Config: api.providerBlock() + `
resource "gpudeck_api_key" "gw" {
  name       = "gateway"
  expires_at = "2027-01-01T00:00:00+00:00"
}`,
			PlanOnly: true,
		},
	)
}

func TestIPRuleCIDRNormalised(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t,
		resource.TestStep{
			Config: api.providerBlock() + `
resource "gpudeck_ip_rule" "office" {
  action = "allow"
  scope  = "admin"
  cidr   = "203.0.113.7"
}`,
			Check: resource.TestCheckResourceAttr("gpudeck_ip_rule.office", "cidr", "203.0.113.7"),
		},
	)
	if got := api.called("PATCH /ip-rules"); got != 0 {
		t.Fatalf("unexpected update calls: %d", got)
	}
}

func TestDataSourcesFiltersAndPagination(t *testing.T) {
	api := newFakeAPI(t)
	unitTest(t, resource.TestStep{
		Config: api.providerBlock() + `
data "gpudeck_nodes" "all" {}
data "gpudeck_nodes" "mac" {
  os = "macos"
}
data "gpudeck_nodes" "labelled" {
  labels = ["zone=lab"]
}
data "gpudeck_devices" "nvidia" {
  vendor = "nvidia"
}
data "gpudeck_engines" "apple_chat" {
  vendor     = "apple"
  model_type = "chat"
  enabled    = true
}`,
		Check: resource.ComposeAggregateTestCheckFunc(
			resource.TestCheckResourceAttr("data.gpudeck_nodes.all", "nodes.#", "3"),
			resource.TestCheckResourceAttr("data.gpudeck_nodes.mac", "nodes.#", "1"),
			resource.TestCheckResourceAttr("data.gpudeck_nodes.mac", "nodes.0.name", "mac-1"),
			resource.TestCheckResourceAttr("data.gpudeck_nodes.labelled", "nodes.#", "3"),
			resource.TestCheckResourceAttr("data.gpudeck_nodes.all", "nodes.0.labels.zone", "lab"),
			resource.TestCheckResourceAttr("data.gpudeck_devices.nvidia", "devices.#", "1"),
			resource.TestCheckResourceAttr("data.gpudeck_devices.nvidia", "devices.0.compute_capability", "8.9"),
			resource.TestCheckResourceAttr("data.gpudeck_engines.apple_chat", "engines.#", "2"),
		),
	})
	if api.called("GET /nodes") < 2 {
		t.Fatal("expected the nodes list to be paged")
	}
}
