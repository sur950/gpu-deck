package provider

// In-memory admin API used by the unit tests (httptest). It implements the subset of
// admin.yaml the provider calls, including the server-side behaviours the provider must cope
// with: filled-in defaults, CIDR canonicalisation, one-time secrets, async deployment status
// and deletion, list pagination.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

const (
	fakeUser     = "admin"
	fakePassword = "s3cret"
	fakeToken    = "sess-token-123"
)

type fakeAPI struct {
	t      *testing.T
	srv    *httptest.Server
	mu     sync.Mutex
	store  map[string]map[string]map[string]any // collection -> id -> object
	calls  []string                             // "METHOD /path"
	bodies map[string][]map[string]any          // "METHOD collection" -> request bodies
	gets   map[string]int                       // deployment id -> GET count
}

func newFakeAPI(t *testing.T) *fakeAPI {
	t.Helper()
	f := &fakeAPI{
		t:      t,
		store:  map[string]map[string]map[string]any{},
		bodies: map[string][]map[string]any{},
		gets:   map[string]int{},
	}
	f.seedInventory()
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeAPI) URL() string { return f.srv.URL }

func (f *fakeAPI) providerBlock() string {
	return fmt.Sprintf(`
provider "gpudeck" {
  endpoint = %q
  username = %q
  password = %q
}
`, f.srv.URL, fakeUser, fakePassword)
}

// collections served with plain CRUD (path prefix -> store key).
var fakeCollections = []string{
	"nodes/join-tokens", "alert-channels", "clusters", "models", "deployments",
	"layouts", "rules", "api-keys", "ip-rules",
}

func (f *fakeAPI) called(substr string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, c := range f.calls {
		if strings.Contains(c, substr) {
			n++
		}
	}
	return n
}

func (f *fakeAPI) lastBody(key string) map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	b := f.bodies[key]
	if len(b) == 0 {
		return nil
	}
	return b[len(b)-1]
}

func (f *fakeAPI) object(collection, id string) map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.store[collection][id]
}

func (f *fakeAPI) mutate(collection, id string, fn func(map[string]any)) {
	f.mu.Lock()
	defer f.mu.Unlock()
	fn(f.store[collection][id])
}

func (f *fakeAPI) remove(collection, id string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.store[collection], id)
}

func (f *fakeAPI) firstID(collection string) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	for id := range f.store[collection] {
		return id
	}
	return ""
}

// ----- HTTP ------------------------------------------------------------------------------------

func (f *fakeAPI) handle(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	p := strings.TrimPrefix(r.URL.Path, "/api/v1/")
	f.calls = append(f.calls, r.Method+" /"+p)

	if p == "auth/login" && r.Method == http.MethodPost {
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["username"] != fakeUser || body["password"] != fakePassword {
			writeErr(w, 401, "INVALID_CREDENTIALS", "bad username or password")
			return
		}
		http.SetCookie(w, &http.Cookie{Name: "gpudeck_session", Value: fakeToken, HttpOnly: true})
		http.SetCookie(w, &http.Cookie{Name: "gpudeck_csrf", Value: "csrf"})
		writeEnv(w, 200, map[string]any{"session_id": uuid.NewString(), "csrf_token": "csrf"})
		return
	}
	if r.Header.Get("Authorization") != "Bearer "+fakeToken {
		writeErr(w, 401, "AUTHENTICATION_ERROR", "missing or bad bearer token")
		return
	}
	if r.Header.Get("X-CSRF-Token") != "" {
		f.t.Errorf("provider sent a CSRF header with bearer auth")
	}

	switch {
	case r.Method == http.MethodGet && (p == "nodes" || p == "devices" || p == "engines"):
		f.list(w, r, p)
		return
	case r.Method == http.MethodGet && p == "nodes/join-tokens":
		f.list(w, r, p)
		return
	}

	for _, coll := range fakeCollections {
		if p == coll {
			if r.Method != http.MethodPost {
				f.list(w, r, coll)
				return
			}
			f.create(w, r, coll)
			return
		}
		if strings.HasPrefix(p, coll+"/") {
			rest := strings.Split(strings.TrimPrefix(p, coll+"/"), "/")
			f.item(w, r, coll, rest[0], rest[1:])
			return
		}
	}
	writeErr(w, 404, "RESOURCE_NOT_FOUND", "no route "+p)
}

func (f *fakeAPI) decode(r *http.Request, key string) map[string]any {
	body := map[string]any{}
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&body)
	}
	f.bodies[key] = append(f.bodies[key], body)
	return body
}

