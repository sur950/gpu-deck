/**
 * GENERATED FILE — do not edit by hand.
 * Source: libs/contracts/openapi/admin.yaml
 * Regenerate with `pnpm gen:api` (apps/web).
 */
export type paths = {
    "/agent/downloads": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Available node-agent builds
         * @description Builds in AGENT_DIST_DIR (nested <version>/<target>/ or flat release files), builds cached from the GitHub release (AGENT_CACHE_DIR), and the builds of this control plane's release that are downloaded on first use (source=release).
         */
        get: operations["listAgentDownloads"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/agent/downloads/{version}/{target}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Download a node-agent build
         * @description Unauthenticated with a usable join token (unused, unrevoked, unexpired), else an admin session. The agent hub also serves it at https://<hub>:8443/agent/v1/downloads/{version}/{target} (join token only).
         */
        get: operations["downloadAgent"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/agent/downloads/{version}/{target}/sha256": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** sha256 of a node-agent build */
        get: operations["downloadAgentSha256"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/agent/install.sh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Install script for a join token
         * @description Join token required (header or ?token=). GPU machines fetch the same script from the agent hub (<hub>/agent/v1/install.sh, see JoinTokenCreated.install_command), so they never need the admin API.
         */
        get: operations["agentInstallScript"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alert-channels": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List channels */
        get: operations["listAlertChannels"];
        put?: never;
        /** Create a channel */
        post: operations["createAlertChannel"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alert-channels/{channel_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a channel */
        get: operations["getAlertChannel"];
        put?: never;
        post?: never;
        /** Delete a channel */
        delete: operations["deleteAlertChannel"];
        options?: never;
        head?: never;
        /** Update a channel */
        patch: operations["updateAlertChannel"];
        trace?: never;
    };
    "/alert-channels/{channel_id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Send a test message now */
        post: operations["testAlertChannel"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alert-events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List alert events */
        get: operations["listAlertEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alert-events/{event_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get an event with deliveries */
        get: operations["getAlertEvent"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alert-events/{event_id}/acknowledge": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Acknowledge */
        post: operations["acknowledgeAlertEvent"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alert-events/{event_id}/resend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Re-deliver to all (or one) channel */
        post: operations["resendAlertEvent"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/alert-events/{event_id}/resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve */
        post: operations["resolveAlertEvent"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api-keys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List API keys */
        get: operations["listApiKeys"];
        put?: never;
        /** Create an API key (plaintext returned once) */
        post: operations["createApiKey"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api-keys/{key_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get an API key */
        get: operations["getApiKey"];
        put?: never;
        post?: never;
        /** Soft-delete (also revokes) */
        delete: operations["deleteApiKey"];
        options?: never;
        head?: never;
        /** Update name/description/expiry/metadata */
        patch: operations["updateApiKey"];
        trace?: never;
    };
    "/api-keys/{key_id}/activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Re-activate a suspended key */
        post: operations["activateApiKey"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api-keys/{key_id}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revoke immediately (routing cache invalidated within 1 s) */
        post: operations["revokeApiKey"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api-keys/{key_id}/rotate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rotate: new secret, same id (old secret invalid at once) */
        post: operations["rotateApiKey"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api-keys/{key_id}/suspend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Suspend (reversible) */
        post: operations["suspendApiKey"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api-keys/stats": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Counts by status */
        get: operations["getApiKeyStats"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/audit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search the audit log */
        get: operations["listAuditLogs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/audit/{log_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get one entry */
        get: operations["getAuditLog"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/audit/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Export matching entries as CSV */
        get: operations["exportAuditCsv"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/change-password": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Change the admin password
         * @description Verifies current_password, re-hashes with Argon2id, revokes all other sessions, audits auth.password_changed. The .env seed is not touched; a later change of ADMIN_PASSWORD in .env overrides this on restart.
         */
        post: operations["changePassword"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/csrf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get a CSRF token for the current session
         * @description Also refreshes the gpudeck_csrf cookie. Token = HMAC(SESSION_SECRET, session token hash) valid for the session lifetime.
         */
        get: operations["getCsrfToken"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Log in
         * @description Rate limited 10/minute per IP. Lockout after AUTH_LOCKOUT_ATTEMPTS failures for AUTH_LOCKOUT_MINUTES (401 ACCOUNT_LOCKED). Sets the session and CSRF cookies.
         */
        post: operations["login"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Log out (revokes current session, clears cookies) */
        post: operations["logout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/logout-all": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revoke every session except the current one */
        post: operations["logoutAll"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Current admin */
        get: operations["getMe"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List active sessions */
        get: operations["listSessions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/sessions/{session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Revoke a session */
        delete: operations["revokeSession"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/batches": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Anthropic message batches (admin view) */
        get: operations["listBatches"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/batches/{batch_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a batch */
        get: operations["getBatch"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/batches/{batch_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel a batch */
        post: operations["cancelBatch"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/benchmark-runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a run with results */
        get: operations["getBenchmarkRun"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/benchmark-runs/{run_id}/apply": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Apply the run's recommendation to the deployment */
        post: operations["applyBenchmarkRecommendation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/benchmark-runs/{run_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel a running benchmark */
        post: operations["cancelBenchmarkRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/benchmark-runs/{run_id}/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Results as CSV */
        get: operations["exportBenchmarkRun"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/benchmarks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List benchmarks */
        get: operations["listBenchmarks"];
        put?: never;
        /** Create a benchmark definition */
        post: operations["createBenchmark"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/benchmarks/{benchmark_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a benchmark */
        get: operations["getBenchmark"];
        put?: never;
        post?: never;
        /** Delete a benchmark and its runs */
        delete: operations["deleteBenchmark"];
        options?: never;
        head?: never;
        /** Update a benchmark */
        patch: operations["updateBenchmark"];
        trace?: never;
    };
    "/benchmarks/{benchmark_id}/auto-tune": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Start an auto-tune run (concurrency + launch-flag sweep against the SLO) */
        post: operations["autoTuneBenchmark"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/benchmarks/{benchmark_id}/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Runs of a benchmark */
        get: operations["listBenchmarkRuns"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/benchmarks/{benchmark_id}/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start a run (load test through the API layer from the jobs process, Celery queue benchmark)
         * @description 409 INSTANCE_BUSY if the chosen instance has live traffic and auto-tune is requested.
         */
        post: operations["startBenchmark"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/billing/estimate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Live cost for the current (or given) period */
        get: operations["getBillingEstimate"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/billing/reports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List reports */
        get: operations["listBillingReports"];
        put?: never;
        /** Generate a report (async) */
        post: operations["createBillingReport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/billing/reports/{report_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a report with line items */
        get: operations["getBillingReport"];
        put?: never;
        post?: never;
        /** Delete a report */
        delete: operations["deleteBillingReport"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/billing/reports/{report_id}/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** CSV export */
        get: operations["exportBillingReportCsv"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/billing/reports/{report_id}/regenerate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Regenerate with current prices */
        post: operations["regenerateBillingReport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/branding": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Branding for the login page and UI shell
         * @description Cache-Control: public, max-age=300.
         */
        get: operations["getBranding"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/catalog/local/scan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Scan a local path for model files (plain, Ollama blobs, LM Studio, HF cache, MLX dirs) */
        post: operations["scanLocalPath"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/catalog/remote-files": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List files of a remote repo with sizes, hashes, format and suggested settings */
        get: operations["listRemoteFiles"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/catalog/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search Hugging Face / ModelScope */
        get: operations["searchCatalog"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/nodes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List provisioned cloud nodes */
        get: operations["listCloudNodes"];
        put?: never;
        /**
         * Provision cloud GPU VMs
         * @description Creates `count` rows (status pending) for a cloud cluster (type cloud_aws|cloud_azure|cloud_gcp|cloud_do, credential_id set) and returns them; the cloud worker/job then creates each VM with a cloud-init that downloads the agent from the hub (GET /agent/v1/downloads/{version}/{target}) and joins with a one-time join token minted for that VM. Watch GET /cloud/nodes/{id}: pending -> provisioning -> booting -> running (node_id set). The instance type is validated against the provider catalog first. Per-cluster cap: clusters.config.cloud.max_nodes (default CLOUD_MAX_NODES_PER_CLUSTER) -> 409 CLOUD_MAX_NODES. dry_run=true returns 200 with a CloudProvisionPreview (price, monthly estimate, resolved zone/options) and creates nothing. Provider failures use error codes CLOUD_QUOTA_EXCEEDED (409), CLOUD_CAPACITY_UNAVAILABLE (409), CLOUD_AUTH_FAILED (424), CLOUD_CREDENTIAL_INVALID (422), CLOUD_INVALID_REQUEST (422), CLOUD_NOT_FOUND (404), CLOUD_RATE_LIMITED (429), CLOUD_PROVIDER_ERROR (502).
         */
        post: operations["createCloudNodes"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/nodes/{cloud_node_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a cloud node */
        get: operations["getCloudNode"];
        put?: never;
        post?: never;
        /**
         * Forget a terminated cloud node record
         * @description Only terminated rows (or error rows that never created a VM); 409 otherwise.
         */
        delete: operations["deleteCloudNode"];
        options?: never;
        head?: never;
        /** Update idle auto-terminate / labels */
        patch: operations["updateCloudNode"];
        trace?: never;
    };
    "/cloud/nodes/{cloud_node_id}/apply-price-plan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Attach a price plan from the provider list price
         * @description Creates (or reuses) the price plan named price_plan_suggestion.name with usd_per_gpu_minute = list price / 60 / gpu_count and sets it on the node (nodes.price_plan_id). 409 when the provider has no price or the VM has not joined yet.
         */
        post: operations["applyCloudNodePricePlan"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/nodes/{cloud_node_id}/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Run one lifecycle step now
         * @description Polls the provider and advances the state machine immediately (the cloud.reconcile job does this every CLOUD_RECONCILE_SECONDS). Rate limited 60/minute.
         */
        post: operations["refreshCloudNode"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/nodes/{cloud_node_id}/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start a stopped VM
         * @description Status starting -> running once the agent reconnects (a node drained by stop is undrained). Provider failures use error codes CLOUD_QUOTA_EXCEEDED (409), CLOUD_CAPACITY_UNAVAILABLE (409), CLOUD_AUTH_FAILED (424), CLOUD_CREDENTIAL_INVALID (422), CLOUD_INVALID_REQUEST (422), CLOUD_NOT_FOUND (404), CLOUD_RATE_LIMITED (429), CLOUD_PROVIDER_ERROR (502).
         */
        post: operations["startCloudNode"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/nodes/{cloud_node_id}/stop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Drain the node, then stop the VM
         * @description Allowed in running|booting|error. Unless force=true an online node is drained first (POST /nodes/{id}/drain semantics, move_instances=true); the VM is stopped once the node is drained/offline or drain_timeout_seconds (default CLOUD_DRAIN_TIMEOUT_SECONDS) passed. Status: draining -> stopping -> stopped. Provider failures use error codes CLOUD_QUOTA_EXCEEDED (409), CLOUD_CAPACITY_UNAVAILABLE (409), CLOUD_AUTH_FAILED (424), CLOUD_CREDENTIAL_INVALID (422), CLOUD_INVALID_REQUEST (422), CLOUD_NOT_FOUND (404), CLOUD_RATE_LIMITED (429), CLOUD_PROVIDER_ERROR (502).
         */
        post: operations["stopCloudNode"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/nodes/{cloud_node_id}/terminate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Drain the node, terminate the VM, remove the node
         * @description Unless force=true the node is drained first; then the VM is terminated (everything tagged gpu-deck:* that the VM owns is deleted with it), the node is removed (certs revoked) and the unused join token revoked. Status: draining -> terminating -> terminated. Provider failures use error codes CLOUD_QUOTA_EXCEEDED (409), CLOUD_CAPACITY_UNAVAILABLE (409), CLOUD_AUTH_FAILED (424), CLOUD_CREDENTIAL_INVALID (422), CLOUD_INVALID_REQUEST (422), CLOUD_NOT_FOUND (404), CLOUD_RATE_LIMITED (429), CLOUD_PROVIDER_ERROR (502).
         */
        post: operations["terminateCloudNode"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/providers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List supported cloud providers
         * @description AWS, Azure, GCP and DigitalOcean with their cluster type, vault credential format, pricing source and launch options. verified_live=false: providers were verified against recorded/mocked APIs only.
         */
        get: operations["listCloudProviders"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/providers/{provider}/check-credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Verify a vault credential against the provider API
         * @description Runs one cheap authenticated call (STS GetCallerIdentity, compute usages, regions list, /v2/account). Rate limited 20/minute. Provider failures use error codes CLOUD_QUOTA_EXCEEDED (409), CLOUD_CAPACITY_UNAVAILABLE (409), CLOUD_AUTH_FAILED (424), CLOUD_CREDENTIAL_INVALID (422), CLOUD_INVALID_REQUEST (422), CLOUD_NOT_FOUND (404), CLOUD_RATE_LIMITED (429), CLOUD_PROVIDER_ERROR (502).
         */
        post: operations["checkCloudCredential"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/providers/{provider}/instance-types": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * GPU instance types of a region
         * @description GPU model / vendor / count / per-GPU VRAM, vCPUs, memory and the on-demand list price per hour where the provider API exposes it (AWS Pricing API, Azure Retail Prices, DigitalOcean sizes; GCP: null). GCP N1 attachable GPUs appear as n1-standard-N+<accelerator>xK. Cached; meta.total = item count. Provider failures use error codes CLOUD_QUOTA_EXCEEDED (409), CLOUD_CAPACITY_UNAVAILABLE (409), CLOUD_AUTH_FAILED (424), CLOUD_CREDENTIAL_INVALID (422), CLOUD_INVALID_REQUEST (422), CLOUD_NOT_FOUND (404), CLOUD_RATE_LIMITED (429), CLOUD_PROVIDER_ERROR (502).
         */
        get: operations["listCloudInstanceTypes"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/providers/{provider}/quotas": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * GPU / vCPU quotas of a region
         * @description AWS Service Quotas (On-Demand accelerator vCPU quotas; used is null), Azure compute usages (GPU families + total vCPUs), GCP region quotas (*_GPUS, CPUS, INSTANCES), DigitalOcean droplet limit. Provider failures use error codes CLOUD_QUOTA_EXCEEDED (409), CLOUD_CAPACITY_UNAVAILABLE (409), CLOUD_AUTH_FAILED (424), CLOUD_CREDENTIAL_INVALID (422), CLOUD_INVALID_REQUEST (422), CLOUD_NOT_FOUND (404), CLOUD_RATE_LIMITED (429), CLOUD_PROVIDER_ERROR (502).
         */
        get: operations["listCloudQuotas"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cloud/providers/{provider}/regions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Regions offering GPU VMs
         * @description Cached for CLOUD_CATALOG_CACHE_SECONDS. meta.total = item count. Provider failures use error codes CLOUD_QUOTA_EXCEEDED (409), CLOUD_CAPACITY_UNAVAILABLE (409), CLOUD_AUTH_FAILED (424), CLOUD_CREDENTIAL_INVALID (422), CLOUD_INVALID_REQUEST (422), CLOUD_NOT_FOUND (404), CLOUD_RATE_LIMITED (429), CLOUD_PROVIDER_ERROR (502).
         */
        get: operations["listCloudRegions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/clusters": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List clusters */
        get: operations["listClusters"];
        put?: never;
        /** Create a cluster */
        post: operations["createCluster"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/clusters/{cluster_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a cluster with counts */
        get: operations["getCluster"];
        put?: never;
        post?: never;
        /**
         * Delete an empty cluster
         * @description 409 CLUSTER_NOT_EMPTY while nodes exist.
         */
        delete: operations["deleteCluster"];
        options?: never;
        head?: never;
        /** Update a cluster */
        patch: operations["updateCluster"];
        trace?: never;
    };
    "/clusters/{cluster_id}/nodes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Nodes in the cluster */
        get: operations["listClusterNodes"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List deployments */
        get: operations["listDeployments"];
        put?: never;
        /**
         * Create (and by default start) a deployment
         * @description Runs compat check + placement; 422 NO_COMPATIBLE_ENGINE / 409 NO_CAPACITY with the PlacementPreview in error.details. With dry_run=true returns 200 and data = PlacementPreview instead.
         */
        post: operations["createDeployment"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments/{deployment_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a deployment with instances */
        get: operations["getDeployment"];
        put?: never;
        post?: never;
        /** Unload all instances and soft-delete */
        delete: operations["deleteDeployment"];
        options?: never;
        head?: never;
        /** Update (live for weights/replicas; rolling swap for engine/model/launch changes) */
        patch: operations["updateDeployment"];
        trace?: never;
    };
    "/deployments/{deployment_id}/instances": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Instances of the deployment */
        get: operations["listDeploymentInstances"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments/{deployment_id}/restart": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rolling restart of every instance */
        post: operations["restartDeployment"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments/{deployment_id}/scale": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Set replicas */
        post: operations["scaleDeployment"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments/{deployment_id}/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Schedule and load instances (desired_running=true) */
        post: operations["startDeployment"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments/{deployment_id}/stop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Unload all instances, keep the deployment (desired_running=false) */
        post: operations["stopDeployment"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments/{deployment_id}/swaps": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Swap history of the deployment */
        get: operations["listDeploymentSwaps"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments/{deployment_id}/traffic": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set per-instance traffic weights and strategy */
        put: operations["setTrafficWeights"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deployments/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Placement preview (scheduler dry run) */
        post: operations["previewPlacement"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/device-slices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** All active slices */
        get: operations["listAllDeviceSlices"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List devices */
        get: operations["listDevices"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{device_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a device with live sample and slices */
        get: operations["getDevice"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{device_id}/slices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Slices on the device */
        get: operations["listDeviceSlices"];
        put?: never;
        /**
         * Create a slice on the device
         * @description Standalone (no deployment_id): a VRAM/compute reservation the scheduler honours; an SSH GPU instance can take it with slice_id. With deployment_id (a deployment that uses GPU slices): one more replica of that deployment is placed on this device with the deployment's slice size (replicas + 1). 409 NO_CAPACITY when the unreserved VRAM / compute does not fit.
         */
        post: operations["createDeviceSlice"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/devices/{device_id}/slices/{slice_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Delete (release) a slice
         * @description Releases a standalone slice. A slice serving a running replica or an SSH GPU instance needs force=true: the replica is removed (replicas - 1) / the instance is stopped, and the slice is released when it has unloaded.
         */
        delete: operations["deleteDeviceSlice"];
        options?: never;
        head?: never;
        /**
         * Resize or rename a slice
         * @description Standalone slice: capacity-checked resize (200). Slice of a deployment replica: changes the deployment's slice_vram_bytes / slice_compute_pct, every replica is replaced (202, rolling_update=true). Slice held by an SSH GPU instance: 409 until it is stopped.
         */
        patch: operations["updateDeviceSlice"];
        trace?: never;
    };
    "/engines": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List engines with their versions */
        get: operations["listEngines"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/engines/{engine_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get an engine */
        get: operations["getEngine"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Enable/disable, edit capabilities (custom engine) */
        patch: operations["updateEngine"];
        trace?: never;
    };
    "/engines/{engine_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Versions of an engine */
        get: operations["listEngineVersions"];
        put?: never;
        /** Add a version (image or native artifacts + launch/health/metrics config) */
        post: operations["createEngineVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/engines/{engine_id}/versions/{version_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a version */
        get: operations["getEngineVersion"];
        put?: never;
        post?: never;
        /**
         * Delete a version
         * @description 409 ENGINE_VERSION_IN_USE while deployments reference it.
         */
        delete: operations["deleteEngineVersion"];
        options?: never;
        head?: never;
        /** Update a version */
        patch: operations["updateEngineVersion"];
        trace?: never;
    };
    "/engines/auto-pick": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Pick the best engine version for a model version (and node/cluster) */
        post: operations["autoPickEngine"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/engines/compat-check": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check model version × engine version(s) compatibility */
        post: operations["runCompatCheck"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/engines/compat-checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Past checks */
        get: operations["listCompatChecks"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/gpu-instances": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List SSH GPU instances */
        get: operations["listGpuInstances"];
        put?: never;
        /**
         * Request an SSH GPU instance
         * @description Placed by the scheduler (whole GPUs need a completely free device; a slice needs its VRAM + compute share; slice_id takes an existing standalone slice), then start_ssh_instance is sent; state goes requested -> placing -> starting -> running. 409 NO_CAPACITY when nothing fits unless wait_for_capacity_seconds queues the request. Nodes need a container runtime (hello.capabilities.ssh_instances); Apple devices only with device_vendor=apple (no GPU passthrough: gpu_attached=false).
         */
        post: operations["createGpuInstance"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/gpu-instances/{instance_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get an SSH GPU instance */
        get: operations["getGpuInstance"];
        put?: never;
        post?: never;
        /** Delete an SSH GPU instance */
        delete: operations["deleteGpuInstance"];
        options?: never;
        head?: never;
        /**
         * Extend TTL, idle timeout, labels, keys
         * @description ttl_seconds restarts the TTL from now (0 removes it). authorized_keys only while stopped (applied on the next start).
         */
        patch: operations["updateGpuInstance"];
        trace?: never;
    };
    "/gpu-instances/{instance_id}/connection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Connection details
         * @description host:port of the agent's SSH forwarder on the node's private address (never proxied by the control plane), user, host key fingerprints, ssh command and known_hosts lines.
         */
        get: operations["getGpuInstanceConnection"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/gpu-instances/{instance_id}/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start a stopped instance again
         * @description New placement; 409 NO_CAPACITY when nothing fits.
         */
        post: operations["startGpuInstance"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/gpu-instances/{instance_id}/stop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Stop an SSH GPU instance */
        post: operations["stopGpuInstance"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Health of the admin API and its dependencies
         * @description 200 healthy/degraded, 503 unhealthy. Bounded probes (DB 2×1.5 s).
         */
        get: operations["getHealth"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/live": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Liveness (process up) */
        get: operations["getLiveness"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Readiness (migrations applied, seed done, hub listening) */
        get: operations["getReadiness"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/instances": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List instances */
        get: operations["listInstances"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/instances/{instance_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get an instance */
        get: operations["getInstance"];
        put?: never;
        post?: never;
        /** Unload this instance (deployment replicas are reduced by one) */
        delete: operations["stopInstance"];
        options?: never;
        head?: never;
        /** Set traffic weight */
        patch: operations["updateInstance"];
        trace?: never;
    };
    "/instances/{instance_id}/drain": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Drain (stop new traffic, wait for sessions) */
        post: operations["drainInstance"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/instances/{instance_id}/restart": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Drain → unload → load on the same devices */
        post: operations["restartInstance"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/instances/{instance_id}/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Live per-session / per-slot snapshot */
        get: operations["getInstanceSessions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ip-rules": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List rules */
        get: operations["listIpRules"];
        put?: never;
        /**
         * Create a rule
         * @description Refuses (409 SELF_LOCKOUT) an admin-scope rule that would block the caller's own IP.
         */
        post: operations["createIpRule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ip-rules/{rule_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a rule */
        get: operations["getIpRule"];
        put?: never;
        post?: never;
        /** Delete a rule */
        delete: operations["deleteIpRule"];
        options?: never;
        head?: never;
        /** Update a rule */
        patch: operations["updateIpRule"];
        trace?: never;
    };
    "/ip-rules/check": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluate an IP against the rules of a scope */
        post: operations["checkIp"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/layouts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List layouts */
        get: operations["listLayouts"];
        put?: never;
        /** Create a layout */
        post: operations["createLayout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/layouts/{layout_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a layout */
        get: operations["getLayout"];
        put?: never;
        post?: never;
        /**
         * Delete a layout
         * @description 409 LAYOUT_IN_USE while a time_schedule rule references it.
         */
        delete: operations["deleteLayout"];
        options?: never;
        head?: never;
        /** Update (items are replaced when present) */
        patch: operations["updateLayout"];
        trace?: never;
    };
    "/layouts/{layout_id}/apply": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Apply the layout (swap operations) */
        post: operations["applyLayout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/layouts/{layout_id}/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Diff against the current placement (nothing executed) */
        post: operations["previewLayout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/layouts/capture": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Snapshot the current placement as a new layout */
        post: operations["captureLayout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/live/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * SSE stream of live metrics and state changes
         * @description metrics frames carry {gpu: [latest GpuSample per device], engine: [latest EngineSample per instance], overview: OverviewLive}.
         */
        get: operations["streamLiveEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/live/ws": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * WebSocket equivalent of /live/events
         * @description Authenticated by the session cookie (same-origin) or `Authorization: Bearer <session token>`; CSRF not required (read-only).
         */
        get: operations["connectLiveWebSocket"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Query log chunks (newest first, cursor paginated) */
        get: operations["queryLogs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/logs/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Download matching logs as plain text */
        get: operations["exportLogs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/logs/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Live tail (SSE) */
        get: operations["streamLogs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/catalog": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Metric catalog (name, unit, type, scope) as served */
        get: operations["listMetricNames"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/gpu": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** GPU metrics (utilization, VRAM, power, temperature, clocks, errors) */
        get: operations["queryGpuMetrics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/inference": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Engine metrics (sessions, throughput, TTFT, latency, KV cache, cached vs computed tokens, errors) */
        get: operations["queryInferenceMetrics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/overview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Dashboard overview: counts, live totals, cost, recent activity */
        get: operations["getOverview"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/prometheus": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Prometheus exposition of the latest samples for every device/instance (scrape target)
         * @description Protected by PROMETHEUS_SCRAPE_TOKEN (Authorization: Bearer) or an admin session; also served on /metrics of each process for process-level metrics.
         */
        get: operations["getPrometheusExposition"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Per-session/slot series (KV cache per session, tokens, state) for one instance */
        get: operations["querySessionMetrics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/sessions/live": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Current sessions across instances */
        get: operations["getLiveSessions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Requests / tokens / cost over time from usage rollups */
        get: operations["queryUsageMetrics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/model-cache": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Cache items across nodes */
        get: operations["listCacheItems"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/model-cache/{item_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Evict one item */
        delete: operations["deleteCacheItem"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/model-cache/{item_id}/pin": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Pin (never auto-evicted) */
        post: operations["pinCacheItem"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/model-cache/{item_id}/unpin": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Unpin */
        post: operations["unpinCacheItem"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/model-cache/evict": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evict / unpin cached files */
        post: operations["evictCache"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/model-cache/prefetch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Download a model version to nodes */
        post: operations["prefetchModel"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/model-cache/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Per-node cache summary */
        get: operations["getCacheSummary"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List registered models */
        get: operations["listModels"];
        put?: never;
        /**
         * Register a model (optionally with its first version)
         * @description For huggingface/modelscope the files are listed from the remote; for local_path the path is scanned. Version status becomes blocked if only pickle weights exist and allow_pickle=false.
         */
        post: operations["registerModel"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/models/{model_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a model */
        get: operations["getModel"];
        put?: never;
        post?: never;
        /**
         * Soft-delete a model
         * @description 409 MODEL_IN_USE while deployments exist.
         */
        delete: operations["deleteModel"];
        options?: never;
        head?: never;
        /** Update a model (priority, parser defaults, flags, aliases, ...) */
        patch: operations["updateModel"];
        trace?: never;
    };
    "/models/{model_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Versions of a model */
        get: operations["listModelVersions"];
        put?: never;
        /** Add a version */
        post: operations["addModelVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/models/{model_id}/versions/{version_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a version */
        get: operations["getModelVersion"];
        put?: never;
        post?: never;
        /** Delete a version */
        delete: operations["deleteModelVersion"];
        options?: never;
        head?: never;
        /** Set default / rename / quantization label */
        patch: operations["updateModelVersion"];
        trace?: never;
    };
    "/models/{model_id}/versions/{version_id}/cache": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Where this version is cached */
        get: operations["listModelVersionCache"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/models/{model_id}/versions/{version_id}/files": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Files of a version */
        get: operations["listModelFiles"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/models/{model_id}/versions/{version_id}/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Re-verify hashes against the source / cached copies and refresh status */
        post: operations["verifyModelVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List nodes */
        get: operations["listNodes"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a node */
        get: operations["getNode"];
        put?: never;
        post?: never;
        /**
         * Remove a node
         * @description Requires status drained unless force. Revokes all its certificates, closes the hub socket, soft-deletes the row, audits node.removed.
         */
        delete: operations["removeNode"];
        options?: never;
        head?: never;
        /** Update name, labels, price plan, proxy address */
        patch: operations["updateNode"];
        trace?: never;
    };
    "/nodes/{node_id}/cache": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Model cache summary for the node */
        get: operations["getNodeCache"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/certificates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Certificates issued to the node */
        get: operations["listNodeCertificates"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/certificates/{cert_id}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revoke a certificate (cuts the node off if it is the active one) */
        post: operations["revokeNodeCertificate"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/certificates/rotate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Ask the agent for a CSR and issue a new certificate */
        post: operations["rotateNodeCertificate"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/commands": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Agent commands sent to the node */
        get: operations["listNodeCommands"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/commands/{command_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** One agent command (state, progress, result) */
        get: operations["getNodeCommand"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/devices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Devices on the node */
        get: operations["listNodeDevices"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/drain": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Drain: stop scheduling, move/unload instances */
        post: operations["drainNode"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/hash": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * sha256 of files on the node
         * @description Agent command hash_files (streams progress_pct while running). result = HashFilesResult {files[{path, sha256, size_bytes, error}], hashes{path: sha256}, bytes_hashed}.
         */
        post: operations["hashNodeFiles"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/instances": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Instances on the node */
        get: operations["listNodeInstances"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/scan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Scan a path on the node for model files
         * @description Agent command scan_path (agent >= 1.1.0): Ollama stores, LM Studio, Hugging Face caches, MLX dirs and plain GGUF/safetensors; `~` expands on the node. Waits up to wait_seconds for the result. Same as POST /catalog/local/scan with node_id, but returns the raw agent result.
         */
        post: operations["scanNodePath"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/{node_id}/undrain": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel drain / make schedulable again */
        post: operations["undrainNode"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/join-tokens": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List join tokens */
        get: operations["listJoinTokens"];
        put?: never;
        /** Create a join (enrolment) token, one-time or multi-use (shown once) */
        post: operations["createJoinToken"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/nodes/join-tokens/{token_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Revoke an unused token */
        delete: operations["revokeJoinToken"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/pki/ca": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** CA certificate (public) */
        get: operations["getCaCertificate"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/pki/certificates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** All issued certificates */
        get: operations["listCertificates"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/pki/certificates/{cert_id}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revoke any certificate */
        post: operations["revokeCertificate"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/pki/crl": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Current revocation list */
        get: operations["getCrl"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/pki/service-certificates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Issue a service certificate (api_layer / control_plane / agent_hub) */
        post: operations["issueServiceCertificate"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/price-plans": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List plans */
        get: operations["listPricePlans"];
        put?: never;
        /** Create a plan */
        post: operations["createPricePlan"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/price-plans/{plan_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a plan */
        get: operations["getPricePlan"];
        put?: never;
        post?: never;
        /** Soft-delete (detaches from clusters/nodes) */
        delete: operations["deletePricePlan"];
        options?: never;
        head?: never;
        /** Update a plan (affects future usage events only) */
        patch: operations["updatePricePlan"];
        trace?: never;
    };
    "/rule-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Run history across rules */
        get: operations["listAllRuleRuns"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/rule-runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a run */
        get: operations["getRuleRun"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/rules": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List rules */
        get: operations["listRules"];
        put?: never;
        /** Create a rule (params validated against the type's JSON Schema) */
        post: operations["createRule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/rules/{rule_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a rule */
        get: operations["getRule"];
        put?: never;
        post?: never;
        /** Soft-delete a rule */
        delete: operations["deleteRule"];
        options?: never;
        head?: never;
        /** Update a rule */
        patch: operations["updateRule"];
        trace?: never;
    };
    "/rules/{rule_id}/disable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Disable */
        post: operations["disableRule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/rules/{rule_id}/dry-run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Evaluate now without acting; returns the run
         * @description Works on disabled rules too. Writes a rule_runs row with dry_run=true.
         */
        post: operations["dryRunRule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/rules/{rule_id}/enable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Enable */
        post: operations["enableRule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/rules/{rule_id}/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluate now and act if the condition holds (ignores cooldown) */
        post: operations["runRuleNow"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/rules/{rule_id}/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Run history of a rule */
        get: operations["listRuleRuns"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/rules/schemas": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * JSON Schemas for the rule builder
         * @description Serves libs/contracts/rules/*.json verbatim.
         */
        get: operations["getRuleSchemas"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/swaps": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List swap operations */
        get: operations["listSwaps"];
        put?: never;
        /** Manual load/unload/move/restart */
        post: operations["createSwap"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/swaps/{swap_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a swap with steps */
        get: operations["getSwap"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/swaps/{swap_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel (rolls back if past unload) */
        post: operations["cancelSwap"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/swaps/{swap_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry a failed swap as a new operation */
        post: operations["retrySwap"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/system/info": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Version, URLs, limits and retention in effect */
        get: operations["getSystemInfo"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/topology": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Full topology with live state */
        get: operations["getTopology"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/traces": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search root spans */
        get: operations["listTraces"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/traces/{trace_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** All spans of a trace */
        get: operations["getTrace"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Raw usage events (drill-down) */
        get: operations["listUsageEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/usage/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** CSV export of events or summary */
        get: operations["exportUsageCsv"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/usage/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Totals grouped by key/model/node/deployment/cluster/format */
        get: operations["getUsageSummary"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/vault": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List credentials (masked) */
        get: operations["listCredentials"];
        put?: never;
        /**
         * Store a secret
         * @description Typed credential (`provider` = kind from GET /vault/metadata, `fields` validated against the kind's JSON Schema; 422 CREDENTIAL_INVALID). Legacy `value` (+ `type`) is still accepted; without a provider the secret is stored as a `generic` credential. System kinds (internal_ca, *_tls) and ca_key / tls_key types are refused.
         */
        post: operations["createCredential"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/vault/{credential_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a credential (masked) */
        get: operations["getCredential"];
        put?: never;
        post?: never;
        /**
         * Soft-delete / revoke
         * @description 409 CREDENTIAL_IN_USE when referenced by a cluster (credential_id or config SSH / kubeconfig reference), an alert channel, a model (metadata.credential_id) or a cloud VM that is not terminated. System credentials 403 CREDENTIAL_SYSTEM.
         */
        delete: operations["deleteCredential"];
        options?: never;
        head?: never;
        /**
         * Update (may include a new value)
         * @description `fields` merge into the stored ones: secret fields left out (or empty) keep their value, public fields present replace (empty clears). `value` replaces the stored secret. The kind cannot change (an untyped legacy credential may be given one). System credentials: 403 CREDENTIAL_SYSTEM.
         */
        patch: operations["updateCredential"];
        trace?: never;
    };
    "/vault/{credential_id}/reveal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reveal plaintext once (audited)
         * @description Returns the stored value and the typed fields (secret ones included), writes credential_usage_history (purpose reveal) and audit credential.revealed. System credentials 403 CREDENTIAL_SYSTEM.
         */
        post: operations["revealCredential"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/vault/{credential_id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Check a credential offline (audited)
         * @description Decrypts in memory and checks VAULT_KEYS, status / expiry and the kind's JSON Schema; never returns secret material. Cloud kinds also return `cloud_check_path` (POST /cloud/providers/{provider}/check-credential) for the live provider call. Writes a usage row (purpose test) and audit credential.tested.
         */
        post: operations["testCredential"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/vault/{credential_id}/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Where a credential is used
         * @description Clusters, alert channels, models and cloud VMs that reference it, the implicit defaults it serves (HF_TOKEN_CREDENTIAL ...) and the last 20 usage history rows.
         */
        get: operations["getCredentialUsage"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/vault/metadata": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Credential types (fields, JSON Schemas), pickers and defaults for forms */
        get: operations["getVaultMetadata"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/vault/stats": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Counts */
        get: operations["getVaultStats"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
};
export type webhooks = Record<string, never>;
export type components = {
    schemas: {
        AdminUser: {
            /** Format: date-time */
            created_at?: string;
            display_name?: string | null;
            /** Format: uuid */
            id: string;
            /** Format: date-time */
            last_login_at?: string | null;
            last_login_ip?: string | null;
            /** Format: date-time */
            password_changed_at?: string;
            /** Format: date-time */
            updated_at?: string;
            username: string;
        };
        /** @enum {string} */
        agent_command_state: "pending" | "sent" | "accepted" | "running" | "succeeded" | "failed" | "rejected" | "timed_out" | "cancelled";
        AgentBuild: {
            /** @enum {string|null} */
            arch?: "x86_64" | "aarch64" | null;
            /** Format: date-time */
            built_at?: string;
            /** @description Relative to /api/v1 */
            download_path?: string;
            filename: string;
            /** @enum {string|null} */
            os?: "linux" | "macos" | null;
            sha256: string;
            size_bytes: number;
            /**
             * @description local: AGENT_DIST_DIR; cache: fetched from the release earlier; release: listed in the release SHA256SUMS.txt, downloaded on first use (size_bytes 0)
             * @enum {string}
             */
            source?: "local" | "cache" | "release";
            /** @description Rust target triple */
            target: string;
            version: string;
        };
        AgentCommand: {
            /** Format: date-time */
            accepted_at?: string | null;
            attempts?: number;
            command_type: string;
            /** Format: date-time */
            completed_at?: string | null;
            /** Format: date-time */
            created_at: string;
            error_code?: string | null;
            error_message?: string | null;
            /** Format: date-time */
            expires_at?: string | null;
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            instance_id?: string | null;
            /** Format: uuid */
            node_id: string;
            progress_pct?: number | null;
            result?: {
                [key: string]: unknown;
            } | null;
            /** Format: date-time */
            sent_at?: string | null;
            state: components["schemas"]["agent_command_state"];
            /** Format: uuid */
            swap_operation_id?: string | null;
        };
        /** @enum {string} */
        alert_channel_type: "slack" | "email" | "webhook";
        /** @enum {string} */
        alert_delivery_status: "pending" | "sent" | "failed" | "suppressed";
        /** @enum {string} */
        alert_event_status: "open" | "acknowledged" | "resolved";
        /** @enum {string} */
        alert_severity: "info" | "warning" | "critical";
        AlertChannel: {
            /** @description slack: {channel, username}; email: {recipients[], from, smtp_host, smtp_port, use_tls}; webhook: {url, method, headers, sign} */
            config: {
                [key: string]: unknown;
            };
            /** Format: date-time */
            created_at?: string;
            /**
             * Format: uuid
             * @description Vault secret: slack webhook URL/bot token, SMTP password, webhook signing secret
             */
            credential_id?: string | null;
            enabled: boolean;
            /** @description event_type globs; empty = all */
            event_filter: string[];
            /** Format: uuid */
            id: string;
            last_error?: string | null;
            /** Format: date-time */
            last_test_at?: string | null;
            last_test_ok?: boolean | null;
            min_severity: components["schemas"]["alert_severity"];
            name: string;
            type: components["schemas"]["alert_channel_type"];
            /** Format: date-time */
            updated_at?: string;
        };
        AlertChannelCreate: {
            config: {
                [key: string]: unknown;
            };
            /** Format: uuid */
            credential_id?: string | null;
            /** @default true */
            enabled?: boolean;
            event_filter?: string[];
            min_severity?: components["schemas"]["alert_severity"];
            name: string;
            /** @description Convenience: create the vault credential inline (webhook URL with token / SMTP password / bot token) */
            secret?: string | null;
            type: components["schemas"]["alert_channel_type"];
        };
        /** @description Partial update; every field optional. */
        AlertChannelUpdate: {
            config?: {
                [key: string]: unknown;
            };
            /** Format: uuid */
            credential_id?: string | null;
            /** @default true */
            enabled?: boolean;
            event_filter?: string[];
            min_severity?: components["schemas"]["alert_severity"];
            name?: string;
            /** @description Convenience: create the vault credential inline (webhook URL with token / SMTP password / bot token) */
            secret?: string | null;
        };
        AlertDelivery: {
            attempts: number;
            /** Format: uuid */
            channel_id: string;
            channel_name?: string;
            error_message?: string | null;
            /** Format: uuid */
            id: string;
            response_code?: number | null;
            /** Format: date-time */
            sent_at?: string | null;
            status: components["schemas"]["alert_delivery_status"];
        };
        AlertEvent: {
            /** Format: date-time */
            acknowledged_at?: string | null;
            /** Format: date-time */
            created_at?: string;
            dedupe_key?: string | null;
            deliveries?: components["schemas"]["AlertDelivery"][];
            event_type: string;
            /** Format: date-time */
            first_seen_at: string;
            /** Format: uuid */
            id: string;
            /** Format: date-time */
            last_seen_at: string;
            message?: string | null;
            occurrences: number;
            payload?: {
                [key: string]: unknown;
            };
            /** Format: date-time */
            resolved_at?: string | null;
            /** Format: uuid */
            resource_id?: string | null;
            resource_name?: string | null;
            resource_type?: string | null;
            severity: components["schemas"]["alert_severity"];
            status: components["schemas"]["alert_event_status"];
            title: string;
        };
        AlertTestResult: {
            error?: string | null;
            latency_ms?: number | null;
            ok: boolean;
            response_code?: number | null;
            /** Format: date-time */
            sent_at: string;
        };
        /** @enum {string} */
        api_key_status: "active" | "expired" | "revoked" | "suspended";
        ApiKey: {
            /** Format: date-time */
            created_at: string;
            description?: string | null;
            /** Format: date-time */
            expires_at?: string | null;
            /** Format: uuid */
            id: string;
            /** @description First 12 chars of the key (gd-xxxxxxxxx) */
            key_prefix: string;
            /** Format: date-time */
            last_rotated_at?: string | null;
            /** Format: date-time */
            last_used_at?: string | null;
            metadata?: {
                [key: string]: unknown;
            };
            name: string;
            /** Format: date-time */
            revoked_at?: string | null;
            status: components["schemas"]["api_key_status"];
            total_requests: number;
            /** Format: date-time */
            updated_at?: string;
        };
        ApiKeyCreate: {
            description?: string | null;
            /** Format: date-time */
            expires_at?: string | null;
            metadata?: {
                [key: string]: unknown;
            };
            name: string;
        };
        ApiKeyCreated: components["schemas"]["ApiKey"] & {
            /** @description Full plaintext key. Shown exactly once; never retrievable again. */
            key: string;
        };
        ApiKeyStats: {
            active?: number;
            expired?: number;
            revoked?: number;
            suspended?: number;
            total?: number;
        };
        /** @description Partial update; every field optional. */
        ApiKeyUpdate: {
            description?: string | null;
            /** Format: date-time */
            expires_at?: string | null;
            metadata?: {
                [key: string]: unknown;
            };
            name?: string;
        };
        ApplyRecommendationRequest: {
            /** @default true */
            apply_launch_flags?: boolean;
            /** @default false */
            apply_replicas?: boolean;
            /**
             * @description Sets traffic_config.max_concurrent_per_instance to best_concurrency
             * @default true
             */
            apply_traffic_config?: boolean;
        };
        /** @enum {string} */
        audit_actor_type: "admin" | "api_key" | "system" | "rule" | "node_agent";
        /** @enum {string} */
        audit_outcome: "success" | "failure";
        AuditLog: {
            /** Format: uuid */
            actor_id?: string | null;
            actor_name?: string | null;
            actor_type: components["schemas"]["audit_actor_type"];
            after_state?: {
                [key: string]: unknown;
            } | null;
            before_state?: {
                [key: string]: unknown;
            } | null;
            /** Format: date-time */
            created_at: string;
            event_type: string;
            /** Format: uuid */
            id: string;
            ip_address?: string | null;
            message?: string | null;
            metadata?: {
                [key: string]: unknown;
            };
            outcome: components["schemas"]["audit_outcome"];
            /** Format: uuid */
            request_id?: string | null;
            /** Format: uuid */
            resource_id?: string | null;
            resource_name?: string | null;
            resource_type?: string | null;
            user_agent?: string | null;
        };
        AutoPickRequest: {
            /** Format: uuid */
            cluster_id?: string | null;
            context_size?: number | null;
            /** @default 1 */
            gpu_count?: number;
            /** Format: uuid */
            model_version_id: string;
            /** Format: uuid */
            node_id?: string | null;
            preset?: components["schemas"]["deployment_preset"] | null;
        };
        AutoPickResult: {
            alternatives: components["schemas"]["CompatCheck"][];
            check?: components["schemas"]["CompatCheck"] | null;
            engine_version?: components["schemas"]["EngineVersion"] | null;
            suggested_launch_flags?: {
                [key: string]: unknown;
            };
            suggested_tool_parser?: string | null;
        };
        Batch: {
            /** Format: uuid */
            api_key_id?: string | null;
            /** Format: date-time */
            cancel_initiated_at?: string | null;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            ended_at?: string | null;
            /** Format: date-time */
            expires_at: string;
            external_id: string;
            format?: components["schemas"]["usage_format"];
            /** Format: uuid */
            id: string;
            request_counts: {
                [key: string]: unknown;
            };
            status: components["schemas"]["batch_status"];
            total_items: number;
        };
        /** @enum {string} */
        batch_status: "in_progress" | "canceling" | "ended";
        Benchmark: components["schemas"]["BenchmarkCreate"] & {
            /** Format: date-time */
            created_at: string;
            deployment_name?: string;
            /** Format: uuid */
            id: string;
            last_run?: components["schemas"]["BenchmarkRun"] | null;
            run_count?: number;
            /** Format: date-time */
            updated_at?: string;
        };
        /** @enum {string} */
        benchmark_run_status: "pending" | "running" | "completed" | "failed" | "cancelled";
        BenchmarkCreate: {
            /** @default false */
            auto_tune: boolean;
            concurrency_levels?: number[];
            /** Format: uuid */
            deployment_id: string;
            description?: string | null;
            /** @default 60 */
            duration_seconds: number;
            name: string;
            /** @description {ttft_ms_p95, e2e_ms_p95, tpot_ms_p95, min_generation_tps, max_error_rate_pct} */
            target_slo?: {
                [key: string]: unknown;
            };
            /** @description {input_tokens, output_tokens, requests_per_level, dataset, prompts[], stream} */
            workload?: {
                [key: string]: unknown;
            };
        };
        BenchmarkRun: {
            /** Format: uuid */
            benchmark_id: string;
            benchmark_name?: string;
            best_concurrency?: number | null;
            /** Format: date-time */
            created_at: string;
            error_message?: string | null;
            /** Format: date-time */
            finished_at?: string | null;
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            instance_id?: string | null;
            /** Format: uuid */
            node_id?: string | null;
            progress_pct: number;
            /** @description {concurrency, launch_flags, replicas, reason} */
            recommendation?: {
                [key: string]: unknown;
            };
            results: {
                concurrency?: number;
                e2e_ms?: {
                    [key: string]: unknown;
                };
                errors?: number;
                generation_tps?: number;
                launch_flags?: {
                    [key: string]: unknown;
                } | null;
                prompt_tps?: number | null;
                requests?: number;
                rps?: number;
                slo_met?: boolean;
                tpot_ms?: {
                    [key: string]: unknown;
                };
                ttft_ms?: {
                    [key: string]: unknown;
                };
            }[];
            slo_met?: boolean | null;
            /** Format: date-time */
            started_at?: string | null;
            status: components["schemas"]["benchmark_run_status"];
        };
        BenchmarkStartRequest: {
            /** @description Override benchmark.auto_tune */
            auto_tune?: boolean | null;
            /** @description Launch-flag variants to sweep when auto-tuning (max 8) */
            flag_variants?: {
                [key: string]: unknown;
            }[];
            /**
             * Format: uuid
             * @description null = pick one ready instance with no traffic
             */
            instance_id?: string | null;
        };
        /** @description Partial update; every field optional. */
        BenchmarkUpdate: {
            /** @default false */
            auto_tune?: boolean;
            concurrency_levels?: number[];
            description?: string | null;
            /** @default 60 */
            duration_seconds?: number;
            name?: string;
            /** @description {ttft_ms_p95, e2e_ms_p95, tpot_ms_p95, min_generation_tps, max_error_rate_pct} */
            target_slo?: {
                [key: string]: unknown;
            };
            /** @description {input_tokens, output_tokens, requests_per_level, dataset, prompts[], stream} */
            workload?: {
                [key: string]: unknown;
            };
        };
        /** @enum {string} */
        billing_report_status: "pending" | "generating" | "ready" | "failed";
        BillingEstimate: {
            /** Format: date-time */
            as_of: string;
            by_api_key?: components["schemas"]["UsageSummaryRow"][];
            by_model?: components["schemas"]["UsageSummaryRow"][];
            by_node?: components["schemas"]["UsageSummaryRow"][];
            /** Format: date-time */
            period_end: string;
            /** Format: date-time */
            period_start: string;
            /** @description Linear projection to period_end */
            projected_cost_usd: number;
            totals: components["schemas"]["UsageTotals"];
        };
        BillingLineItem: {
            cost_breakdown?: {
                cached_tokens_usd?: number;
                gpu_time_usd?: number;
                input_tokens_usd?: number;
                output_tokens_usd?: number;
            };
            group: {
                [key: string]: string;
            };
            labels?: {
                [key: string]: string;
            };
            /** Format: uuid */
            price_plan_id?: string | null;
            price_plan_name?: string | null;
            totals: components["schemas"]["UsageTotals"];
        };
        BillingReport: components["schemas"]["BillingReportCreate"] & {
            /** Format: date-time */
            created_at: string;
            currency?: string;
            error_message?: string | null;
            /** Format: date-time */
            generated_at?: string | null;
            /** Format: uuid */
            id: string;
            line_items?: components["schemas"]["BillingLineItem"][];
            status: components["schemas"]["billing_report_status"];
            totals?: components["schemas"]["UsageTotals"];
            /** Format: date-time */
            updated_at?: string;
        };
        BillingReportCreate: {
            /** @description {api_key_ids[], model_ids[], node_ids[], cluster_ids[]} */
            filters?: {
                [key: string]: unknown;
            };
            /** @description Default [api_key, model] */
            group_by?: ("api_key" | "model" | "node" | "cluster" | "deployment")[];
            name: string;
            /** Format: date-time */
            period_end: string;
            /** Format: date-time */
            period_start: string;
        };
        Branding: {
            app_byline: string;
            app_name: string;
            brand_color: string;
            docs_url?: string | null;
            favicon_url: string;
            logo_url: string;
            support_url?: string | null;
            /** @description Optional overrides (NEXT_PUBLIC_* mirrors are the primary source for the web app) */
            theme?: {
                [key: string]: unknown;
            };
        };
        CaCertificate: {
            certificate_pem: string;
            fingerprint_sha256: string;
            /** Format: date-time */
            not_after: string;
            subject: string;
        };
        /** @enum {string} */
        cache_state: "queued" | "downloading" | "ready" | "evicting" | "evicted" | "error";
        CacheItem: {
            /** Format: date-time */
            created_at?: string;
            error_message?: string | null;
            /** Format: uuid */
            id: string;
            /** @description A running instance uses this file */
            in_use?: boolean;
            /** Format: date-time */
            last_used_at?: string | null;
            local_path: string;
            /** Format: uuid */
            model_file_id: string;
            /** Format: uuid */
            model_id?: string;
            model_name?: string;
            /** Format: uuid */
            model_version_id: string;
            /** Format: uuid */
            node_id: string;
            node_name?: string;
            path?: string;
            pinned: boolean;
            pinned_reason?: string | null;
            progress_pct?: number;
            sha256_verified?: boolean;
            size_bytes: number;
            state: components["schemas"]["cache_state"];
            /** Format: date-time */
            updated_at?: string;
            version?: string;
        };
        CacheSummary: {
            by_model: {
                in_use?: boolean;
                /** Format: date-time */
                last_used_at?: string | null;
                /** Format: uuid */
                model_id?: string;
                model_name?: string;
                /** Format: uuid */
                model_version_id?: string;
                pinned?: boolean;
                size_bytes?: number;
                state?: components["schemas"]["cache_state"];
                version?: string;
            }[];
            cache_dir?: string | null;
            free_disk_bytes?: number | null;
            item_count: number;
            limit_bytes?: number | null;
            /** Format: uuid */
            node_id: string;
            node_name?: string;
            pinned_bytes?: number;
            used_bytes: number;
        };
        CatalogRemoteFile: {
            format: components["schemas"]["model_file_format"];
            is_required?: boolean;
            lfs?: boolean;
            path: string;
            sha256?: string | null;
            size_bytes: number;
        };
        CatalogRemoteFiles: {
            /** @description Parsed config.json / GGUF header when available */
            config?: {
                [key: string]: unknown;
            };
            files: components["schemas"]["CatalogRemoteFile"][];
            has_pickle_only_weights?: boolean;
            repo_id: string;
            revision?: string | null;
            source: components["schemas"]["model_source"];
            suggested?: {
                context_size?: number | null;
                family?: string | null;
                name?: string;
                tool_parser?: string | null;
                type?: components["schemas"]["model_type"] | null;
            };
            total_size_bytes: number;
        };
        CatalogSearchResult: {
            /** Format: uuid */
            already_registered_model_id?: string | null;
            author?: string | null;
            detected_type?: components["schemas"]["model_type"] | null;
            downloads?: number | null;
            formats: components["schemas"]["model_file_format"][];
            gated?: boolean;
            /** Format: date-time */
            last_modified?: string | null;
            library?: string | null;
            likes?: number | null;
            name: string;
            pipeline_tag?: string | null;
            private?: boolean;
            repo_id: string;
            source: components["schemas"]["model_source"];
            tags?: string[];
            total_size_bytes?: number | null;
        };
        /** @enum {string} */
        certificate_kind: "node_agent" | "api_layer" | "agent_hub" | "control_plane";
        ChangePasswordRequest: {
            current_password: string;
            /** @description Min 12 chars; policy from AUTH_PASSWORD_MIN_LENGTH */
            new_password: string;
        };
        /**
         * @description pending (row only) -> provisioning (create accepted) -> booting (VM up, agent not joined) -> running (node joined); draining (before stop/terminate) -> stopping -> stopped -> starting; terminating -> terminated; error keeps last_error.
         * @enum {string}
         */
        cloud_node_status: "pending" | "provisioning" | "booting" | "running" | "draining" | "stopping" | "stopped" | "starting" | "terminating" | "terminated" | "error";
        /** @enum {string} */
        cloud_provider: "aws" | "azure" | "gcp" | "digitalocean";
        CloudCredentialCheck: {
            /** @description Account / subscription / project / email */
            account?: string | null;
            detail?: string | null;
            ok: boolean;
            provider: components["schemas"]["cloud_provider"];
        };
        CloudCredentialCheckRequest: {
            /** Format: uuid */
            credential_id: string;
        };
        CloudInstanceType: {
            description?: string | null;
            /** @description zones (Azure/GCP), architectures (AWS), partial_gpu (Azure NVadsA10), machine_type (GCP) */
            extra: {
                [key: string]: unknown;
            };
            family?: string | null;
            gpu_count: number;
            gpu_model?: string | null;
            gpu_vendor?: string | null;
            /** @description Per GPU */
            gpu_vram_bytes?: number | null;
            id: string;
            memory_bytes?: number | null;
            /** @description On-demand list price (null when the provider API has none) */
            price_per_hour_usd?: number | null;
            price_source?: string | null;
            regions: string[];
            total_vram_bytes?: number | null;
            vcpus?: number | null;
        };
        CloudNode: {
            auto_terminate_idle_minutes?: number | null;
            /** Format: uuid */
            cluster_id: string;
            cluster_name?: string | null;
            /** Format: date-time */
            created_at: string;
            /** Format: uuid */
            created_by?: string | null;
            /** Format: uuid */
            credential_id?: string | null;
            disk_gb: number;
            /** Format: date-time */
            drain_started_at?: string | null;
            gpu_count: number;
            gpu_model?: string | null;
            gpu_vram_bytes?: number | null;
            /** Format: uuid */
            id: string;
            /** Format: date-time */
            idle_since?: string | null;
            image?: string | null;
            instance_type: string;
            /** Format: uuid */
            join_token_id?: string | null;
            /** Format: date-time */
            joined_at?: string | null;
            labels: {
                [key: string]: string;
            };
            /** @description {code, message, provider, provider_code, stage, at} */
            last_error?: {
                [key: string]: unknown;
            } | null;
            /** Format: date-time */
            last_polled_at?: string | null;
            /** Format: date-time */
            launched_at?: string | null;
            memory_bytes?: number | null;
            name: string;
            /**
             * Format: uuid
             * @description Set once the agent joined
             */
            node_id?: string | null;
            node_name?: string | null;
            node_status?: components["schemas"]["node_status"] | null;
            options: {
                [key: string]: unknown;
            };
            /** @enum {string|null} */
            pending_action?: "stop" | "terminate" | null;
            price_per_hour_usd?: number | null;
            price_plan_suggestion?: components["schemas"]["CloudPricePlanSuggestion"] | null;
            price_source?: string | null;
            private_ip?: string | null;
            provider: components["schemas"]["cloud_provider"];
            /** @description EC2 instance id | Azure VM resource id | GCP instance name | Droplet id */
            provider_instance_id?: string | null;
            provider_state?: string | null;
            public_ip?: string | null;
            region: string;
            status: components["schemas"]["cloud_node_status"];
            status_message?: string | null;
            /** Format: date-time */
            stopped_at?: string | null;
            /** @description gpu-deck:* tags applied on the provider (GCP: gpu-deck-* labels) */
            tags: {
                [key: string]: string;
            };
            /** Format: date-time */
            terminated_at?: string | null;
            /** Format: date-time */
            updated_at: string;
            vcpus?: number | null;
            zone?: string | null;
        };
        CloudNodeAction: {
            drain_timeout_seconds?: number | null;
            /**
             * @description Skip the node drain
             * @default false
             */
            force?: boolean;
            reason?: string | null;
        };
        CloudNodeCreate: {
            /**
             * @description Attach a list-price plan to the node when it joins
             * @default false
             */
            apply_price_plan?: boolean;
            /** @description Terminate after drain when the node ran no instance this long; default config.cloud.auto_terminate_idle_minutes */
            auto_terminate_idle_minutes?: number | null;
            /**
             * Format: uuid
             * @description A cloud cluster with credential_id set
             */
            cluster_id: string;
            /** @default 1 */
            count?: number;
            /** @description Default config.cloud.disk_gb or CLOUD_DEFAULT_DISK_GB */
            disk_gb?: number | null;
            /** @default false */
            dry_run?: boolean;
            /** @description AMI id | Azure publisher:offer:sku:version or image id | GCP image URL | DO image slug; default: the provider's NVIDIA GPU image */
            image?: string | null;
            instance_type: string;
            /** @description Node labels (applied through the join token) */
            labels?: {
                [key: string]: string;
            };
            /** @description VM / node name (lowercased, '-' separated); generated when absent; count > 1 appends -N */
            name?: string | null;
            /** @description Provider launch options merged over clusters.config.cloud (see CloudProvider.launch_options) */
            options?: {
                [key: string]: unknown;
            };
            /** @description Default: cluster.region, then config.cloud.region */
            region?: string | null;
            /** @description Default config.cloud.zone; GCP picks the first zone offering the type */
            zone?: string | null;
        };
        /** @description Partial update */
        CloudNodeUpdate: {
            auto_terminate_idle_minutes?: number | null;
            labels?: {
                [key: string]: string;
            } | null;
        };
        CloudPricePlanSuggestion: {
            name: string;
            source?: string | null;
            usd_per_gpu_minute?: number | null;
            usd_per_hour?: number | null;
        };
        CloudProvider: {
            cluster_type: components["schemas"]["cluster_type"];
            /** @description Keys of the JSON secret ('?' = optional); DigitalOcean also accepts a bare token */
            credential_fields: string[];
            /** @description Vault credential type to store the account secret with (provider = id) */
            credential_type: string;
            /** @description In CLOUD_PROVIDERS_ENABLED */
            enabled: boolean;
            id: components["schemas"]["cloud_provider"];
            /** @description Keys accepted in clusters.config.cloud / CloudNodeCreate.options */
            launch_options: string[];
            name: string;
            notes?: string | null;
            pricing: string;
            sdk: string;
            verified_live: boolean;
        };
        CloudProvisionPreview: {
            auto_terminate_idle_minutes?: number | null;
            /** Format: uuid */
            cluster_id: string;
            count: number;
            current_nodes: number;
            disk_gb: number;
            /** @description price x 730 h x count */
            estimated_monthly_usd?: number | null;
            image?: string | null;
            instance_type: components["schemas"]["CloudInstanceType"];
            max_nodes: number;
            names: string[];
            options: {
                [key: string]: unknown;
            };
            price_per_hour_usd?: number | null;
            price_plan_suggestion?: components["schemas"]["CloudPricePlanSuggestion"] | null;
            provider: components["schemas"]["cloud_provider"];
            region: string;
            zone?: string | null;
        };
        CloudQuota: {
            limit?: number | null;
            name: string;
            region?: string | null;
            /** @description vCPU | GPU | count */
            unit: string;
            used?: number | null;
        };
        CloudRegion: {
            available: boolean;
            id: string;
            name: string;
            zones: string[];
        };
        Cluster: {
            /** @description Provider settings: kubernetes {namespace, kubeconfig_credential_id}, cloud {vpc, subnet, instance_types[], ssh_key_credential_id} */
            config?: {
                [key: string]: unknown;
            };
            counts?: components["schemas"]["ClusterCounts"];
            /** Format: date-time */
            created_at: string;
            /** Format: uuid */
            credential_id?: string | null;
            description?: string | null;
            egress_allowlist?: string[];
            /** Format: uuid */
            id: string;
            labels?: {
                [key: string]: string;
            };
            name: string;
            /** Format: uuid */
            price_plan_id?: string | null;
            region?: string | null;
            status: components["schemas"]["cluster_status"];
            type: components["schemas"]["cluster_type"];
            /** Format: date-time */
            updated_at?: string;
        };
        /** @enum {string} */
        cluster_status: "active" | "disabled";
        /** @enum {string} */
        cluster_type: "bare_metal" | "vm" | "kubernetes" | "cloud_aws" | "cloud_azure" | "cloud_gcp" | "cloud_do";
        ClusterCounts: {
            devices?: number;
            instances?: number;
            nodes?: number;
            /** @description Connected nodes (online, draining or drained) */
            nodes_online?: number;
            vram_total_bytes?: number;
            vram_used_bytes?: number;
        };
        ClusterCreate: {
            config?: {
                [key: string]: unknown;
            };
            /** Format: uuid */
            credential_id?: string | null;
            description?: string | null;
            egress_allowlist?: string[];
            labels?: {
                [key: string]: string;
            };
            name: string;
            /** Format: uuid */
            price_plan_id?: string | null;
            region?: string | null;
            type: components["schemas"]["cluster_type"];
        };
        /** @description Partial update; every field optional. */
        ClusterUpdate: {
            config?: {
                [key: string]: unknown;
            };
            /** Format: uuid */
            credential_id?: string | null;
            description?: string | null;
            egress_allowlist?: string[];
            labels?: {
                [key: string]: string;
            };
            name?: string;
            /** Format: uuid */
            price_plan_id?: string | null;
            region?: string | null;
            status?: components["schemas"]["cluster_status"];
            type?: components["schemas"]["cluster_type"];
        };
        /** @description Ids to poll for asynchronous progress */
        CommandRefs: {
            command_ids?: string[];
            swap_operation_ids?: string[];
        };
        /** @enum {string} */
        compat_result: "compatible" | "incompatible" | "unknown";
        CompatCheck: {
            /** Format: date-time */
            checked_at: string;
            device_vendor?: components["schemas"]["device_vendor"] | null;
            engine_name?: components["schemas"]["engine_name"];
            engine_version?: string;
            /** Format: uuid */
            engine_version_id: string;
            estimated_vram_bytes?: number | null;
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            model_version_id: string;
            /** Format: uuid */
            node_id?: string | null;
            reasons: {
                code?: string;
                message?: string;
                /** @enum {string} */
                severity?: "info" | "warning" | "error";
            }[];
            result: components["schemas"]["compat_result"];
            score: number;
        };
        CompatCheckRequest: {
            context_size?: number | null;
            device_vendor?: components["schemas"]["device_vendor"] | null;
            /**
             * Format: uuid
             * @description null = check every enabled engine version
             */
            engine_version_id?: string | null;
            /** @default 1 */
            gpu_count?: number;
            /** Format: uuid */
            model_version_id: string;
            /** Format: uuid */
            node_id?: string | null;
        };
        Credential: {
            access_restrictions?: {
                [key: string]: unknown;
            };
            base_url?: string | null;
            /** Format: date-time */
            created_at: string;
            /** @description Implicit roles (e.g. "Hugging Face downloads (default token)") */
            default_for?: string[];
            description?: string | null;
            /** Format: date-time */
            expires_at?: string | null;
            /** @description Public (non-secret) fields plus derived facts (GCP project_id, SSH fingerprint, kubeconfig context); secret fields are never returned */
            fields?: {
                [key: string]: unknown;
            };
            /** Format: uuid */
            id: string;
            /** @description The typed kind (= provider) when it is one of GET /vault/metadata kinds; null for untyped legacy and system credentials */
            kind?: string | null;
            /** Format: date-time */
            last_used_at?: string | null;
            masked_value?: string | null;
            /** @description extra_data */
            metadata?: {
                [key: string]: unknown;
            };
            name: string;
            /** @description Credential kind (aws, azure, gcp, digitalocean, huggingface, modelscope, slack, smtp, webhook, ssh_key, container_registry, kubernetes, generic) or a legacy / system slug */
            provider?: string | null;
            /** @description Names of the secret fields that hold a value (write-only) */
            secret_fields?: string[];
            status: components["schemas"]["credential_status"];
            /** @description One-line non-secret description (e.g. "alerts@x.com @ smtp.x.com:587", "project my-proj") */
            summary?: string | null;
            /** @description Managed by GPU Deck (internal CA, TLS keys); hidden from lists by default and read-only */
            system?: boolean;
            type: components["schemas"]["credential_type"];
            /** Format: date-time */
            updated_at?: string;
            usage_count?: number;
            used_by?: components["schemas"]["CredentialUsageCounts"];
        };
        /** @enum {string} */
        credential_status: "active" | "expired" | "revoked";
        /** @enum {string} */
        credential_type: "api_key" | "token" | "oauth" | "service_account" | "ssh_key" | "cloud_account" | "smtp" | "webhook" | "tls_key" | "ca_key" | "other";
        CredentialCategory: {
            id: string;
            label: string;
        };
        CredentialCheck: {
            message: string;
            /** @description decrypt | status | schema */
            name: string;
            ok: boolean;
        };
        /** @description Typed (`provider` + `fields`) or legacy (`value`); one of `fields` / `value` is required. */
        CredentialCreate: {
            access_restrictions?: {
                [key: string]: unknown;
            };
            base_url?: string | null;
            description?: string | null;
            /** Format: date-time */
            expires_at?: string | null;
            /** @description The kind's fields; secret ones are write-only */
            fields?: {
                [key: string]: unknown;
            } | null;
            metadata?: {
                [key: string]: unknown;
            };
            name: string;
            /** @description Credential kind (GET /vault/metadata kinds[].id); default generic */
            provider?: string | null;
            /** @description Legacy category; derived from the kind for typed credentials */
            type?: components["schemas"]["credential_type"] | null;
            /** @description Legacy plaintext (the kind's secret, or a JSON object for aws / azure); encrypted before persistence, never returned */
            value?: string | null;
        };
        CredentialDefault: {
            kind?: string | null;
            name?: string | null;
            /** @description Env setting that names the credential (HF_TOKEN_CREDENTIAL ...) */
            setting: string;
            used_for: string;
        };
        CredentialKindField: {
            /** @description Default value (number / boolean / string) */
            default?: unknown;
            help?: string | null;
            /** @enum {string} */
            input: "text" | "password" | "textarea" | "json" | "number" | "boolean" | "url" | "email";
            label: string;
            max_length?: number | null;
            maximum?: number | null;
            min_length?: number | null;
            minimum?: number | null;
            name: string;
            pattern?: string | null;
            pattern_hint?: string | null;
            placeholder?: string | null;
            required: boolean;
            /** @description Write-only; never returned outside the audited reveal */
            secret: boolean;
            /** @description File input accept list when the field can be filled from a file (.json, .pub, kubeconfig) */
            upload_accept?: string | null;
        };
        CredentialKindInfo: {
            /** @enum {string} */
            category: "cloud" | "model_source" | "notification" | "access" | "other";
            cloud_provider?: string | null;
            consumers?: string[];
            credential_type: components["schemas"]["credential_type"];
            /** @description Public facts computed from the fields (GCP project_id, SSH fingerprint ...) */
            derived_fields?: string[];
            description: string;
            docs_url?: string | null;
            fields: components["schemas"]["CredentialKindField"][];
            /** @description Stored in credentials.provider */
            id: string;
            /** @description JSON Schema (2020-12) the fields are validated against */
            json_schema: {
                [key: string]: unknown;
            };
            label: string;
            secret_fields?: string[];
            /** @enum {string} */
            test?: "format" | "cloud";
            used_for?: string[];
        };
        CredentialReference: {
            /** @description Cluster / channel type, model source or VM status */
            detail?: string | null;
            /** @description Where the reference lives (credential_id, config.ssh_key_credential_id, metadata.credential_id, ...) */
            field: string;
            name: string;
            /** Format: uuid */
            resource_id: string;
            /** @enum {string} */
            resource_type: "cluster" | "alert_channel" | "model" | "cloud_instance";
        };
        CredentialRevealed: {
            /** @description Every field of a typed credential, secret ones included (`{value}` for untyped) */
            fields?: {
                [key: string]: unknown;
            };
            /** Format: uuid */
            id: string;
            /** @description Plaintext, returned once per call; audited and recorded in credential_usage_history */
            value: string;
        };
        CredentialStats: {
            active?: number;
            /** @description Counts per kind (`untyped` for legacy rows); system credentials excluded */
            by_kind?: {
                [key: string]: number;
            };
            expired?: number;
            expiring_soon?: number;
            total?: number;
        };
        CredentialTestResult: {
            checks: components["schemas"]["CredentialCheck"][];
            /** @description Live provider check for cloud kinds (POST, body {credential_id}) */
            cloud_check_path?: string | null;
            /** @description aws | azure | gcp | digitalocean for cloud kinds */
            cloud_provider?: string | null;
            /** Format: uuid */
            id: string;
            kind?: string | null;
            ok: boolean;
            /** Format: date-time */
            tested_at: string;
        };
        /** @description Partial update; every field optional. `fields` merge (secret fields left out keep their value). */
        CredentialUpdate: {
            access_restrictions?: {
                [key: string]: unknown;
            };
            base_url?: string | null;
            description?: string | null;
            /** Format: date-time */
            expires_at?: string | null;
            fields?: {
                [key: string]: unknown;
            } | null;
            metadata?: {
                [key: string]: unknown;
            };
            name?: string;
            provider?: string | null;
            type?: components["schemas"]["credential_type"];
            /** @description Plaintext secret; encrypted before persistence, never returned */
            value?: string;
        };
        CredentialUsage: {
            /** Format: uuid */
            credential_id: string;
            default_for: string[];
            history: components["schemas"]["CredentialUsageEntry"][];
            references: components["schemas"]["CredentialReference"][];
            used_by: components["schemas"]["CredentialUsageCounts"];
        };
        CredentialUsageCounts: {
            alert_channels?: number;
            cloud_instances?: number;
            clusters?: number;
            models?: number;
            total?: number;
        };
        CredentialUsageEntry: {
            purpose?: string | null;
            /** Format: date-time */
            used_at: string;
            /** Format: uuid */
            used_by?: string | null;
            used_by_username?: string | null;
        };
        Crl: {
            /** @description X.509 CRL in PEM for consumers that prefer it */
            crl_pem?: string | null;
            fingerprints_sha256: string[];
            /** Format: date-time */
            generated_at: string;
            serials: string[];
        };
        CsrfToken: {
            /** @default gpudeck_csrf */
            cookie_name: string;
            csrf_token: string;
            /** @default X-CSRF-Token */
            header_name: string;
        };
        Deployment: components["schemas"]["DeploymentSpec"] & {
            active_swap_ids?: string[];
            /** Format: date-time */
            created_at: string;
            desired_running?: boolean;
            effective_priority?: components["schemas"]["model_priority"];
            engine_name?: components["schemas"]["engine_name"] | null;
            engine_version?: string | null;
            external?: components["schemas"]["ExternalInfo"] | null;
            /** Format: uuid */
            id: string;
            instances?: components["schemas"]["Instance"][];
            /** Format: date-time */
            last_request_at?: string | null;
            live?: components["schemas"]["InstanceLive"];
            model_name?: string;
            model_type?: components["schemas"]["model_type"];
            origin?: components["schemas"]["deployment_origin"];
            ready_replicas?: number;
            status: components["schemas"]["deployment_status"];
            status_message?: string | null;
            /** Format: date-time */
            updated_at?: string;
        };
        /**
         * @description managed = GPU Deck runs it; external = a model server GPU Deck did not start and only observes (read-only)
         * @enum {string}
         */
        deployment_origin: "managed" | "external";
        /** @enum {string} */
        deployment_preset: "low_latency" | "high_throughput";
        /** @enum {string} */
        deployment_status: "pending" | "scheduling" | "deploying" | "running" | "degraded" | "scaling" | "stopped" | "failed" | "deleting";
        DeploymentCreate: components["schemas"]["DeploymentSpec"] & {
            /**
             * @description Validate + placement preview only
             * @default false
             */
            dry_run?: boolean;
            /**
             * @description Schedule and load immediately
             * @default true
             */
            start?: boolean;
        };
        DeploymentSpec: {
            /** Format: uuid */
            cluster_id?: string | null;
            context_size?: number | null;
            control_vectors?: {
                [key: string]: unknown;
            }[];
            description?: string | null;
            device_vendor_filter?: components["schemas"]["device_vendor"][];
            /**
             * Format: uuid
             * @description null = auto-pick
             */
            engine_version_id?: string | null;
            env_vars?: {
                [key: string]: string;
            };
            /** @default 1 */
            gpu_count: number;
            /** @default false */
            kv_sharing: boolean;
            kv_sharing_config?: {
                [key: string]: unknown;
            };
            /** @description {"flag": value}; true = bare flag */
            launch_flags?: {
                [key: string]: unknown;
            };
            /** @default 1 */
            max_replicas: number;
            /** @default 0 */
            min_replicas: number;
            /**
             * Format: uuid
             * @description Catalog model. Required to create a deployment; null only on external (observed) deployments.
             */
            model_id: string | null;
            /**
             * Format: uuid
             * @description null = model default version
             */
            model_version_id?: string | null;
            name: string;
            node_selector?: {
                [key: string]: unknown;
            };
            pd_config?: {
                [key: string]: unknown;
            };
            /** @default false */
            pd_split: boolean;
            placement_strategy?: components["schemas"]["placement_strategy"];
            /** @default 1 */
            pp_size: number;
            preset?: components["schemas"]["deployment_preset"] | null;
            priority_override?: components["schemas"]["model_priority"] | null;
            /** @default 1 */
            replicas: number;
            slice_compute_pct?: number | null;
            slice_vram_bytes?: number | null;
            spec_decoding?: {
                [key: string]: unknown;
            };
            tool_parser?: string | null;
            /** @default 1 */
            tp_size: number;
            traffic_config?: {
                [key: string]: unknown;
            };
        };
        /** @description Partial update. Changes to model_version_id, engine_version_id, gpu_count, slices, tp/pp, launch_flags, context_size, control_vectors or env_vars trigger a rolling swap of every instance; replicas/weights/traffic_config apply live. */
        DeploymentUpdate: {
            /** Format: uuid */
            cluster_id?: string | null;
            context_size?: number | null;
            control_vectors?: {
                [key: string]: unknown;
            }[];
            description?: string | null;
            device_vendor_filter?: components["schemas"]["device_vendor"][];
            /**
             * Format: uuid
             * @description null = auto-pick
             */
            engine_version_id?: string | null;
            env_vars?: {
                [key: string]: string;
            };
            /** @default 1 */
            gpu_count?: number;
            /** @default false */
            kv_sharing?: boolean;
            kv_sharing_config?: {
                [key: string]: unknown;
            };
            /** @description {"flag": value}; true = bare flag */
            launch_flags?: {
                [key: string]: unknown;
            };
            /** @default 1 */
            max_replicas?: number;
            /** @default 0 */
            min_replicas?: number;
            /**
             * Format: uuid
             * @description null = model default version
             */
            model_version_id?: string | null;
            name?: string;
            node_selector?: {
                [key: string]: unknown;
            };
            pd_config?: {
                [key: string]: unknown;
            };
            /** @default false */
            pd_split?: boolean;
            placement_strategy?: components["schemas"]["placement_strategy"];
            /** @default 1 */
            pp_size?: number;
            preset?: components["schemas"]["deployment_preset"] | null;
            priority_override?: components["schemas"]["model_priority"] | null;
            /** @default 1 */
            replicas?: number;
            slice_compute_pct?: number | null;
            slice_vram_bytes?: number | null;
            spec_decoding?: {
                [key: string]: unknown;
            };
            tool_parser?: string | null;
            /** @default 1 */
            tp_size?: number;
            traffic_config?: {
                [key: string]: unknown;
            };
        };
        Device: {
            /**
             * @description From live data, not only GPU Deck instances (external = memory or load from processes outside GPU Deck)
             * @enum {string}
             */
            activity?: "detached" | "stale" | "gpu_deck" | "mixed" | "external" | "idle";
            /** Format: uuid */
            cluster_id?: string;
            compute_capability?: string | null;
            core_count?: number | null;
            /** Format: date-time */
            created_at?: string;
            driver_version?: string | null;
            extra?: {
                [key: string]: unknown;
            };
            health: components["schemas"]["device_health"];
            health_reason?: string | null;
            /** Format: uuid */
            id: string;
            index: number;
            instance_ids?: string[];
            is_unified_memory?: boolean;
            labels?: {
                [key: string]: string;
            };
            /** Format: date-time */
            last_seen_at?: string | null;
            live?: components["schemas"]["DeviceLive"];
            max_power_watts?: number | null;
            name: string;
            node_detached?: boolean;
            /** Format: uuid */
            node_id: string;
            node_name?: string;
            numa_node?: number | null;
            outside_processes?: number;
            pci_bus_id?: string | null;
            /** @description Processes holding memory on this GPU (NVIDIA / NVML, latest sample) */
            processes?: components["schemas"]["DeviceProcess"][];
            /** Format: date-time */
            processes_ts?: string | null;
            runtime_version?: string | null;
            slices?: components["schemas"]["DeviceSlice"][];
            /** Format: date-time */
            updated_at?: string;
            uuid?: string | null;
            vendor: components["schemas"]["device_vendor"];
            /** @description total - headroom - max(reserved, GPU Deck use) - used outside GPU Deck: what placement and new slices can still take */
            vram_free_bytes?: number;
            /** @description Scheduler headroom kept free (SCHEDULER_VRAM_HEADROOM_PCT) */
            vram_headroom_bytes?: number;
            /** @description Sum of active/reserved slices */
            vram_reserved_bytes?: number;
            vram_total_bytes: number;
            /** @description No fresh sample, so vram_free_bytes counts reservations only */
            vram_usage_stale?: boolean;
            /** @description Memory in use from the latest fresh sample (null when stale) */
            vram_used_bytes?: number | null;
            /** @description Part of vram_used_bytes held by processes GPU Deck does not run (external servers, anything else); null when the per-process list is unknown */
            vram_used_outside_bytes?: number | null;
        };
        /** @enum {string} */
        device_health: "healthy" | "degraded" | "unhealthy" | "unknown";
        /** @enum {string} */
        device_slice_status: "reserved" | "active" | "released";
        /** @enum {string} */
        device_vendor: "nvidia" | "amd" | "hygon" | "metax" | "moorethreads" | "thead" | "apple";
        /** @description Latest sample (<= METRICS_LIVE_STALE_SECONDS old) or nulls */
        DeviceLive: {
            memory_used_bytes?: number | null;
            power_watts?: number | null;
            temperature_c?: number | null;
            /** Format: date-time */
            ts?: string | null;
            utilization_pct?: number | null;
        };
        DeviceProcess: {
            /** Format: uuid */
            deployment_id?: string | null;
            deployment_name?: string | null;
            /** Format: uuid */
            instance_id?: string | null;
            /** @description Executable name (never the command line arguments) */
            name?: string | null;
            /**
             * @description managed (GPU Deck runs it), external (an observed server) or null (not GPU Deck's)
             * @enum {string|null}
             */
            origin?: "managed" | "external" | null;
            pid: number;
            used_memory_bytes?: number | null;
        };
        DeviceSlice: {
            compute_pct: number;
            /** Format: date-time */
            created_at?: string;
            /** Format: uuid */
            deployment_id?: string | null;
            /** Format: uuid */
            device_id: string;
            /**
             * Format: uuid
             * @description Set when an SSH GPU instance holds the slice (whole-device reservations too)
             */
            gpu_instance_id?: string | null;
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            instance_id?: string | null;
            name?: string | null;
            /** Format: date-time */
            released_at?: string | null;
            status: components["schemas"]["device_slice_status"];
            vram_bytes: number;
        };
        DeviceSliceCreate: {
            /** @description Default 100 */
            compute_pct?: number | null;
            /**
             * Format: uuid
             * @description Place one more replica of this sliced deployment here
             */
            deployment_id?: string | null;
            name?: string | null;
            /** @description Required for a standalone slice; must equal the deployment's slice size when deployment_id is set */
            vram_bytes?: number | null;
        };
        DeviceSliceResult: {
            command_ids?: string[];
            /** Format: uuid */
            deployment_id?: string | null;
            /** Format: uuid */
            gpu_instance_id?: string | null;
            /** Format: uuid */
            instance_id?: string | null;
            message?: string | null;
            rolling_update: boolean;
            slice: components["schemas"]["DeviceSlice"];
        };
        DeviceSliceUpdate: {
            compute_pct?: number | null;
            name?: string | null;
            vram_bytes?: number | null;
        };
        DrainRequest: {
            /**
             * @description Move instances to other nodes via the scheduler before marking drained; false = just unload
             * @default true
             */
            move_instances?: boolean;
            reason?: string | null;
            /** @default 300 */
            timeout_seconds?: number;
        };
        Engine: {
            capabilities?: {
                [key: string]: unknown;
            };
            /** Format: date-time */
            created_at?: string;
            description?: string | null;
            display_name: string;
            /** Format: uuid */
            id: string;
            is_builtin: boolean;
            is_enabled: boolean;
            name: components["schemas"]["engine_name"];
            runtime: components["schemas"]["engine_runtime"];
            supported_formats?: components["schemas"]["model_file_format"][];
            supported_model_types?: components["schemas"]["model_type"][];
            supported_os?: components["schemas"]["node_os"][];
            supported_vendors?: components["schemas"]["device_vendor"][];
            /** Format: date-time */
            updated_at?: string;
            versions?: components["schemas"]["EngineVersion"][];
        };
        /** @enum {string} */
        engine_name: "llama_cpp" | "vllm" | "vllm_omni" | "sglang" | "sglang_diffusion" | "tensorrt_llm" | "mlx" | "mlx_audio" | "whisper_cpp" | "stable_diffusion_cpp" | "custom";
        /** @enum {string} */
        engine_runtime: "native" | "container";
        /** @enum {string} */
        engine_version_status: "available" | "deprecated" | "disabled";
        EngineUpdate: {
            capabilities?: {
                [key: string]: unknown;
            };
            description?: string | null;
            display_name?: string;
            is_enabled?: boolean;
            supported_formats?: components["schemas"]["model_file_format"][];
            supported_model_types?: components["schemas"]["model_type"][];
            supported_vendors?: components["schemas"]["device_vendor"][];
        };
        EngineVersion: {
            /** @description {"linux/x86_64": {url, sha256, kind}, "macos/aarch64": {...}} */
            artifacts?: {
                [key: string]: unknown;
            };
            /** Format: date-time */
            created_at?: string;
            default_launch_flags?: {
                [key: string]: unknown;
            };
            /** Format: uuid */
            engine_id: string;
            engine_name: components["schemas"]["engine_name"];
            health_config?: {
                [key: string]: unknown;
            };
            /** Format: uuid */
            id: string;
            image?: string | null;
            is_default: boolean;
            launch_template?: {
                [key: string]: unknown;
            };
            metrics_config?: {
                [key: string]: unknown;
            };
            min_driver_version?: string | null;
            /** @description [start, end) */
            port_range?: number[];
            requirements?: {
                [key: string]: unknown;
            };
            status: components["schemas"]["engine_version_status"];
            /** Format: date-time */
            updated_at?: string;
            version: string;
        };
        EngineVersionCreate: {
            artifacts?: {
                [key: string]: unknown;
            };
            default_launch_flags?: {
                [key: string]: unknown;
            };
            health_config?: {
                [key: string]: unknown;
            };
            image?: string | null;
            /** @default false */
            is_default?: boolean;
            launch_template?: {
                [key: string]: unknown;
            };
            metrics_config?: {
                [key: string]: unknown;
            };
            min_driver_version?: string | null;
            port_range?: number[];
            requirements?: {
                [key: string]: unknown;
            };
            status?: components["schemas"]["engine_version_status"];
            version: string;
        };
        /** @description Partial update; every field optional. */
        EngineVersionUpdate: {
            artifacts?: {
                [key: string]: unknown;
            };
            default_launch_flags?: {
                [key: string]: unknown;
            };
            health_config?: {
                [key: string]: unknown;
            };
            image?: string | null;
            /** @default false */
            is_default?: boolean;
            launch_template?: {
                [key: string]: unknown;
            };
            metrics_config?: {
                [key: string]: unknown;
            };
            min_driver_version?: string | null;
            port_range?: number[];
            requirements?: {
                [key: string]: unknown;
            };
            status?: components["schemas"]["engine_version_status"];
            version?: string;
        };
        ErrorBody: {
            /** @description Stable machine code (AUTHENTICATION_ERROR, INVALID_CREDENTIALS, VALIDATION_ERROR, RESOURCE_NOT_FOUND, DUPLICATE_RESOURCE, RATE_LIMIT_EXCEEDED, CSRF_FAILED, ACCOUNT_LOCKED, IP_BLOCKED, SWAP_IN_PROGRESS, NO_CAPACITY, INTERNAL_ERROR, ...) */
            code: string;
            /** @description Only in DEBUG; validation errors list {field, message, type}. */
            details?: Record<string, unknown> | unknown[] | null;
            field?: string | null;
            message: string;
        };
        ErrorEnvelope: {
            data: null;
            error: components["schemas"]["ErrorBody"];
            meta: Record<string, unknown> | null;
            /** @constant */
            success: false;
        };
        EvictRequest: {
            cache_item_ids?: string[];
            /** @default false */
            force?: boolean;
            /** Format: uuid */
            model_version_id?: string | null;
            node_ids: string[];
            target_free_bytes?: number | null;
            /** @default false */
            unpin_only?: boolean;
            unpinned_older_than_seconds?: number | null;
        };
        ExternalEndpointStat: {
            count: number;
            method: string;
            path: string;
        };
        /** @description What the node agent reports about an external (observed) server. GPU Deck never changes it. */
        ExternalInfo: {
            device_ids?: string[];
            /** @description host:port */
            endpoint?: string | null;
            /** @enum {string} */
            engine: "vllm" | "sglang" | "llamacpp" | "tgi" | "openai" | "generic";
            engine_label?: string | null;
            /** Format: date-time */
            first_seen_at?: string | null;
            health?: components["schemas"]["instance_health"] | null;
            /** Format: date-time */
            last_ok_at?: string | null;
            /** Format: date-time */
            last_seen_at?: string | null;
            /** @description Some figures come from the server log (token totals estimated from logged throughput) */
            log_derived?: boolean;
            message?: string | null;
            models?: string[];
            name: string;
            /** Format: uuid */
            node_id?: string | null;
            node_name?: string | null;
            pid?: number | null;
            process_vram_bytes?: number | null;
            requests_per_minute?: number | null;
            sources?: ("metrics" | "health" | "models" | "log")[];
            /**
             * Format: date-time
             * @description Set when the agent stopped reporting it (removed after a day)
             */
            stale_since?: string | null;
            /** @description HTTP status classes from the access log (2xx, 4xx, 5xx...) */
            status_counts?: {
                [key: string]: number;
            };
            top_endpoints?: components["schemas"]["ExternalEndpointStat"][];
            version?: string | null;
        };
        /** @enum {string} */
        gpu_instance_state: "requested" | "placing" | "starting" | "running" | "stopping" | "stopped" | "error";
        GpuInstance: {
            active_connections?: number;
            allowed_source_cidrs?: string[];
            authorized_keys?: {
                comment?: string | null;
                fingerprint?: string;
                type?: string;
            }[];
            /** Format: uuid */
            cluster_id?: string | null;
            connection: components["schemas"]["GpuInstanceConnection"];
            connections_total?: number;
            container_id?: string | null;
            container_port: number;
            /** Format: date-time */
            created_at: string;
            description?: string | null;
            device_ids?: string[];
            device_vendor?: string | null;
            env?: {
                [key: string]: string;
            };
            /** Format: date-time */
            expires_at?: string | null;
            gpu_attached?: boolean;
            gpu_count: number;
            /** @description Metered GPU-seconds (usage_events endpoint gpu_instance) */
            gpu_seconds_total?: number;
            health?: string;
            /** Format: uuid */
            id: string;
            idle_timeout_seconds?: number | null;
            image: string;
            labels?: {
                [key: string]: string;
            };
            /** Format: date-time */
            last_connection_at?: string | null;
            limits?: Record<string, unknown>;
            name: string;
            /** Format: uuid */
            node_id?: string | null;
            node_name?: string | null;
            node_selector?: Record<string, unknown>;
            /** Format: date-time */
            running_at?: string | null;
            slice_compute_pct?: number | null;
            slice_vram_bytes?: number | null;
            ssh_user: string;
            /** Format: uuid */
            start_command_id?: string | null;
            /** Format: date-time */
            started_at?: string | null;
            state: components["schemas"]["gpu_instance_state"];
            /** Format: date-time */
            state_changed_at?: string | null;
            state_message?: string | null;
            /** Format: uuid */
            stop_command_id?: string | null;
            /** @description user | ttl_expired | idle_timeout | deleted | slice_deleted */
            stop_reason?: string | null;
            /** Format: date-time */
            stopped_at?: string | null;
            ttl_seconds?: number | null;
            /** Format: date-time */
            updated_at?: string | null;
            volumes?: Record<string, unknown>[];
            warnings?: string[];
        };
        GpuInstanceConnection: {
            host?: string | null;
            /** @description OpenSSH SHA256:<base64> */
            host_key_fingerprint?: string | null;
            host_keys?: {
                fingerprint?: string;
                public_key?: string | null;
                type?: string;
            }[];
            known_hosts?: string[];
            port?: number | null;
            ready: boolean;
            /** @example ssh -p 2201 gpudeck@10.0.0.12 */
            ssh_command?: string | null;
            user: string;
        };
        GpuInstanceCreate: {
            /** @default [] */
            allowed_source_cidrs?: string[];
            /** @description OpenSSH public key lines (no options prefix) */
            authorized_keys: string[];
            /** Format: uuid */
            cluster_id?: string | null;
            container_port?: number | null;
            description?: string | null;
            /** @enum {string|null} */
            device_vendor?: "nvidia" | "amd" | "hygon" | "metax" | "moorethreads" | "thead" | "apple" | null;
            /** @default {} */
            env?: {
                [key: string]: string;
            };
            /**
             * @description Whole GPUs (exclusive); 0 = CPU-only container
             * @default 1
             */
            gpu_count?: number;
            /** @description Auto-stop after this long without an SSH connection */
            idle_timeout_seconds?: number | null;
            /** @description Container image running sshd (protocol.md §8). Default GPU_INSTANCE_DEFAULT_IMAGE (ghcr.io/sur950/gpu-deck-ssh:1, Ubuntu + OpenSSH, container_port 2222; published with the first release). */
            image?: string | null;
            /** @default {} */
            labels?: {
                [key: string]: string;
            };
            limits?: components["schemas"]["GpuInstanceLimits"];
            name: string;
            /**
             * @description Node labels plus node_ids / device_ids pinning
             * @default {}
             */
            node_selector?: Record<string, unknown>;
            slice_compute_pct?: number | null;
            /**
             * Format: uuid
             * @description Take an existing standalone slice (POST /devices/{id}/slices)
             */
            slice_id?: string | null;
            /** @description One GPU slice of this VRAM instead of whole GPUs (gpu_count 1) */
            slice_vram_bytes?: number | null;
            /** @description Requested host port (default: agent picks from 2200-2299) */
            ssh_port?: number | null;
            /** @default gpudeck */
            ssh_user?: string;
            /** @description Auto-stop after this long (default GPU_INSTANCE_DEFAULT_TTL_SECONDS) */
            ttl_seconds?: number | null;
            /** @default [] */
            volumes?: components["schemas"]["GpuInstanceVolume"][];
            /**
             * @description Queue the request (state requested) instead of 409 when nothing fits
             * @default 0
             */
            wait_for_capacity_seconds?: number;
        };
        GpuInstanceLimits: {
            cpu_cores?: number | null;
            memory_bytes?: number | null;
            pids?: number | null;
            shm_bytes?: number | null;
        };
        GpuInstanceStopRequest: {
            /** @default false */
            force?: boolean;
            /** @default true */
            remove_volumes?: boolean;
        };
        GpuInstanceUpdate: {
            /** @description Only while stopped */
            authorized_keys?: string[] | null;
            description?: string | null;
            /** @description 0 disables */
            idle_timeout_seconds?: number | null;
            labels?: {
                [key: string]: string;
            } | null;
            /** @description New TTL from now; 0 removes it */
            ttl_seconds?: number | null;
        };
        GpuInstanceVolume: {
            /** @default false */
            keep?: boolean;
            /** @default false */
            read_only?: boolean;
            size_bytes?: number | null;
            source?: string | null;
            target: string;
            /**
             * @default volume
             * @enum {string}
             */
            type?: "volume" | "bind" | "tmpfs";
        };
        Health: {
            /** @description database, redis, celery, agent_hub, jobs_leader, api_layer */
            checks: {
                [key: string]: components["schemas"]["ServiceHealth"];
            };
            /** @enum {string} */
            status: "healthy" | "degraded" | "unhealthy";
            /** Format: date-time */
            timestamp: string;
            version: string;
        };
        Instance: {
            container_id?: string | null;
            /** Format: date-time */
            created_at: string;
            /** Format: uuid */
            deployment_id: string;
            deployment_name?: string;
            device_ids: string[];
            engine_name?: components["schemas"]["engine_name"];
            /** Format: uuid */
            engine_process_id?: string | null;
            health: components["schemas"]["instance_health"];
            health_message?: string | null;
            /** Format: uuid */
            id: string;
            /** Format: date-time */
            last_health_at?: string | null;
            /** Format: date-time */
            last_request_at?: string | null;
            live?: components["schemas"]["InstanceLive"];
            model_name?: string;
            /** Format: uuid */
            node_id: string;
            node_name?: string;
            origin?: components["schemas"]["deployment_origin"];
            pd_role?: string | null;
            pid?: number | null;
            port?: number | null;
            /** Format: date-time */
            ready_at?: string | null;
            resources?: {
                [key: string]: unknown;
            };
            restart_count?: number;
            served_model_name?: string | null;
            /** Format: date-time */
            started_at?: string | null;
            state: components["schemas"]["instance_state"];
            /** Format: date-time */
            state_changed_at?: string;
            state_message?: string | null;
            /** Format: date-time */
            stopped_at?: string | null;
            traffic_weight: number;
            /** Format: date-time */
            updated_at?: string;
        };
        /** @enum {string} */
        instance_health: "healthy" | "unhealthy" | "unknown";
        /** @enum {string} */
        instance_state: "pending" | "loading" | "ready" | "draining" | "unloading" | "stopped" | "error";
        InstanceDrainRequest: {
            /**
             * @description What to do once drained
             * @enum {string}
             */
            then?: "none" | "unload" | "restart";
            /** @default 120 */
            timeout_seconds?: number;
        };
        InstanceLive: {
            active_sessions?: number | null;
            error_rate_pct?: number | null;
            generation_tps?: number | null;
            kv_cache_usage_pct?: number | null;
            requests_per_minute?: number | null;
            /** Format: date-time */
            ts?: string | null;
            ttft_ms_p95?: number | null;
            waiting_sessions?: number | null;
        };
        InstanceUpdate: {
            traffic_weight?: number;
        };
        /** @enum {string} */
        ip_rule_action: "allow" | "block";
        /** @enum {string} */
        ip_rule_scope: "admin" | "api_layer";
        IpCheckRequest: {
            ip: string;
            scope: components["schemas"]["ip_rule_scope"];
        };
        IpCheckResult: {
            allowed: boolean;
            ip: string;
            matched_rule?: components["schemas"]["IpRule"] | null;
            /** @description blocked_by_rule | not_in_allowlist | allowed_by_rule | no_rules | env_allowlist */
            reason: string;
            scope: components["schemas"]["ip_rule_scope"];
        };
        IpRule: {
            action: components["schemas"]["ip_rule_action"];
            /** @description IPv4/IPv6 CIDR */
            cidr: string;
            /** Format: date-time */
            created_at: string;
            description?: string | null;
            enabled: boolean;
            /** Format: date-time */
            expires_at?: string | null;
            /** Format: uuid */
            id: string;
            scope: components["schemas"]["ip_rule_scope"];
            /** Format: date-time */
            updated_at?: string;
        };
        IpRuleCreate: {
            action: components["schemas"]["ip_rule_action"];
            cidr: string;
            description?: string | null;
            /** @default true */
            enabled?: boolean;
            /** Format: date-time */
            expires_at?: string | null;
            scope: components["schemas"]["ip_rule_scope"];
        };
        /** @description Partial update; every field optional. */
        IpRuleUpdate: {
            action?: components["schemas"]["ip_rule_action"];
            cidr?: string;
            description?: string | null;
            /** @default true */
            enabled?: boolean;
            /** Format: date-time */
            expires_at?: string | null;
            scope?: components["schemas"]["ip_rule_scope"];
        };
        JoinToken: {
            /** Format: uuid */
            cluster_id: string;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            expires_at: string;
            /** Format: uuid */
            id: string;
            labels?: {
                [key: string]: string;
            };
            /**
             * Format: date-time
             * @description Time of the most recent join
             */
            last_used_at?: string | null;
            /**
             * @description Machines the token may enrol (1 = one-time token)
             * @default 1
             */
            max_uses: number;
            node_name_hint?: string | null;
            /** Format: date-time */
            revoked_at?: string | null;
            token_prefix: string;
            /**
             * @description Successful joins so far
             * @default 0
             */
            use_count: number;
            /**
             * Format: date-time
             * @description Set when the token is used up (use_count reached max_uses)
             */
            used_at?: string | null;
            /**
             * Format: uuid
             * @description Node created by the most recent join
             */
            used_by_node_id?: string | null;
        };
        JoinTokenCreate: {
            /** Format: uuid */
            cluster_id: string;
            labels?: {
                [key: string]: string;
            };
            /**
             * @description Machines the token may enrol. Use > 1 for Kubernetes DaemonSets and node pools; node_name_hint then must be empty (each machine uses its hostname)
             * @default 1
             */
            max_uses?: number;
            node_name_hint?: string | null;
            /**
             * @description Lifetime in minutes; at most 1440 for a one-time token, up to a year for a multi-use enrolment token
             * @default 15
             */
            ttl_minutes?: number;
        };
        JoinTokenCreated: components["schemas"]["JoinToken"] & {
            ca_fingerprint_sha256?: string;
            /** @description sha256 of the PEM served at <hub>/agent/v1/ca.pem (pinned by install_command) */
            ca_pem_sha256?: string | null;
            /** @description Agent hub URL (AGENT_HUB_PUBLIC_URL); GPU machines need only this host */
            hub_url?: string | null;
            /** @description One-liner run on the GPU machine. It talks only to the hub: fetches <hub>/agent/v1/ca.pem, checks its pinned sha256, then runs <hub>/agent/v1/install.sh with the token */
            install_command?: string | null;
            /** @description <hub>/agent/v1/install.sh */
            install_script_url?: string | null;
            /** @description Copy-paste command: gpudeck-agent join --hub https://... --token gdj_... --ca-fingerprint ... */
            join_command: string;
            /** @description Plaintext gdj_... token, shown once */
            token: string;
        };
        Layout: {
            /** Format: date-time */
            applied_at?: string | null;
            /** Format: date-time */
            created_at: string;
            description?: string | null;
            /** Format: uuid */
            id: string;
            is_active: boolean;
            items: components["schemas"]["LayoutItem"][];
            name: string;
            /** @enum {string} */
            unlisted_policy?: "keep" | "unload";
            /** Format: date-time */
            updated_at?: string;
            used_by_rule_ids?: string[];
        };
        LayoutApplyRequest: {
            /** @default 300 */
            drain_timeout_seconds?: number;
            /**
             * @description Only issue prefetch commands for the layout's models
             * @default false
             */
            prefetch_only?: boolean;
        };
        LayoutApplyResult: {
            command_ids?: string[];
            diff: components["schemas"]["LayoutDiff"];
            /** Format: uuid */
            layout_id: string;
            swap_operation_ids: string[];
        };
        LayoutCaptureRequest: {
            /**
             * Format: uuid
             * @description Limit to one cluster
             */
            cluster_id?: string | null;
            description?: string | null;
            name: string;
        };
        LayoutCreate: {
            description?: string | null;
            items: components["schemas"]["LayoutItemInput"][];
            name: string;
            /** @enum {string} */
            unlisted_policy?: "keep" | "unload";
        };
        LayoutDiff: {
            entries: components["schemas"]["LayoutDiffEntry"][];
            estimated_seconds?: number | null;
            feasible: boolean;
            /** Format: uuid */
            layout_id: string;
            warnings?: string[];
        };
        LayoutDiffEntry: {
            /** @enum {string} */
            action: "load" | "unload" | "move" | "scale" | "reweight" | "noop";
            /** Format: uuid */
            deployment_id: string;
            deployment_name?: string;
            from?: {
                [key: string]: unknown;
            } | null;
            needs_prefetch_bytes?: number;
            placement?: components["schemas"]["PlacementPreview"] | null;
            /** @description always_on kept despite unlisted_policy=unload */
            protected?: boolean;
            to?: {
                [key: string]: unknown;
            } | null;
        };
        LayoutItem: {
            /** Format: uuid */
            deployment_id: string;
            deployment_name?: string;
            device_ids?: string[];
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            node_id?: string | null;
            node_name?: string | null;
            position: number;
            replicas: number;
            slice_compute_pct?: number | null;
            slice_vram_bytes?: number | null;
            traffic_weight: number;
        };
        /** @description Mirror of libs/contracts/rules/layout.schema.json items[] */
        LayoutItemInput: {
            /** Format: uuid */
            deployment_id: string;
            device_ids?: string[];
            /** Format: uuid */
            node_id?: string | null;
            position?: number | null;
            /** @default 1 */
            replicas?: number;
            slice?: {
                /** @default 100 */
                compute_pct: number;
                vram_bytes: number;
            } | null;
            /** @default 100 */
            traffic_weight?: number;
        };
        /** @description Partial update; every field optional. */
        LayoutUpdate: {
            description?: string | null;
            items?: components["schemas"]["LayoutItemInput"][];
            name?: string;
            /** @enum {string} */
            unlisted_policy?: "keep" | "unload";
        };
        ListMeta: {
            has_more: boolean;
            page: number;
            page_size: number;
            total: number;
        } & {
            [key: string]: unknown;
        };
        /** @description SSE frame: `event: <event>` line + `data: <json of data>`; WebSocket frame: this whole object as JSON. */
        LiveEvent: {
            data: {
                [key: string]: unknown;
            };
            /** @enum {string} */
            event: "metrics" | "instance" | "node" | "device" | "swap" | "rule_run" | "alert" | "log" | "heartbeat";
            /** Format: date-time */
            ts: string;
        };
        LocalScanCandidate: {
            files: components["schemas"]["CatalogRemoteFile"][];
            format: components["schemas"]["model_file_format"];
            /** @enum {string} */
            kind: "plain" | "ollama_blob" | "lmstudio" | "hf_cache" | "mlx_dir";
            /**
             * Format: uuid
             * @description Set for node scans; register the model with metadata.node_id (versions scanned/hashed on that node, deployments pinned to it)
             */
            node_id?: string | null;
            path: string;
            suggested?: {
                context_size?: number | null;
                family?: string | null;
                name?: string;
                quantization?: string | null;
                type?: components["schemas"]["model_type"] | null;
            };
            total_size_bytes: number;
        };
        LocalScanRequest: {
            /** @default 4 */
            max_depth?: number;
            /**
             * Format: uuid
             * @description Scan on this node's disk through the agent instead of the control plane container
             */
            node_id?: string | null;
            /** @description Absolute path (or `~` / `~/...`, expanded on the scanning host) on the control plane host or a node (node_id set) — a file or a directory */
            path: string;
            /** @default true */
            recursive?: boolean;
        };
        /** @enum {string} */
        log_level: "debug" | "info" | "warn" | "error";
        /** @enum {string} */
        log_source: "engine" | "agent" | "control_plane" | "rule" | "swap" | "api_layer";
        /** @enum {string} */
        log_stream: "stdout" | "stderr" | "app";
        LogChunk: {
            attributes?: {
                [key: string]: unknown;
            } | null;
            /** @description Newline-separated lines */
            content: string;
            /** Format: uuid */
            deployment_id?: string | null;
            id: number;
            /** Format: uuid */
            instance_id?: string | null;
            level: components["schemas"]["log_level"];
            line_count: number;
            logger?: string | null;
            /** Format: uuid */
            node_id?: string | null;
            node_name?: string | null;
            /** Format: uuid */
            request_id?: string | null;
            source: components["schemas"]["log_source"];
            stream: components["schemas"]["log_stream"];
            /** Format: date-time */
            ts: string;
            /** Format: date-time */
            ts_end?: string | null;
        };
        LoginRequest: {
            password: string;
            username: string;
        };
        /** @description Set-Cookie: gpudeck_session (HttpOnly, Secure in production, SameSite=Strict) and gpudeck_csrf (readable) accompany this body. */
        LoginResult: {
            csrf_token: string;
            /** Format: date-time */
            expires_at: string;
            /** @description Seconds */
            expires_in: number;
            /** @description True when the password still equals the .env seed and AUTH_REQUIRE_PASSWORD_CHANGE is on */
            must_change_password?: boolean;
            /** Format: uuid */
            session_id: string;
            user: components["schemas"]["AdminUser"];
        };
        LogPage: {
            has_more: boolean;
            items: components["schemas"]["LogChunk"][];
            /** @description Opaque; pass as cursor to continue backwards in time */
            next_cursor?: string | null;
        };
        Model: {
            aliases?: string[];
            allow_pickle: boolean;
            /** Format: date-time */
            created_at: string;
            default_context_size?: number | null;
            default_launch_flags?: {
                [key: string]: unknown;
            };
            default_version?: components["schemas"]["ModelVersion"] | null;
            deployment_count?: number;
            description?: string | null;
            display_name?: string | null;
            family?: string | null;
            /** Format: uuid */
            id: string;
            metadata?: {
                [key: string]: unknown;
            };
            name: string;
            priority: components["schemas"]["model_priority"];
            repo_id: string;
            source: components["schemas"]["model_source"];
            tags?: string[];
            tool_parser_default?: string | null;
            trust_remote_code: boolean;
            type: components["schemas"]["model_type"];
            /** Format: date-time */
            updated_at?: string;
            version_count?: number;
        };
        /** @enum {string} */
        model_file_format: "gguf" | "safetensors" | "mlx" | "pickle" | "other";
        /** @enum {string} */
        model_priority: "always_on" | "normal" | "evictable";
        /** @enum {string} */
        model_source: "huggingface" | "modelscope" | "local_path";
        /** @enum {string} */
        model_type: "chat" | "thinking" | "vision" | "embedding" | "rerank" | "stt" | "tts" | "image" | "video";
        /** @enum {string} */
        model_version_status: "registered" | "verifying" | "ready" | "missing" | "blocked";
        ModelCreate: {
            aliases?: string[];
            /** @default false */
            allow_pickle?: boolean;
            default_context_size?: number | null;
            default_launch_flags?: {
                [key: string]: unknown;
            };
            description?: string | null;
            display_name?: string | null;
            family?: string | null;
            /** @description Create the first version in the same call (default: version 'main' with all files) */
            initial_version?: components["schemas"]["ModelVersionCreate"] | null;
            metadata?: {
                [key: string]: unknown;
            };
            /** @description Serving name used in API requests */
            name: string;
            priority?: components["schemas"]["model_priority"];
            /** @description org/name or absolute local path */
            repo_id: string;
            source: components["schemas"]["model_source"];
            tags?: string[];
            tool_parser_default?: string | null;
            /** @default false */
            trust_remote_code?: boolean;
            type: components["schemas"]["model_type"];
        };
        ModelFile: {
            /** @description Count of nodes with state=ready */
            cached_on_nodes?: number;
            format: components["schemas"]["model_file_format"];
            /** Format: uuid */
            id: string;
            is_required: boolean;
            /** Format: uuid */
            model_version_id: string;
            path: string;
            sha256?: string | null;
            shard_count?: number | null;
            shard_index?: number | null;
            size_bytes: number;
        };
        /** @description Partial update; every field optional. */
        ModelUpdate: {
            aliases?: string[];
            /** @default false */
            allow_pickle?: boolean;
            default_context_size?: number | null;
            default_launch_flags?: {
                [key: string]: unknown;
            };
            description?: string | null;
            display_name?: string | null;
            family?: string | null;
            metadata?: {
                [key: string]: unknown;
            };
            /** @description Serving name used in API requests */
            name?: string;
            priority?: components["schemas"]["model_priority"];
            tags?: string[];
            tool_parser_default?: string | null;
            /** @default false */
            trust_remote_code?: boolean;
            type?: components["schemas"]["model_type"];
        };
        ModelVersion: {
            config?: {
                [key: string]: unknown;
            };
            /** Format: date-time */
            created_at: string;
            file_count?: number;
            format: components["schemas"]["model_file_format"];
            /** Format: uuid */
            id: string;
            is_default: boolean;
            /** Format: uuid */
            model_id: string;
            parameter_count?: number | null;
            quantization?: string | null;
            revision?: string | null;
            status: components["schemas"]["model_version_status"];
            status_message?: string | null;
            total_size_bytes: number;
            /** Format: date-time */
            updated_at?: string;
            version: string;
        };
        ModelVersionCreate: {
            /** @description Restrict to these repo-relative paths (e.g. one GGUF quant); empty = all files */
            files?: string[];
            /** @default false */
            is_default?: boolean;
            /** @description For source=local_path: directory or file */
            local_path?: string | null;
            quantization?: string | null;
            /** @description HF commit sha / ModelScope revision; null = resolve latest */
            revision?: string | null;
            version: string;
        };
        Node: {
            /**
             * @description What the node's GPUs really do, from live samples (external = models running outside GPU Deck)
             * @enum {string|null}
             */
            activity?: "detached" | "stale" | "gpu_deck" | "mixed" | "external" | "idle" | null;
            agent_version?: string | null;
            arch?: components["schemas"]["node_arch"] | null;
            cache_dir?: string | null;
            cache_limit_bytes?: number | null;
            cache_used_bytes?: number | null;
            capabilities?: {
                [key: string]: unknown;
            };
            cert_fingerprint?: string | null;
            cloud_instance_id?: string | null;
            cloud_instance_type?: string | null;
            /** Format: uuid */
            cluster_id: string;
            /** Format: date-time */
            connected_at?: string | null;
            cpu_cores?: number | null;
            /** Format: date-time */
            created_at: string;
            /** @description Agent gone (offline / removed, or heartbeat stale); shown red */
            detached?: boolean;
            device_count?: number;
            /** Format: date-time */
            disconnected_at?: string | null;
            disks?: components["schemas"]["NodeDisk"][];
            drain_reason?: string | null;
            /** Format: date-time */
            drain_requested_at?: string | null;
            /** Format: date-time */
            drained_at?: string | null;
            hostname?: string | null;
            /** Format: uuid */
            id: string;
            instance_count?: number;
            ip_address?: string | null;
            kernel_version?: string | null;
            kubernetes_node_name?: string | null;
            labels: {
                [key: string]: string;
            };
            /** Format: date-time */
            last_heartbeat_at?: string | null;
            /** Format: date-time */
            last_inventory_at?: string | null;
            memory_total_bytes?: number | null;
            metadata?: {
                [key: string]: unknown;
            };
            name: string;
            os?: components["schemas"]["node_os"] | null;
            os_version?: string | null;
            /** @description GPU processes not run by GPU Deck */
            outside_processes?: number;
            /** Format: uuid */
            price_plan_id?: string | null;
            protocol_version?: number | null;
            proxy_address?: string | null;
            proxy_port: number;
            status: components["schemas"]["node_status"];
            status_message?: string | null;
            /** Format: date-time */
            updated_at?: string;
        };
        /** @enum {string} */
        node_arch: "x86_64" | "aarch64";
        /** @enum {string} */
        node_os: "linux" | "macos";
        /**
         * @description Connectivity plus scheduling. draining / drained imply a connected agent; a disconnected node is offline even while drained (the drain stays in drain_requested_at / drained_at and comes back on reconnect).
         * @enum {string}
         */
        node_status: "pending" | "online" | "offline" | "unhealthy" | "draining" | "drained" | "removed";
        NodeCertificate: {
            fingerprint_sha256: string;
            /** Format: uuid */
            id: string;
            is_active: boolean;
            /** Format: date-time */
            issued_at?: string;
            kind: components["schemas"]["certificate_kind"];
            /** Format: uuid */
            node_id?: string | null;
            /** Format: date-time */
            not_after: string;
            /** Format: date-time */
            not_before: string;
            revocation_reason?: string | null;
            /** Format: date-time */
            revoked_at?: string | null;
            serial: string;
            subject: string;
        };
        NodeDisk: {
            device?: string | null;
            free_bytes: number;
            fs?: string | null;
            is_cache_dir?: boolean;
            mount: string;
            total_bytes: number;
        };
        NodeFileCommandResult: {
            command: string;
            /** Format: uuid */
            command_id: string;
            error_code?: string | null;
            error_message?: string | null;
            /** Format: uuid */
            node_id: string;
            progress_pct?: number | null;
            /** @description agent-protocol ScanPathResult / HashFilesResult when succeeded */
            result?: Record<string, unknown> | null;
            state: components["schemas"]["agent_command_state"];
        };
        NodeHashRequest: {
            paths: string[];
            /** @description paths are relative to root (must stay inside it) */
            root?: string | null;
            /** @default 30 */
            wait_seconds?: number;
        };
        NodeScanRequest: {
            /** @default 4 */
            max_depth?: number;
            /** @default 20000 */
            max_entries?: number;
            /** @description Absolute path or ~/... (expanded on the node) */
            path: string;
            /** @default true */
            read_metadata?: boolean;
            /** @default true */
            recursive?: boolean;
            /** @default 30 */
            wait_seconds?: number;
        };
        NodeUpdate: {
            cache_limit_bytes?: number | null;
            /** @description Replaces all labels */
            labels?: {
                [key: string]: string;
            };
            metadata?: {
                [key: string]: unknown;
            };
            name?: string;
            /** Format: uuid */
            price_plan_id?: string | null;
            proxy_address?: string | null;
            proxy_port?: number;
        };
        Overview: {
            cost_month_usd?: number;
            cost_today_usd?: number;
            counts: components["schemas"]["OverviewCounts"];
            live: components["schemas"]["OverviewLive"];
            recent_alerts?: components["schemas"]["AlertEvent"][];
            recent_rule_runs?: components["schemas"]["RuleRun"][];
            recent_swaps?: components["schemas"]["SwapOperation"][];
        };
        OverviewCounts: {
            alerts_open?: number;
            api_keys_active?: number;
            clusters?: number;
            deployments?: number;
            deployments_running?: number;
            devices?: number;
            devices_unhealthy?: number;
            instances_ready?: number;
            models?: number;
            nodes?: number;
            /** @description Connected nodes (online, draining or drained) */
            nodes_online?: number;
            rules_enabled?: number;
            swaps_in_progress?: number;
        };
        OverviewLive: {
            active_sessions?: number;
            error_rate_pct?: number | null;
            generation_tps?: number | null;
            gpu_utilization_pct_avg?: number | null;
            power_watts?: number | null;
            requests_per_minute?: number | null;
            /** Format: date-time */
            ts: string;
            ttft_ms_p95?: number | null;
            vram_total_bytes?: number;
            vram_used_bytes?: number;
            waiting_sessions?: number;
        };
        /** @enum {string} */
        placement_strategy: "spread" | "pack";
        PlacementCandidate: {
            device_ids: string[];
            device_names?: string[];
            /** Format: uuid */
            engine_version_id: string;
            /** @description Bytes the instance takes per device — the slice, or the model estimate, or what the engine pre-allocates when larger (vLLM gpu-memory-utilization / SGLang mem-fraction-static x device VRAM, TensorRT-LLM kv_cache_free_gpu_memory_fraction of the memory weights leave); reasons explain it */
            estimated_vram_bytes?: number;
            free_vram_bytes_after?: number;
            /** Format: uuid */
            node_id: string;
            node_name?: string;
            reasons?: string[];
            score: number;
            slice?: {
                compute_pct?: number;
                vram_bytes?: number;
            } | null;
        };
        PlacementPreview: {
            compat?: components["schemas"]["CompatCheck"] | null;
            feasible: boolean;
            /** @description One per replica */
            placements: components["schemas"]["PlacementCandidate"][];
            rejected?: {
                device_ids?: string[];
                /** Format: uuid */
                node_id?: string;
                reason?: string;
            }[];
            /** @description Instances make_room_by_priority would unload */
            would_evict?: components["schemas"]["Instance"][];
        };
        PlacementPreviewRequest: {
            deployment?: components["schemas"]["DeploymentSpec"];
            /**
             * Format: uuid
             * @description Preview a scale/move of an existing deployment instead
             */
            deployment_id?: string | null;
            replicas?: number | null;
        };
        PrefetchRequest: {
            /** Format: uuid */
            model_version_id: string;
            /** @description Target nodes (at least one) */
            node_ids: string[];
            /** @default false */
            pin?: boolean;
            pin_reason?: string | null;
        };
        PricePlan: {
            /** Format: date-time */
            created_at?: string;
            currency: string;
            description?: string | null;
            /** Format: uuid */
            id: string;
            is_default: boolean;
            name: string;
            /** Format: date-time */
            updated_at?: string;
            usd_per_1k_cached_tokens: number;
            usd_per_1k_input_tokens: number;
            usd_per_1k_output_tokens: number;
            usd_per_gpu_minute: number;
        };
        PricePlanCreate: {
            description?: string | null;
            /** @default false */
            is_default?: boolean;
            name: string;
            /** @default 0 */
            usd_per_1k_cached_tokens?: number;
            /** @default 0 */
            usd_per_1k_input_tokens?: number;
            /** @default 0 */
            usd_per_1k_output_tokens?: number;
            /** @default 0 */
            usd_per_gpu_minute?: number;
        };
        /** @description Partial update; every field optional. */
        PricePlanUpdate: {
            description?: string | null;
            /** @default false */
            is_default?: boolean;
            name?: string;
            /** @default 0 */
            usd_per_1k_cached_tokens?: number;
            /** @default 0 */
            usd_per_1k_input_tokens?: number;
            /** @default 0 */
            usd_per_1k_output_tokens?: number;
            /** @default 0 */
            usd_per_gpu_minute?: number;
        };
        RevokeRequest: {
            reason?: string | null;
        };
        Rule: components["schemas"]["RuleCreate"] & {
            /** Format: date-time */
            created_at: string;
            fire_count?: number;
            /** Format: uuid */
            id: string;
            /** Format: date-time */
            last_evaluated_at?: string | null;
            /** Format: date-time */
            last_fired_at?: string | null;
            last_run?: components["schemas"]["RuleRunSummary"] | null;
            /**
             * Format: date-time
             * @description When the jobs leader next acts on the rule. time_schedule = next cron/at fire time in the rule's zone; other types = last_evaluated_at + evaluation_interval_seconds (now when overdue or never evaluated); null while disabled.
             */
            readonly next_evaluation_at?: string | null;
            /** Format: date-time */
            updated_at?: string;
        };
        /** @enum {string} */
        rule_run_outcome: "no_match" | "cooldown" | "fired" | "dry_run" | "skipped" | "error";
        /** @enum {string} */
        rule_type: "idle_unload" | "time_schedule" | "low_traffic_shrink" | "high_load_grow" | "make_room_by_priority" | "auto_heal";
        RuleCreate: {
            /** @default 300 */
            cooldown_seconds: number;
            description?: string | null;
            /** @default false */
            dry_run: boolean;
            /** @default true */
            enabled: boolean;
            /** @default 30 */
            evaluation_interval_seconds: number;
            name: string;
            /** @description Validated against libs/contracts/rules/<type>.schema.json */
            params: {
                [key: string]: unknown;
            };
            /** @default 100 */
            priority: number;
            /** @description libs/contracts/rules/target-selector.schema.json */
            target?: {
                [key: string]: unknown;
            };
            type: components["schemas"]["rule_type"];
        };
        RuleRun: {
            actions: components["schemas"]["RuleRunAction"][];
            /** Format: date-time */
            created_at?: string;
            /** @description Observed values and reason */
            decision: {
                [key: string]: unknown;
            };
            dry_run: boolean;
            duration_ms?: number | null;
            error_message?: string | null;
            /** Format: date-time */
            evaluated_at: string;
            fired: boolean;
            /** Format: uuid */
            id: string;
            outcome: components["schemas"]["rule_run_outcome"];
            /** Format: uuid */
            rule_id: string;
            rule_name?: string;
            rule_type: components["schemas"]["rule_type"];
        };
        RuleRunAction: {
            /** @description unload | load | scale_up | scale_down | move | restart | apply_layout | prefetch | evict */
            action: string;
            message?: string | null;
            status?: string;
            /** Format: uuid */
            swap_operation_id?: string | null;
            /** Format: uuid */
            target_id?: string | null;
            target_name?: string | null;
            target_type: string;
        };
        RuleRunSummary: {
            dry_run: boolean;
            /** Format: date-time */
            evaluated_at: string;
            fired: boolean;
            /** Format: uuid */
            id: string;
            outcome: components["schemas"]["rule_run_outcome"];
        };
        RuleSchemas: {
            layout: {
                [key: string]: unknown;
            };
            /** @description rule_type -> JSON Schema */
            params: {
                [key: string]: {
                    [key: string]: unknown;
                };
            };
            rule: {
                [key: string]: unknown;
            };
            target_selector: {
                [key: string]: unknown;
            };
        };
        /** @description Partial update; every field optional. */
        RuleUpdate: {
            /** @default 300 */
            cooldown_seconds?: number;
            description?: string | null;
            /** @default false */
            dry_run?: boolean;
            /** @default true */
            enabled?: boolean;
            /** @default 30 */
            evaluation_interval_seconds?: number;
            name?: string;
            /** @description Validated against libs/contracts/rules/<type>.schema.json */
            params?: {
                [key: string]: unknown;
            };
            /** @default 100 */
            priority?: number;
            /** @description libs/contracts/rules/target-selector.schema.json */
            target?: {
                [key: string]: unknown;
            };
        };
        ScaleRequest: {
            /** @default 120 */
            drain_timeout_seconds?: number;
            placement_strategy?: components["schemas"]["placement_strategy"] | null;
            replicas: number;
        };
        ServiceCertificateIssued: {
            ca_bundle_pem: string;
            certificate: components["schemas"]["NodeCertificate"];
            certificate_pem: string;
            /** @description Only when the server generated the key; shown once */
            private_key_pem?: string | null;
        };
        ServiceCertificateRequest: {
            /** @description Default per kind */
            common_name?: string | null;
            /** @description Bring your own key: only the cert is returned */
            csr_pem?: string | null;
            /** @enum {string} */
            kind: "api_layer" | "control_plane" | "agent_hub";
            /** @description DNS names / IPs for agent_hub server certs */
            sans?: string[];
            /** @default 365 */
            validity_days?: number;
        };
        ServiceHealth: {
            error?: string | null;
            response_time_ms?: number | null;
            /** @enum {string} */
            status: "healthy" | "degraded" | "unhealthy";
        };
        Session: {
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            expires_at: string;
            /** Format: uuid */
            id: string;
            ip_address?: string | null;
            is_current: boolean;
            /** Format: date-time */
            last_active_at?: string | null;
            user_agent?: string | null;
        };
        SessionSample: {
            cached_tokens?: number | null;
            context_size?: number | null;
            elapsed_ms?: number | null;
            extra?: {
                [key: string]: unknown;
            } | null;
            generated_tokens?: number | null;
            /** Format: uuid */
            instance_id: string;
            kv_cache_bytes?: number | null;
            kv_cache_tokens?: number | null;
            prompt_tokens?: number | null;
            /** Format: uuid */
            request_id?: string | null;
            session_key: string;
            slot_id?: number | null;
            /** @enum {string} */
            state: "idle" | "waiting" | "processing" | "generating";
            tokens_per_second?: number | null;
            /** Format: date-time */
            ts: string;
            ttft_ms?: number | null;
        };
        SessionsSnapshot: {
            active: number;
            /** Format: uuid */
            instance_id: string;
            sessions: components["schemas"]["SessionSample"][];
            slots_total?: number | null;
            /** Format: date-time */
            ts: string;
            waiting: number;
        };
        /** @enum {string} */
        swap_kind: "load" | "unload" | "swap" | "scale_up" | "scale_down" | "restart" | "move" | "apply_layout";
        /** @enum {string} */
        swap_state: "pending" | "draining" | "unloading" | "loading" | "health_check" | "completed" | "failed" | "rolling_back" | "rolled_back" | "cancelled";
        /** @enum {string} */
        swap_trigger: "manual" | "rule" | "layout" | "auto_heal" | "api" | "benchmark";
        SwapCreate: {
            /** Format: uuid */
            deployment_id: string;
            device_ids?: string[];
            /** @default 120 */
            drain_timeout_seconds?: number;
            /** @default false */
            dry_run?: boolean;
            /** @default false */
            force?: boolean;
            /**
             * Format: uuid
             * @description For unload/move/restart
             */
            instance_id?: string | null;
            /** @enum {string} */
            kind: "load" | "unload" | "move" | "restart";
            /**
             * Format: uuid
             * @description Target node for load/move (null = scheduler)
             */
            node_id?: string | null;
        };
        SwapOperation: {
            /** Format: date-time */
            created_at: string;
            current_step: number;
            /** Format: uuid */
            deployment_id?: string | null;
            deployment_name?: string | null;
            drain_timeout_seconds?: number;
            dry_run?: boolean;
            error_message?: string | null;
            /** Format: date-time */
            finished_at?: string | null;
            /** Format: uuid */
            from_instance_id?: string | null;
            /** Format: uuid */
            id: string;
            kind: components["schemas"]["swap_kind"];
            /** Format: uuid */
            layout_id?: string | null;
            /** Format: uuid */
            node_id?: string | null;
            plan?: {
                [key: string]: unknown;
            };
            rollback?: {
                [key: string]: unknown;
            };
            rule_name?: string | null;
            /** Format: uuid */
            rule_run_id?: string | null;
            /** Format: date-time */
            started_at?: string | null;
            state: components["schemas"]["swap_state"];
            steps: components["schemas"]["SwapStep"][];
            /** Format: uuid */
            to_instance_id?: string | null;
            trigger: components["schemas"]["swap_trigger"];
            /** Format: date-time */
            updated_at?: string;
        };
        SwapStep: {
            /** Format: uuid */
            command_id?: string | null;
            details?: {
                [key: string]: unknown;
            } | null;
            error?: string | null;
            /** Format: date-time */
            finished_at?: string | null;
            /** @description plan | route_off | drain | unload | load | health | route_on | rollback */
            name: string;
            /** Format: date-time */
            started_at?: string | null;
            /** @enum {string} */
            state: "pending" | "running" | "completed" | "failed" | "skipped";
        };
        SystemInfo: {
            agent_hub_url?: string;
            api_layer_url?: string;
            app_name: string;
            build_sha?: string | null;
            environment: string;
            limits?: {
                [key: string]: unknown;
            };
            protocol_version: number;
            retention?: {
                [key: string]: number;
            };
            serving?: {
                /** @description Full Anthropic mount prefix, e.g. /anthropic/v1 */
                anthropic_prefix?: string;
                /** @description Full Ollama mount prefix, e.g. /ollama/api (bare /api also served) */
                ollama_prefix?: string;
                /** @description Full OpenAI mount prefix, e.g. /openai/v1 (bare /v1 also served) */
                openai_prefix?: string;
            };
            version: string;
        };
        TimeSeries: {
            /** @description e.g. {device_id, node_id, metric} */
            labels: {
                [key: string]: string;
            };
            /** @description Canonical metric name from metrics.md */
            metric: string;
            /** @description [ts, value] pairs; null = no data */
            points: [
                string,
                number | null
            ][];
            unit: string;
        };
        TimeSeriesResponse: {
            /** Format: date-time */
            from: string;
            series: components["schemas"]["TimeSeries"][];
            step: string;
            /** Format: date-time */
            to: string;
        };
        Topology: {
            clusters: components["schemas"]["TopologyCluster"][];
            /** Format: date-time */
            generated_at: string;
            /** @description Cross-instance relations for multi-GPU/multi-node deployments */
            links?: {
                /** Format: uuid */
                from_instance_id?: string;
                /** @enum {string} */
                kind?: "tensor_parallel" | "pipeline_parallel" | "pd_transfer" | "kv_share";
                /** Format: uuid */
                to_instance_id?: string;
            }[];
        };
        TopologyCluster: {
            cluster: components["schemas"]["Cluster"];
            nodes: components["schemas"]["TopologyNode"][];
        };
        TopologyDevice: {
            device: components["schemas"]["Device"];
            instances: components["schemas"]["TopologyInstance"][];
        };
        TopologyInstance: {
            active_sessions?: number | null;
            /** Format: uuid */
            deployment_id: string;
            deployment_name?: string;
            device_ids: string[];
            engine?: components["schemas"]["engine_name"];
            health: components["schemas"]["instance_health"];
            /** Format: uuid */
            id: string;
            model_name?: string;
            origin?: components["schemas"]["deployment_origin"];
            state: components["schemas"]["instance_state"];
            traffic_weight?: number;
            waiting_sessions?: number | null;
        };
        TopologyNode: {
            devices: components["schemas"]["TopologyDevice"][];
            node: components["schemas"]["Node"];
        };
        Trace: {
            duration_ms?: number | null;
            /** @description Deep link into the external tracing UI when OTEL_UI_URL is set */
            otlp_url?: string | null;
            root?: components["schemas"]["TraceSpan"] | null;
            spans: components["schemas"]["TraceSpan"][];
            trace_id: string;
        };
        TraceSpan: {
            /** Format: uuid */
            api_key_id?: string | null;
            attributes?: {
                [key: string]: unknown;
            } | null;
            /** Format: uuid */
            deployment_id?: string | null;
            duration_ms?: number | null;
            endpoint?: string | null;
            error: boolean;
            format?: components["schemas"]["usage_format"] | null;
            /** Format: uuid */
            instance_id?: string | null;
            /** Format: uuid */
            model_id?: string | null;
            name: string;
            /** Format: uuid */
            node_id?: string | null;
            parent_span_id?: string | null;
            /** Format: uuid */
            request_id?: string | null;
            service: string;
            span_id: string;
            status_code?: number | null;
            trace_id: string;
            /** Format: date-time */
            ts: string;
        };
        TrafficWeightsRequest: {
            /** @enum {string|null} */
            strategy?: "round_robin" | "least_sessions" | "weighted" | null;
            /** @description instance_id -> weight (0 = drained from LB) */
            weights: {
                [key: string]: number;
            };
        };
        /** @enum {string} */
        usage_format: "openai" | "anthropic" | "ollama";
        /** @enum {string} */
        usage_status: "success" | "error" | "timeout" | "cancelled";
        UsageEvent: {
            /** Format: uuid */
            api_key_id?: string | null;
            api_key_name?: string | null;
            cached_tokens: number;
            /** Format: uuid */
            cluster_id?: string | null;
            cost_usd: number;
            /** Format: uuid */
            deployment_id?: string | null;
            endpoint: string;
            error_code?: string | null;
            format: components["schemas"]["usage_format"];
            gpu_count?: number;
            gpu_seconds: number;
            http_status?: number | null;
            id: number;
            input_tokens: number;
            /** Format: uuid */
            instance_id?: string | null;
            ip_address?: string | null;
            latency_ms?: number | null;
            media_units?: number;
            metadata?: {
                [key: string]: unknown;
            } | null;
            /** Format: uuid */
            model_id?: string | null;
            model_name: string;
            /** Format: uuid */
            node_id?: string | null;
            node_name?: string | null;
            output_tokens: number;
            /** Format: uuid */
            price_plan_id?: string | null;
            reasoning_tokens?: number;
            request_count?: number;
            /** Format: uuid */
            request_id: string;
            status: components["schemas"]["usage_status"];
            stream?: boolean;
            /** Format: date-time */
            ts: string;
            ttft_ms?: number | null;
        };
        UsageSummary: {
            /** Format: date-time */
            from: string;
            group_by: string[];
            rows: components["schemas"]["UsageSummaryRow"][];
            /** Format: date-time */
            to: string;
            totals: components["schemas"]["UsageTotals"];
        };
        UsageSummaryRow: {
            /** @description group_by key -> id */
            group: {
                [key: string]: string;
            };
            /** @description group_by key -> display name */
            labels: {
                [key: string]: string;
            };
            totals: components["schemas"]["UsageTotals"];
        };
        UsageTotals: {
            cached_tokens: number;
            cost_usd: number;
            error_count?: number;
            gpu_minutes?: number;
            gpu_seconds: number;
            input_tokens: number;
            latency_ms_avg?: number | null;
            media_units?: number;
            output_tokens: number;
            reasoning_tokens?: number;
            request_count: number;
            success_count?: number;
            ttft_ms_avg?: number | null;
        };
        VaultMetadata: {
            categories?: components["schemas"]["CredentialCategory"][];
            defaults?: components["schemas"]["CredentialDefault"][];
            kinds?: components["schemas"]["CredentialKindInfo"][];
            /** @description Consumer -> accepted kinds (cluster.cloud_aws, alert_channel.email, model.huggingface, gpu_instance.authorized_keys, ...) */
            pickers?: {
                [key: string]: string[];
            };
            providers?: string[];
            statuses?: string[];
            types?: string[];
        };
        VerifyResult: {
            files_checked: number;
            mismatches?: components["schemas"]["ModelFile"][];
            /** Format: uuid */
            model_version_id: string;
            pickle_blocked?: boolean;
            status: components["schemas"]["model_version_status"];
        };
    };
    responses: {
        /** @description Bad request */
        Error400: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Not authenticated / session expired */
        Error401: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Forbidden (CSRF failed, IP blocked) */
        Error403: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Resource not found */
        Error404: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Conflict (duplicate name, state conflict such as swap in progress, or EXTERNAL_DEPLOYMENT when the target is an external deployment or instance GPU Deck only observes) */
        Error409: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Validation error */
        Error422: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Rate limited */
        Error429: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Internal error */
        Error500: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Upstream (HF/ModelScope/agent) error */
        Error502: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
        /** @description Dependency unavailable (DB/Redis) — Retry-After header set */
        Error503: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorEnvelope"];
            };
        };
    };
    parameters: {
        /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
        CsrfHeader: string;
        /** @description Range start (inclusive, RFC 3339). Default now-1h */
        from: string;
        /** @description 1-based page */
        page: number;
        /** @description Items per page (max 500) */
        page_size: number;
        /** @description Case-insensitive substring/trigram search on name */
        search: string;
        /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
        sort: string;
        /** @description Bucket size: 2s (raw, only within METRICS_RAW_RETENTION_HOURS), 1m, 5m, 15m, 1h, 1d */
        step: string;
        /** @description Range end (exclusive). Default now */
        to: string;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
};
export type $defs = Record<string, never>;
export interface operations {
    listAgentDownloads: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AgentBuild"][];
                        error: null;
                        meta: {
                            /** @description Origin GPU machines download from (the agent hub by default) */
                            base_url?: string;
                            latest_version?: string | null;
                            /** @description AGENT_RELEASE_URL (null = release fetch disabled) */
                            release_url?: string | null;
                        };
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    downloadAgent: {
        parameters: {
            query?: {
                /** @description Join token (alternative to the header) */
                token?: string;
            };
            header?: {
                /** @description Usable join token (gdj_...); or ?token= */
                "X-GPUDeck-Join-Token"?: string;
            };
            path: {
                /** @description Rust target triple, e.g. aarch64-apple-darwin, x86_64-unknown-linux-gnu */
                target: string;
                /** @description Semver directory or `latest` */
                version: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Binary (or archive) */
            200: {
                headers: {
                    /** @description sha256 of the body */
                    "X-Checksum-Sha256"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/octet-stream": string;
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    downloadAgentSha256: {
        parameters: {
            query?: {
                /** @description Join token (alternative to the header) */
                token?: string;
            };
            header?: {
                /** @description Usable join token (gdj_...); or ?token= */
                "X-GPUDeck-Join-Token"?: string;
            };
            path: {
                /** @description Rust target triple */
                target: string;
                /** @description Semver directory or `latest` */
                version: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description `<sha256>  <filename>` */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/plain": string;
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    agentInstallScript: {
        parameters: {
            query?: {
                /** @description Join token (alternative to the header) */
                token?: string;
                /** @description Build version (default latest) */
                version?: string;
            };
            header?: {
                /** @description Usable join token (gdj_...); or ?token= */
                "X-GPUDeck-Join-Token"?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description POSIX sh: detects OS/arch, downloads + verifies the build, installs /usr/local/bin/gpudeck-agent, joins (hub URL, CA fingerprint, name hint from the token) and installs the systemd unit gpudeck-agent.service or the LaunchAgent io.github.sur950.gpudeck-agent */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/x-shellscript": string;
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listAlertChannels: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                type?: components["schemas"]["alert_channel_type"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertChannel"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createAlertChannel: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AlertChannelCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertChannel"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getAlertChannel: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                channel_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertChannel"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteAlertChannel: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                channel_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateAlertChannel: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                channel_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AlertChannelUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertChannel"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    testAlertChannel: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                channel_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertTestResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listAlertEvents: {
        parameters: {
            query?: {
                /** @description Prefix match */
                event_type?: string;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                resource_id?: string;
                /** @description Filter */
                resource_type?: string;
                /** @description Filter */
                severity?: components["schemas"]["alert_severity"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                status?: components["schemas"]["alert_event_status"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertEvent"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getAlertEvent: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                event_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertEvent"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    acknowledgeAlertEvent: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                event_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertEvent"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    resendAlertEvent: {
        parameters: {
            query?: {
                /** @description Only this channel */
                channel_id?: string;
            };
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                event_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertEvent"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    resolveAlertEvent: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                event_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AlertEvent"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listApiKeys: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                status?: components["schemas"]["api_key_status"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKey"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createApiKey: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApiKeyCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKeyCreated"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getApiKey: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKey"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteApiKey: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateApiKey: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                key_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApiKeyUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKey"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    activateApiKey: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKey"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    revokeApiKey: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKey"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    rotateApiKey: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKeyCreated"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    suspendApiKey: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKey"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getApiKeyStats: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ApiKeyStats"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listAuditLogs: {
        parameters: {
            query?: {
                /** @description Filter */
                actor_id?: string;
                /** @description Filter */
                actor_type?: components["schemas"]["audit_actor_type"];
                /** @description Exact (`rule.created`), category (`rule` = `rule` or `rule.*`), prefix (`rule.`) or glob (`rule.*`, `*.failed`) match; comma-separated values are OR-ed */
                event_type?: string;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                outcome?: components["schemas"]["audit_outcome"];
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                request_id?: string;
                /** @description Filter */
                resource_id?: string;
                /** @description Filter */
                resource_type?: string;
                /** @description Substring on message/resource_name */
                search?: string;
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AuditLog"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getAuditLog: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                log_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AuditLog"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    exportAuditCsv: {
        parameters: {
            query?: {
                /** @description Filter */
                actor_type?: components["schemas"]["audit_actor_type"];
                /** @description Same matching as `GET /audit` `event_type` */
                event_type?: string;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                resource_type?: string;
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description CSV stream (Content-Disposition: attachment) */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/csv": string;
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    changePassword: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChangePasswordRequest"];
            };
        };
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getCsrfToken: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CsrfToken"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    login: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["LoginResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    logout: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    logoutAll: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getMe: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AdminUser"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listSessions: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Session"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    revokeSession: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listBatches: {
        parameters: {
            query?: {
                /** @description Filter */
                api_key_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                status?: components["schemas"]["batch_status"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Batch"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getBatch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                batch_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Batch"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    cancelBatch: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                batch_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Batch"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getBenchmarkRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BenchmarkRun"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    applyBenchmarkRecommendation: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ApplyRecommendationRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Deployment"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    cancelBenchmarkRun: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BenchmarkRun"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    exportBenchmarkRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description CSV attachment */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/csv": string;
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listBenchmarks: {
        parameters: {
            query?: {
                /** @description Filter */
                deployment_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Benchmark"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createBenchmark: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BenchmarkCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Benchmark"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getBenchmark: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                benchmark_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Benchmark"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteBenchmark: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                benchmark_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateBenchmark: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                benchmark_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BenchmarkUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Benchmark"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    autoTuneBenchmark: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                benchmark_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["BenchmarkStartRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BenchmarkRun"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listBenchmarkRuns: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
            };
            header?: never;
            path: {
                /** @description Resource id */
                benchmark_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BenchmarkRun"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    startBenchmark: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                benchmark_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["BenchmarkStartRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BenchmarkRun"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getBillingEstimate: {
        parameters: {
            query?: {
                /** @description Scope */
                cluster_id?: string;
                /** @description Default: first of next month */
                period_end?: string;
                /** @description Default: first of the month UTC */
                period_start?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BillingEstimate"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listBillingReports: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                status?: components["schemas"]["billing_report_status"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BillingReport"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createBillingReport: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BillingReportCreate"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BillingReport"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getBillingReport: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                report_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BillingReport"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteBillingReport: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                report_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    exportBillingReportCsv: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                report_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description CSV attachment */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/csv": string;
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    regenerateBillingReport: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                report_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["BillingReport"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getBranding: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Branding"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            500: components["responses"]["Error500"];
        };
    };
    scanLocalPath: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LocalScanRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["LocalScanCandidate"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listRemoteFiles: {
        parameters: {
            query: {
                /** @description Vault token credential for gated / private repos; default token otherwise */
                credential_id?: string;
                /** @description org/name */
                repo_id: string;
                /** @description Revision (default main) */
                revision?: string;
                /** @description Source */
                source: "huggingface" | "modelscope";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CatalogRemoteFiles"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            404: components["responses"]["Error404"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    searchCatalog: {
        parameters: {
            query: {
                /** @description Filter */
                author?: string;
                /** @description Vault token credential (huggingface / modelscope kind) to search with; default token otherwise */
                credential_id?: string;
                /** @description Filter by weight format */
                format?: components["schemas"]["model_file_format"];
                /** @description Max results */
                limit?: number;
                /** @description Query */
                q: string;
                /** @description Sort */
                sort?: "downloads" | "likes" | "last_modified" | "relevance";
                /** @description Source */
                source: "huggingface" | "modelscope";
                /** @description Filter by detected type */
                type?: components["schemas"]["model_type"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CatalogSearchResult"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    listCloudNodes: {
        parameters: {
            query?: {
                /** @description Filter */
                cluster_id?: string;
                /** @description Include terminated VMs */
                include_terminated?: boolean;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                provider?: components["schemas"]["cloud_provider"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter (repeatable) */
                status?: components["schemas"]["cloud_node_status"][];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    createCloudNodes: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CloudNodeCreate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudProvisionPreview"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            /** @description Created (one row per VM) */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"][];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    getCloudNode: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description cloud_instances.id */
                cloud_node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    deleteCloudNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description cloud_instances.id */
                cloud_node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Deleted */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    updateCloudNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description cloud_instances.id */
                cloud_node_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CloudNodeUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    applyCloudNodePricePlan: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description cloud_instances.id */
                cloud_node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    refreshCloudNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description cloud_instances.id */
                cloud_node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    startCloudNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description cloud_instances.id */
                cloud_node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    stopCloudNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description cloud_instances.id */
                cloud_node_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["CloudNodeAction"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    terminateCloudNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description cloud_instances.id */
                cloud_node_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["CloudNodeAction"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudNode"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    listCloudProviders: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudProvider"][];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    checkCloudCredential: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Cloud provider */
                provider: components["schemas"]["cloud_provider"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CloudCredentialCheckRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudCredentialCheck"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    listCloudInstanceTypes: {
        parameters: {
            query: {
                /** @description Use this cloud cluster's credential (and config.cloud.project for GCP) */
                cluster_id?: string;
                /** @description Vault credential of the provider account (or pass cluster_id) */
                credential_id?: string;
                /** @description Exact GPU count */
                gpu_count?: number;
                /** @description nvidia | amd | aws | ... */
                gpu_vendor?: string;
                /** @description Maximum USD/hour (types without a price are excluded) */
                max_price?: number;
                /** @description Minimum total VRAM (GiB) */
                min_vram_gb?: number;
                /** @description Bypass the CLOUD_CATALOG_CACHE_SECONDS cache */
                refresh?: boolean;
                /** @description Provider region (e.g. us-east-1, eastus, us-central1, nyc2) */
                region: string;
                /** @description Substring on id / GPU model / family */
                search?: string;
            };
            header?: never;
            path: {
                /** @description Cloud provider */
                provider: components["schemas"]["cloud_provider"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudInstanceType"][];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    listCloudQuotas: {
        parameters: {
            query: {
                /** @description Use this cloud cluster's credential (and config.cloud.project for GCP) */
                cluster_id?: string;
                /** @description Vault credential of the provider account (or pass cluster_id) */
                credential_id?: string;
                /** @description Provider region (e.g. us-east-1, eastus, us-central1, nyc2) */
                region: string;
            };
            header?: never;
            path: {
                /** @description Cloud provider */
                provider: components["schemas"]["cloud_provider"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudQuota"][];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    listCloudRegions: {
        parameters: {
            query?: {
                /** @description Use this cloud cluster's credential (and config.cloud.project for GCP) */
                cluster_id?: string;
                /** @description Vault credential of the provider account (or pass cluster_id) */
                credential_id?: string;
                /** @description Bypass the CLOUD_CATALOG_CACHE_SECONDS cache */
                refresh?: boolean;
            };
            header?: never;
            path: {
                /** @description Cloud provider */
                provider: components["schemas"]["cloud_provider"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CloudRegion"][];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            /** @description Cloud provider rejected the credential (CLOUD_AUTH_FAILED) */
            424: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            502: components["responses"]["Error502"];
            503: components["responses"]["Error503"];
        };
    };
    listClusters: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                status?: components["schemas"]["cluster_status"];
                /** @description Filter */
                type?: components["schemas"]["cluster_type"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Cluster"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createCluster: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClusterCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Cluster"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getCluster: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                cluster_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Cluster"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteCluster: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                cluster_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateCluster: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                cluster_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClusterUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Cluster"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listClusterNodes: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
            };
            header?: never;
            path: {
                /** @description Resource id */
                cluster_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Node"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listDeployments: {
        parameters: {
            query?: {
                /** @description Filter */
                cluster_id?: string;
                /** @description Filter */
                engine?: components["schemas"]["engine_name"];
                /** @description Filter */
                model_id?: string;
                /** @description managed or external (observed servers GPU Deck did not start) */
                origin?: components["schemas"]["deployment_origin"];
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Effective priority */
                priority?: components["schemas"]["model_priority"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                status?: components["schemas"]["deployment_status"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Deployment"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createDeployment: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeploymentCreate"];
            };
        };
        responses: {
            /** @description dry_run=true: placement preview, nothing created */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["PlacementPreview"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Deployment"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getDeployment: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Deployment"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteDeployment: {
        parameters: {
            query?: {
                /** @description Skip drain */
                force?: boolean;
            };
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateDeployment: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeploymentUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Deployment"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listDeploymentInstances: {
        parameters: {
            query?: {
                /** @description Include stopped */
                include_stopped?: boolean;
            };
            header?: never;
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Instance"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    restartDeployment: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["InstanceDrainRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    scaleDeployment: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScaleRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    startDeployment: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    stopDeployment: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listDeploymentSwaps: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
            };
            header?: never;
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SwapOperation"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    setTrafficWeights: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TrafficWeightsRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Deployment"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    previewPlacement: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PlacementPreviewRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["PlacementPreview"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listAllDeviceSlices: {
        parameters: {
            query?: {
                /** @description Filter */
                deployment_id?: string;
                /** @description Filter */
                node_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DeviceSlice"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listDevices: {
        parameters: {
            query?: {
                /** @description Filter */
                cluster_id?: string;
                /** @description Only devices with at least this much unreserved VRAM */
                free_vram_min_bytes?: number;
                /** @description Filter */
                health?: components["schemas"]["device_health"];
                /** @description Filter */
                node_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                vendor?: components["schemas"]["device_vendor"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Device"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getDevice: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                device_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Device"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listDeviceSlices: {
        parameters: {
            query?: {
                /** @description Include history */
                include_released?: boolean;
            };
            header?: never;
            path: {
                /** @description Resource id */
                device_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DeviceSlice"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createDeviceSlice: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                device_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeviceSliceCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DeviceSliceResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteDeviceSlice: {
        parameters: {
            query?: {
                /** @description Also stop the replica / SSH GPU instance using it */
                force?: boolean;
            };
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                device_id: string;
                /** @description Resource id */
                slice_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DeviceSliceResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateDeviceSlice: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                device_id: string;
                /** @description Resource id */
                slice_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeviceSliceUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DeviceSliceResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            /** @description Rolling update of the deployment started */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["DeviceSliceResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listEngines: {
        parameters: {
            query?: {
                /** @description Filter */
                enabled?: boolean;
                /** @description Engines supporting this type */
                model_type?: components["schemas"]["model_type"];
                /** @description Engines supporting this vendor */
                vendor?: components["schemas"]["device_vendor"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Engine"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getEngine: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                engine_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Engine"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateEngine: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                engine_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EngineUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Engine"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listEngineVersions: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                engine_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["EngineVersion"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createEngineVersion: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                engine_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EngineVersionCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["EngineVersion"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getEngineVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                engine_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["EngineVersion"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteEngineVersion: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                engine_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateEngineVersion: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                engine_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EngineVersionUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["EngineVersion"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    autoPickEngine: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AutoPickRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AutoPickResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    runCompatCheck: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CompatCheckRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CompatCheck"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listCompatChecks: {
        parameters: {
            query?: {
                /** @description Filter */
                engine_version_id?: string;
                /** @description Filter */
                model_version_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                result?: components["schemas"]["compat_result"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CompatCheck"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listGpuInstances: {
        parameters: {
            query?: {
                /** @description Filter */
                cluster_id?: string;
                /** @description Include stopped / error */
                include_stopped?: boolean;
                /** @description Filter */
                node_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Name / image substring */
                search?: string;
                /** @description Repeatable */
                state?: components["schemas"]["gpu_instance_state"][];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["GpuInstance"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createGpuInstance: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GpuInstanceCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["GpuInstance"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getGpuInstance: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["GpuInstance"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteGpuInstance: {
        parameters: {
            query?: {
                /** @description Stop a live instance first */
                force?: boolean;
            };
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Deleted */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateGpuInstance: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GpuInstanceUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["GpuInstance"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getGpuInstanceConnection: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["GpuInstanceConnection"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    startGpuInstance: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["GpuInstance"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    stopGpuInstance: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["GpuInstanceStopRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["GpuInstance"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getHealth: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Health"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            503: components["responses"]["Error503"];
        };
    };
    getLiveness: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            status?: string;
                        };
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
        };
    };
    getReadiness: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Health"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            503: components["responses"]["Error503"];
        };
    };
    listInstances: {
        parameters: {
            query?: {
                /** @description Filter */
                deployment_id?: string;
                /** @description Filter */
                health?: components["schemas"]["instance_health"];
                /** @description Include stopped */
                include_stopped?: boolean;
                /** @description Filter */
                node_id?: string;
                /** @description managed or external (observed servers GPU Deck did not start) */
                origin?: components["schemas"]["deployment_origin"];
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                state?: components["schemas"]["instance_state"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Instance"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getInstance: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Instance"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    stopInstance: {
        parameters: {
            query?: {
                /** @description Skip drain */
                force?: boolean;
            };
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateInstance: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InstanceUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Instance"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    drainInstance: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["InstanceDrainRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    restartInstance: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["InstanceDrainRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getInstanceSessions: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                instance_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SessionsSnapshot"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listIpRules: {
        parameters: {
            query?: {
                /** @description Filter */
                action?: components["schemas"]["ip_rule_action"];
                /** @description Filter */
                enabled?: boolean;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                scope?: components["schemas"]["ip_rule_scope"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["IpRule"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createIpRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IpRuleCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["IpRule"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getIpRule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["IpRule"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteIpRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateIpRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IpRuleUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["IpRule"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    checkIp: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IpCheckRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["IpCheckResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listLayouts: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Layout"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createLayout: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LayoutCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Layout"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getLayout: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                layout_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Layout"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteLayout: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                layout_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateLayout: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                layout_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LayoutUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Layout"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    applyLayout: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                layout_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["LayoutApplyRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["LayoutApplyResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    previewLayout: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                layout_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["LayoutDiff"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    captureLayout: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LayoutCaptureRequest"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Layout"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    streamLiveEvents: {
        parameters: {
            query?: {
                /** @description Scope */
                cluster_id?: string;
                /** @description Scope */
                deployment_id?: string;
                /** @description Scope */
                instance_id?: string;
                /** @description Metrics push interval */
                interval_seconds?: number;
                /** @description Scope */
                node_id?: string;
                /** @description Subscribe to these topics (default: metrics, instance, node, device, swap, alert) */
                topics?: ("metrics" | "instance" | "node" | "device" | "swap" | "rule_run" | "alert" | "log" | "heartbeat")[];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description text/event-stream of LiveEvent (`event: <event>`, `data: <json>`); `event: heartbeat` every 15 s; `Last-Event-ID` supported for 60 s of replay */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": components["schemas"]["LiveEvent"];
                };
            };
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            500: components["responses"]["Error500"];
        };
    };
    connectLiveWebSocket: {
        parameters: {
            query?: {
                /** @description Scope */
                cluster_id?: string;
                /** @description Scope */
                deployment_id?: string;
                /** @description Scope */
                instance_id?: string;
                /** @description Metrics push interval */
                interval_seconds?: number;
                /** @description Scope */
                node_id?: string;
                /** @description Subscribe to these topics (default: metrics, instance, node, device, swap, alert) */
                topics?: ("metrics" | "instance" | "node" | "device" | "swap" | "rule_run" | "alert" | "log" | "heartbeat")[];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 101 Switching Protocols. Frames are LiveEvent JSON objects. Client may send {"type":"subscribe","topics":[...],"scope":{...}} to change the subscription. */
            101: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
        };
    };
    queryLogs: {
        parameters: {
            query?: {
                /** @description From a previous page */
                cursor?: string;
                /** @description Filter */
                deployment_id?: string;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                instance_id?: string;
                /** @description Minimum level */
                level?: components["schemas"]["log_level"];
                /** @description Max chunks */
                limit?: number;
                /** @description Filter */
                node_id?: string;
                /** @description Filter */
                request_id?: string;
                /** @description Case-insensitive substring */
                search?: string;
                /** @description Filter (repeatable) */
                source?: components["schemas"]["log_source"][];
                /** @description Filter */
                stream?: components["schemas"]["log_stream"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["LogPage"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    exportLogs: {
        parameters: {
            query?: {
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                instance_id?: string;
                /** @description Minimum level */
                level?: components["schemas"]["log_level"];
                /** @description Filter */
                node_id?: string;
                /** @description Filter */
                source?: components["schemas"]["log_source"][];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description text/plain attachment */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/plain": string;
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    streamLogs: {
        parameters: {
            query?: {
                /** @description Filter */
                deployment_id?: string;
                /** @description Filter */
                instance_id?: string;
                /** @description Minimum level */
                level?: components["schemas"]["log_level"];
                /** @description Filter */
                node_id?: string;
                /** @description Filter */
                source?: components["schemas"]["log_source"][];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description text/event-stream; each event is `event: log` + `data: <LogChunk JSON>`; `event: heartbeat` every 15 s */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": components["schemas"]["LogChunk"];
                };
            };
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            500: components["responses"]["Error500"];
        };
    };
    listMetricNames: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: {
                            description?: string;
                            name?: string;
                            /** @enum {string} */
                            scope?: "gpu" | "engine" | "session" | "usage" | "node";
                            /** @enum {string} */
                            type?: "gauge" | "counter" | "histogram";
                            unit?: string;
                        }[];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    queryGpuMetrics: {
        parameters: {
            query?: {
                /** @description Collapse devices into one series (default none) */
                aggregate?: "none" | "sum" | "avg" | "max";
                /** @description Scope */
                cluster_id?: string;
                /** @description Scope (repeatable) */
                device_id?: string[];
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Metric names to return (repeatable). Default: the screen's standard set for the scope */
                metrics?: string[];
                /** @description Scope */
                node_id?: string;
                /** @description Bucket size: 2s (raw, only within METRICS_RAW_RETENTION_HOURS), 1m, 5m, 15m, 1h, 1d */
                step?: components["parameters"]["step"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["TimeSeriesResponse"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    queryInferenceMetrics: {
        parameters: {
            query?: {
                /** @description Collapse instances (default sum for counters/sessions, avg for latencies) */
                aggregate?: "none" | "sum" | "avg" | "max";
                /** @description Scope */
                deployment_id?: string;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Scope (repeatable) */
                instance_id?: string[];
                /** @description Metric names to return (repeatable). Default: the screen's standard set for the scope */
                metrics?: string[];
                /** @description Scope */
                model_id?: string;
                /** @description Scope */
                node_id?: string;
                /** @description Bucket size: 2s (raw, only within METRICS_RAW_RETENTION_HOURS), 1m, 5m, 15m, 1h, 1d */
                step?: components["parameters"]["step"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["TimeSeriesResponse"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getOverview: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Overview"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getPrometheusExposition: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description text/plain; version=0.0.4 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/plain": string;
                };
            };
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            500: components["responses"]["Error500"];
        };
    };
    querySessionMetrics: {
        parameters: {
            query: {
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Instance */
                instance_id: string;
                /** @description One request */
                request_id?: string;
                /** @description One session */
                session_key?: string;
                /** @description Bucket size: 2s (raw, only within METRICS_RAW_RETENTION_HOURS), 1m, 5m, 15m, 1h, 1d */
                step?: components["parameters"]["step"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["TimeSeriesResponse"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getLiveSessions: {
        parameters: {
            query?: {
                /** @description Scope */
                deployment_id?: string;
                /** @description Scope */
                instance_id?: string;
                /** @description Scope */
                node_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SessionsSnapshot"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    queryUsageMetrics: {
        parameters: {
            query?: {
                /** @description Scope */
                api_key_id?: string;
                /** @description Scope */
                deployment_id?: string;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description One series per group */
                group_by?: "none" | "api_key" | "model" | "node" | "deployment";
                /** @description Metric names to return (repeatable). Default: the screen's standard set for the scope */
                metrics?: string[];
                /** @description Scope */
                model_id?: string;
                /** @description Scope */
                node_id?: string;
                /** @description 1h or 1d */
                step?: string;
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["TimeSeriesResponse"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listCacheItems: {
        parameters: {
            query?: {
                /** @description Filter */
                model_id?: string;
                /** @description Filter */
                model_version_id?: string;
                /** @description Filter */
                node_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                pinned?: boolean;
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                state?: components["schemas"]["cache_state"];
                /** @description last_used_at older than N hours */
                unused_for_hours?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CacheItem"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteCacheItem: {
        parameters: {
            query?: {
                /** @description Even if in use */
                force?: boolean;
            };
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                item_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    pinCacheItem: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                item_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": {
                    reason?: string | null;
                };
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CacheItem"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    unpinCacheItem: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                item_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CacheItem"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    evictCache: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EvictRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    prefetchModel: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrefetchRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getCacheSummary: {
        parameters: {
            query?: {
                /** @description Filter */
                cluster_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CacheSummary"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listModels: {
        parameters: {
            query?: {
                /** @description Filter */
                family?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                priority?: components["schemas"]["model_priority"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                source?: components["schemas"]["model_source"];
                /** @description Filter */
                tag?: string;
                /** @description Filter */
                type?: components["schemas"]["model_type"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Model"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    registerModel: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Model"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getModel: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                model_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Model"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteModel: {
        parameters: {
            query?: {
                /** @description Also evict cached files on every node */
                evict_cache?: boolean;
            };
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                model_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateModel: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                model_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Model"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listModelVersions: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                model_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ModelVersion"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    addModelVersion: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                model_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelVersionCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ModelVersion"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getModelVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                model_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ModelVersion"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteModelVersion: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                model_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateModelVersion: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                model_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    is_default?: boolean;
                    quantization?: string | null;
                    version?: string;
                };
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ModelVersion"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listModelVersionCache: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                model_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CacheItem"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listModelFiles: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                model_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ModelFile"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    verifyModelVersion: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                model_id: string;
                /** @description Resource id */
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["VerifyResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listNodes: {
        parameters: {
            query?: {
                /** @description Filter */
                cluster_id?: string;
                /** @description key=value, repeatable (AND) */
                label?: string[];
                /** @description Filter */
                os?: components["schemas"]["node_os"];
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                status?: components["schemas"]["node_status"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Node"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getNode: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Node"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    removeNode: {
        parameters: {
            query?: {
                /** @description Remove even with running instances (they are marked error) */
                force?: boolean;
            };
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NodeUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Node"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getNodeCache: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CacheSummary"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listNodeCertificates: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeCertificate"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    revokeNodeCertificate: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                cert_id: string;
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["RevokeRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeCertificate"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    rotateNodeCertificate: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listNodeCommands: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                state?: components["schemas"]["agent_command_state"];
            };
            header?: never;
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["AgentCommand"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getNodeCommand: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                command_id: string;
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeFileCommandResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listNodeDevices: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Device"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    drainNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["DrainRequest"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CommandRefs"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    hashNodeFiles: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NodeHashRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeFileCommandResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            /** @description Still running; poll /nodes/{node_id}/commands/{command_id} */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeFileCommandResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listNodeInstances: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
            };
            header?: never;
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Instance"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    scanNodePath: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NodeScanRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeFileCommandResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            /** @description Still running; poll /nodes/{node_id}/commands/{command_id} */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeFileCommandResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    undrainNode: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Node"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listJoinTokens: {
        parameters: {
            query?: {
                /** @description Unrevoked, unexpired and with uses left only */
                active_only?: boolean;
                /** @description Filter */
                cluster_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["JoinToken"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createJoinToken: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JoinTokenCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["JoinTokenCreated"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    revokeJoinToken: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                token_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getCaCertificate: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CaCertificate"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            500: components["responses"]["Error500"];
        };
    };
    listCertificates: {
        parameters: {
            query?: {
                /** @description Filter */
                expiring_within_days?: number;
                /** @description Filter */
                kind?: components["schemas"]["certificate_kind"];
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                revoked?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeCertificate"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    revokeCertificate: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                cert_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["RevokeRequest"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["NodeCertificate"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getCrl: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Crl"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            500: components["responses"]["Error500"];
        };
    };
    issueServiceCertificate: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ServiceCertificateRequest"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["ServiceCertificateIssued"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listPricePlans: {
        parameters: {
            query?: {
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["PricePlan"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createPricePlan: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PricePlanCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["PricePlan"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getPricePlan: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["PricePlan"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deletePricePlan: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updatePricePlan: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PricePlanUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["PricePlan"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listAllRuleRuns: {
        parameters: {
            query?: {
                /** @description Runs that targeted this deployment */
                deployment_id?: string;
                /** @description Filter */
                fired?: boolean;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                outcome?: components["schemas"]["rule_run_outcome"];
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                rule_type?: components["schemas"]["rule_type"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RuleRun"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getRuleRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RuleRun"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listRules: {
        parameters: {
            query?: {
                /** @description Filter */
                enabled?: boolean;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                type?: components["schemas"]["rule_type"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Rule"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RuleCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Rule"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getRule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Rule"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RuleUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Rule"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    disableRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Rule"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    dryRunRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RuleRun"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    enableRule: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Rule"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    runRuleNow: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RuleRun"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listRuleRuns: {
        parameters: {
            query?: {
                /** @description Filter */
                fired?: boolean;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                outcome?: components["schemas"]["rule_run_outcome"];
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path: {
                /** @description Resource id */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RuleRun"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getRuleSchemas: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["RuleSchemas"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listSwaps: {
        parameters: {
            query?: {
                /** @description Non-terminal only */
                active_only?: boolean;
                /** @description Filter */
                deployment_id?: string;
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                kind?: components["schemas"]["swap_kind"];
                /** @description Filter */
                node_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                state?: components["schemas"]["swap_state"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
                /** @description Filter */
                trigger?: components["schemas"]["swap_trigger"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SwapOperation"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createSwap: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SwapCreate"];
            };
        };
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SwapOperation"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getSwap: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                swap_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SwapOperation"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    cancelSwap: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                swap_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SwapOperation"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    retrySwap: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                swap_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SwapOperation"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getSystemInfo: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["SystemInfo"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getTopology: {
        parameters: {
            query?: {
                /** @description Limit to one cluster */
                cluster_id?: string;
                /** @description Include stopped instances */
                include_stopped?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Topology"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listTraces: {
        parameters: {
            query?: {
                /** @description Filter */
                api_key_id?: string;
                /** @description Filter */
                deployment_id?: string;
                /** @description Only errors */
                error?: boolean;
                /** @description Filter */
                format?: components["schemas"]["usage_format"];
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                instance_id?: string;
                /** @description Filter */
                min_duration_ms?: number;
                /** @description Filter */
                model_id?: string;
                /** @description Filter */
                node_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                request_id?: string;
                /** @description Filter */
                service?: string;
                /** @description Filter */
                status_code?: number;
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["TraceSpan"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getTrace: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 32-hex trace id */
                trace_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Trace"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listUsageEvents: {
        parameters: {
            query?: {
                /** @description Filter */
                api_key_id?: string;
                /** @description Filter */
                cluster_id?: string;
                /** @description Filter */
                deployment_id?: string;
                /** @description Filter */
                endpoint?: string;
                /** @description Filter */
                format?: components["schemas"]["usage_format"];
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Filter */
                model_id?: string;
                /** @description Filter */
                node_id?: string;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Filter */
                request_id?: string;
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                status?: components["schemas"]["usage_status"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["UsageEvent"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    exportUsageCsv: {
        parameters: {
            query?: {
                /** @description Filter */
                api_key_id?: string;
                /** @description Filter */
                cluster_id?: string;
                /** @description Filter */
                deployment_id?: string;
                /** @description Filter */
                format?: components["schemas"]["usage_format"];
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description For summary */
                group_by?: string[];
                /** @description events (raw) or summary */
                mode?: "events" | "summary";
                /** @description Filter */
                model_id?: string;
                /** @description Filter */
                node_id?: string;
                /** @description Filter */
                status?: components["schemas"]["usage_status"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description CSV attachment */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/csv": string;
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getUsageSummary: {
        parameters: {
            query?: {
                /** @description Filter */
                api_key_id?: string;
                /** @description Filter */
                cluster_id?: string;
                /** @description Filter */
                deployment_id?: string;
                /** @description Filter */
                format?: components["schemas"]["usage_format"];
                /** @description Range start (inclusive, RFC 3339). Default now-1h */
                from?: components["parameters"]["from"];
                /** @description Group keys (repeatable); default api_key */
                group_by?: ("api_key" | "model" | "node" | "deployment" | "cluster" | "format" | "endpoint" | "day" | "hour")[];
                /** @description Filter */
                model_id?: string;
                /** @description Filter */
                node_id?: string;
                /** @description Filter */
                status?: components["schemas"]["usage_status"];
                /** @description Range end (exclusive). Default now */
                to?: components["parameters"]["to"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["UsageSummary"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    listCredentials: {
        parameters: {
            query?: {
                /** @description Include system credentials (internal CA, hub / API-layer TLS keys), hidden by default */
                include_system?: boolean;
                /** @description 1-based page */
                page?: components["parameters"]["page"];
                /** @description Items per page (max 500) */
                page_size?: components["parameters"]["page_size"];
                /** @description Credential kind(s), comma-separated (e.g. `aws` or `huggingface,modelscope`) */
                provider?: string;
                /** @description Case-insensitive substring/trigram search on name */
                search?: components["parameters"]["search"];
                /** @description Sort field; prefix with - for descending (e.g. -created_at). Default -created_at. */
                sort?: components["parameters"]["sort"];
                /** @description Filter */
                status?: components["schemas"]["credential_status"];
                /** @description Filter */
                type?: components["schemas"]["credential_type"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Credential"][];
                        error: null;
                        meta: components["schemas"]["ListMeta"];
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    createCredential: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredentialCreate"];
            };
        };
        responses: {
            /** @description OK */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Credential"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getCredential: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                credential_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Credential"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    deleteCredential: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                credential_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description No content */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    updateCredential: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                credential_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredentialUpdate"];
            };
        };
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["Credential"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    revealCredential: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                credential_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CredentialRevealed"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    testCredential: {
        parameters: {
            query?: never;
            header: {
                /** @description Double-submit CSRF token from GET /auth/csrf (also set as the readable cookie gpudeck_csrf). Required on every mutating request made with the session cookie; not required when authenticating with a Bearer session token. */
                "X-CSRF-Token": components["parameters"]["CsrfHeader"];
            };
            path: {
                /** @description Resource id */
                credential_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CredentialTestResult"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getCredentialUsage: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Resource id */
                credential_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CredentialUsage"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getVaultMetadata: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["VaultMetadata"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
    getVaultStats: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description OK */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        data: components["schemas"]["CredentialStats"];
                        error: null;
                        meta: Record<string, unknown> | null;
                        /** @constant */
                        success: true;
                    };
                };
            };
            400: components["responses"]["Error400"];
            401: components["responses"]["Error401"];
            403: components["responses"]["Error403"];
            404: components["responses"]["Error404"];
            409: components["responses"]["Error409"];
            422: components["responses"]["Error422"];
            429: components["responses"]["Error429"];
            500: components["responses"]["Error500"];
            503: components["responses"]["Error503"];
        };
    };
}