func (f *fakeAPI) create(w http.ResponseWriter, r *http.Request, coll string) {
	body := f.decode(r, "POST "+coll)
	now := time.Now().UTC().Format(time.RFC3339)
	obj := map[string]any{"id": uuid.NewString(), "created_at": now, "updated_at": now}
	for k, v := range fakeDefaults(coll) {
		obj[k] = v
	}
	for k, v := range body {
		obj[k] = v
	}
	status := 201
	switch coll {
	case "clusters":
		if _, ok := body["status"]; ok {
			writeErr(w, 422, "VALIDATION_ERROR", "status is not accepted on create")
			return
		}
	case "api-keys":
		obj["key"] = "gd-abcdef-secret"
		obj["key_prefix"] = "gd-abcdef"
	case "nodes/join-tokens":
		ttl, _ := body["ttl_minutes"].(float64)
		if ttl == 0 {
			ttl = 15
		}
		delete(obj, "ttl_minutes")
		obj["token"] = "gdj_secret_token"
		obj["token_prefix"] = "gdj_"
		obj["join_command"] = "gpudeck-agent join --token gdj_secret_token"
		obj["install_command"] = "curl -fsSL -H 'X-GPUDeck-Join-Token: gdj_secret_token' 'https://hub:8443/agent/v1/install.sh' | sh"
		setDefault(obj, "max_uses", float64(1))
		obj["use_count"] = float64(0)
		obj["ca_fingerprint_sha256"] = "ab:cd"
		obj["expires_at"] = time.Now().Add(time.Duration(ttl) * time.Minute).UTC().Format(time.RFC3339)
		obj["revoked_at"] = nil
	case "models":
		setDefault(obj, "family", "qwen3")
		setDefault(obj, "tool_parser_default", "jinja")
	case "deployments":
		start, _ := body["start"].(bool)
		delete(obj, "start")
		delete(obj, "dry_run")
		obj["desired_running"] = start
		// values the real server fills in when the request leaves them out
		setDefault(obj, "engine_version_id", "5b832c48-85dc-40b9-866b-6bfb026ceac5")
		setDefault(obj, "model_version_id", "0936d146-291b-4ced-a7bb-0796ccb92371")
		setDefault(obj, "tool_parser", "jinja")
		setDefault(obj, "traffic_config", map[string]any{"strategy": "least_sessions", "failover": true})
		obj["status"] = "pending"
		if !start {
			obj["status"] = "stopped"
		}
	}
	if err := fakeTransform(coll, obj, body); err != "" {
		writeErr(w, 422, "VALIDATION_ERROR", err)
		return
	}
	if f.store[coll] == nil {
		f.store[coll] = map[string]map[string]any{}
	}
	f.store[coll][obj["id"].(string)] = obj
	writeEnv(w, status, obj)
	if coll == "api-keys" || coll == "nodes/join-tokens" {
		// secrets are only returned by the create call
		delete(obj, "key")
		delete(obj, "token")
		delete(obj, "join_command")
		delete(obj, "install_command")
	}
}

func (f *fakeAPI) item(w http.ResponseWriter, r *http.Request, coll, id string, rest []string) {
	obj, ok := f.store[coll][id]
	if !ok {
		writeErr(w, 404, "RESOURCE_NOT_FOUND", coll+" "+id+" not found")
		return
	}
	switch {
	case r.Method == http.MethodGet && len(rest) == 0:
		if coll == "deployments" {
			f.gets[id]++
			switch {
			case obj["status"] == "deleting":
				delete(f.store[coll], id)
				writeErr(w, 404, "RESOURCE_NOT_FOUND", "deployment deleted")
				return
			case obj["desired_running"] == true && f.gets[id] >= 2:
				obj["status"] = "running"
				obj["ready_replicas"] = obj["replicas"]
			}
		}
		writeEnv(w, 200, obj)
	case r.Method == http.MethodPatch:
		body := f.decode(r, "PATCH "+coll)
		for k, v := range body {
			obj[k] = v
		}
		if err := fakeTransform(coll, obj, body); err != "" {
			writeErr(w, 422, "VALIDATION_ERROR", err)
			return
		}
		obj["updated_at"] = time.Now().UTC().Add(time.Second).Format(time.RFC3339)
		writeEnv(w, 200, obj)
	case r.Method == http.MethodDelete:
		if coll == "deployments" {
			obj["status"] = "deleting"
			writeEnv(w, 202, map[string]any{"id": id})
			return
		}
		if coll == "nodes/join-tokens" {
			obj["revoked_at"] = time.Now().UTC().Format(time.RFC3339)
			w.WriteHeader(204)
			return
		}
		delete(f.store[coll], id)
		w.WriteHeader(204)
	case r.Method == http.MethodPost && coll == "deployments" && len(rest) == 1 && (rest[0] == "start" || rest[0] == "stop"):
		obj["desired_running"] = rest[0] == "start"
		f.gets[id] = 0
		if rest[0] == "stop" {
			obj["status"] = "stopped"
		} else {
			obj["status"] = "deploying"
		}
		writeEnv(w, 202, map[string]any{"id": id})
	default:
		writeErr(w, 405, "METHOD_NOT_ALLOWED", r.Method+" "+r.URL.Path)
	}
}

// list serves list endpoints with a fixed page size of 2 to exercise pagination.
func (f *fakeAPI) list(w http.ResponseWriter, r *http.Request, coll string) {
	q := r.URL.Query()
	var items []map[string]any
	for _, obj := range f.store[coll] {
		if !matches(obj, q) {
			continue
		}
		items = append(items, obj)
	}
	sortByID(items)
	if coll == "engines" { // not paginated
		writeEnv(w, 200, items)
		return
	}
	page, _ := strconv.Atoi(q.Get("page"))
	if page < 1 {
		page = 1
	}
	const size = 2
	start, end := (page-1)*size, page*size
	if start > len(items) {
		start = len(items)
	}
	if end > len(items) {
		end = len(items)
	}
	data := items[start:end]
	if data == nil {
		data = []map[string]any{}
	}
	writeJSON(w, 200, map[string]any{"success": true, "data": data, "error": nil,
		"meta": map[string]any{"total": len(items), "page": page, "page_size": size, "has_more": end < len(items)}})
}

func matches(obj map[string]any, q map[string][]string) bool {
	for key, vals := range q {
		switch key {
		case "page", "page_size", "sort", "search", "free_vram_min_bytes":
			continue
		case "active_only":
			if vals[0] == "true" && obj["revoked_at"] != nil {
				return false
			}
			continue
		case "label":
			labels, _ := obj["labels"].(map[string]any)
			for _, kv := range vals {
				k, v, _ := strings.Cut(kv, "=")
				if labels[k] != v {
					return false
				}
			}
			continue
		case "enabled":
			key = "is_enabled"
		case "model_type":
			types, _ := obj["supported_model_types"].([]any)
			if !containsAny(types, vals[0]) {
				return false
			}
			continue
		}
		if fmt.Sprint(obj[key]) != vals[0] {
			if list, ok := obj["supported_vendors"].([]any); ok && key == "vendor" && containsAny(list, vals[0]) {
				continue
			}
			return false
		}
	}
	return true
}

func containsAny(list []any, v string) bool {
	for _, it := range list {
		if it == v {
			return true
		}
	}
	return false
}

func sortByID(items []map[string]any) {
	for i := 1; i < len(items); i++ {
		for j := i; j > 0 && fmt.Sprint(items[j]["id"]) < fmt.Sprint(items[j-1]["id"]); j-- {
			items[j], items[j-1] = items[j-1], items[j]
		}
	}
}

// fakeDefaults are the server-side defaults the real API fills in.
func fakeDefaults(coll string) map[string]any {
	switch coll {
	case "clusters":
		return map[string]any{"status": "active", "labels": map[string]any{}, "egress_allowlist": []any{}, "config": map[string]any{}, "description": nil, "region": nil}
	case "models":
		return map[string]any{"priority": "normal", "trust_remote_code": false, "allow_pickle": false, "aliases": []any{}, "tags": []any{}, "metadata": map[string]any{}, "default_launch_flags": map[string]any{}, "version_count": 1, "deployment_count": 0}
	case "deployments":
		return map[string]any{"replicas": 1, "min_replicas": 0, "max_replicas": 1, "gpu_count": 1, "tp_size": 1, "pp_size": 1, "pd_split": false, "kv_sharing": false, "placement_strategy": "spread", "launch_flags": map[string]any{}, "env_vars": map[string]any{}, "node_selector": map[string]any{}, "control_vectors": []any{}, "ready_replicas": 0, "model_name": "qwen3-0.6b"}
	case "layouts":
		return map[string]any{"unlisted_policy": "keep", "is_active": false, "applied_at": nil}
	case "rules":
		return map[string]any{"enabled": true, "dry_run": false, "cooldown_seconds": 300, "evaluation_interval_seconds": 30, "priority": 100, "target": map[string]any{}, "fire_count": 0}
	case "alert-channels":
		return map[string]any{"enabled": true, "min_severity": "warning", "event_filter": []any{}, "credential_id": nil}
	case "api-keys":
		return map[string]any{"status": "active", "total_requests": 0, "metadata": map[string]any{}, "expires_at": nil}
	case "ip-rules":
		return map[string]any{"enabled": true, "expires_at": nil}
	case "nodes/join-tokens":
		return map[string]any{"labels": map[string]any{}, "used_at": nil, "used_by_node_id": nil}
	}
	return nil
}

// fakeTransform applies the representation changes the real API makes on write.
func fakeTransform(coll string, obj, body map[string]any) string {
	switch coll {
	case "ip-rules":
		if c, ok := body["cidr"].(string); ok {
			if !strings.Contains(c, "/") {
				addr, err := netip.ParseAddr(c)
				if err != nil {
					return "invalid cidr"
				}
				c = netip.PrefixFrom(addr, addr.BitLen()).String()
			}
			obj["cidr"] = c
		}
	case "rules":
		if params, ok := body["params"].(map[string]any); ok && obj["type"] == "idle_unload" {
			if _, set := params["min_loaded_minutes"]; !set {
				params["min_loaded_minutes"] = 10 // server fills schema defaults
			}
		}
	case "alert-channels":
		if s, ok := body["secret"].(string); ok && s != "" {
			obj["credential_id"] = uuid.NewString()
		}
		delete(obj, "secret")
	case "layouts":
		if items, ok := body["items"].([]any); ok {
			out := make([]any, 0, len(items))
			for i, it := range items {
				in := it.(map[string]any)
				item := map[string]any{"id": uuid.NewString(), "position": i, "replicas": 1,
					"traffic_weight": 100, "deployment_name": "dep", "node_id": nil, "device_ids": []any{},
					"slice_vram_bytes": nil, "slice_compute_pct": nil}
				for k, v := range in {
					item[k] = v
				}
				if slice, ok := in["slice"].(map[string]any); ok {
					item["slice_vram_bytes"] = slice["vram_bytes"]
					item["slice_compute_pct"] = slice["compute_pct"]
					delete(item, "slice")
				}
				out = append(out, item)
			}
			obj["items"] = out
		}
	}
	return ""
}

func (f *fakeAPI) seedInventory() {
	cluster := "11111111-1111-4111-8111-111111111111"
	f.store["nodes"] = map[string]map[string]any{}
	for i, name := range []string{"mac-1", "gpu-a", "gpu-b"} {
		id := fmt.Sprintf("00000000-0000-4000-8000-00000000000%d", i+1)
		f.store["nodes"][id] = map[string]any{"id": id, "cluster_id": cluster, "name": name,
			"status": "online", "os": "linux", "proxy_port": 9443, "labels": map[string]any{"zone": "lab"},
			"device_count": 1, "instance_count": 0, "cpu_cores": 16, "memory_total_bytes": 68719476736}
	}
	f.store["nodes"]["00000000-0000-4000-8000-000000000001"]["os"] = "macos"
	f.store["devices"] = map[string]map[string]any{
		"d0000000-0000-4000-8000-000000000001": {"id": "d0000000-0000-4000-8000-000000000001",
			"node_id": "00000000-0000-4000-8000-000000000001", "vendor": "apple", "index": 0,
			"name": "Apple M3 Max", "vram_total_bytes": 68719476736, "is_unified_memory": true,
			"health": "healthy"},
		"d0000000-0000-4000-8000-000000000002": {"id": "d0000000-0000-4000-8000-000000000002",
			"node_id": "00000000-0000-4000-8000-000000000002", "vendor": "nvidia", "index": 0,
			"name": "NVIDIA L40S", "vram_total_bytes": 48318382080, "is_unified_memory": false,
			"health": "healthy", "compute_capability": "8.9"},
	}
	f.store["engines"] = map[string]map[string]any{
		"e0000000-0000-4000-8000-000000000001": {"id": "e0000000-0000-4000-8000-000000000001",
			"name": "llama_cpp", "display_name": "llama.cpp", "runtime": "native", "is_builtin": true,
			"is_enabled": true, "supported_model_types": []any{"chat", "embedding"},
			"supported_vendors": []any{"apple", "nvidia"}},
		"e0000000-0000-4000-8000-000000000002": {"id": "e0000000-0000-4000-8000-000000000002",
			"name": "mlx", "display_name": "MLX", "runtime": "native", "is_builtin": true,
			"is_enabled": true, "supported_model_types": []any{"chat"},
			"supported_vendors": []any{"apple"}},
		"e0000000-0000-4000-8000-000000000003": {"id": "e0000000-0000-4000-8000-000000000003",
			"name": "vllm", "display_name": "vLLM", "runtime": "container", "is_builtin": true,
			"is_enabled": false, "supported_model_types": []any{"chat", "vision"},
			"supported_vendors": []any{"nvidia", "amd"}},
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeEnv(w http.ResponseWriter, status int, data any) {
	writeJSON(w, status, map[string]any{"success": true, "data": data, "error": nil, "meta": nil})
}

func writeErr(w http.ResponseWriter, status int, code, msg string) {
	writeJSON(w, status, map[string]any{"success": false, "data": nil,
		"error": map[string]any{"code": code, "message": msg}, "meta": nil})
}

func setDefault(obj map[string]any, key string, v any) {
	if cur, ok := obj[key]; !ok || cur == nil {
		obj[key] = v
	}
}
