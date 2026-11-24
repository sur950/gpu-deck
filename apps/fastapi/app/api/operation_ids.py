"""
operationIds of the admin API, from the contract (``libs/contracts/openapi/admin.yaml``).

FastAPI's default ids (``<function>_<path>_<method>``) differed from the contract, so the
served ``/api/v1/openapi.json`` did not match what clients are generated from. The admin
app passes ``contract_unique_id`` as ``generate_unique_id_function``: every route mounted
under ``API_V1_PREFIX`` whose ``(METHOD, path)`` the contract lists gets the contract's id;
anything else keeps FastAPI's default (``tests/unit/test_openapi_contract.py`` fails on
those, and on any drift between this table and the contract).

The table is generated; after changing admin.yaml run::

    venv/bin/python -m app.api.operation_ids --write
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Tuple

from app.core.logger import get_logger
from fastapi.routing import APIRoute
from fastapi.utils import generate_unique_id

logger = get_logger(__name__)

CONTRACT = Path(__file__).resolve().parents[4] / "libs/contracts/openapi/admin.yaml"
_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# BEGIN GENERATED (method path operationId)
_TABLE = """
GET /agent/downloads listAgentDownloads
GET /agent/downloads/{version}/{target} downloadAgent
GET /agent/downloads/{version}/{target}/sha256 downloadAgentSha256
GET /agent/install.sh agentInstallScript
GET /alert-channels listAlertChannels
POST /alert-channels createAlertChannel
DELETE /alert-channels/{channel_id} deleteAlertChannel
GET /alert-channels/{channel_id} getAlertChannel
PATCH /alert-channels/{channel_id} updateAlertChannel
POST /alert-channels/{channel_id}/test testAlertChannel
GET /alert-events listAlertEvents
GET /alert-events/{event_id} getAlertEvent
POST /alert-events/{event_id}/acknowledge acknowledgeAlertEvent
POST /alert-events/{event_id}/resend resendAlertEvent
POST /alert-events/{event_id}/resolve resolveAlertEvent
GET /api-keys listApiKeys
POST /api-keys createApiKey
GET /api-keys/stats getApiKeyStats
DELETE /api-keys/{key_id} deleteApiKey
GET /api-keys/{key_id} getApiKey
PATCH /api-keys/{key_id} updateApiKey
POST /api-keys/{key_id}/activate activateApiKey
POST /api-keys/{key_id}/revoke revokeApiKey
POST /api-keys/{key_id}/rotate rotateApiKey
POST /api-keys/{key_id}/suspend suspendApiKey
GET /audit listAuditLogs
GET /audit/export exportAuditCsv
GET /audit/{log_id} getAuditLog
POST /auth/change-password changePassword
GET /auth/csrf getCsrfToken
POST /auth/login login
POST /auth/logout logout
POST /auth/logout-all logoutAll
GET /auth/me getMe
GET /auth/sessions listSessions
DELETE /auth/sessions/{session_id} revokeSession
GET /batches listBatches
GET /batches/{batch_id} getBatch
POST /batches/{batch_id}/cancel cancelBatch
GET /benchmark-runs/{run_id} getBenchmarkRun
POST /benchmark-runs/{run_id}/apply applyBenchmarkRecommendation
POST /benchmark-runs/{run_id}/cancel cancelBenchmarkRun
GET /benchmark-runs/{run_id}/export exportBenchmarkRun
GET /benchmarks listBenchmarks
POST /benchmarks createBenchmark
DELETE /benchmarks/{benchmark_id} deleteBenchmark
GET /benchmarks/{benchmark_id} getBenchmark
PATCH /benchmarks/{benchmark_id} updateBenchmark
POST /benchmarks/{benchmark_id}/auto-tune autoTuneBenchmark
GET /benchmarks/{benchmark_id}/runs listBenchmarkRuns
POST /benchmarks/{benchmark_id}/start startBenchmark
GET /billing/estimate getBillingEstimate
GET /billing/reports listBillingReports
POST /billing/reports createBillingReport
DELETE /billing/reports/{report_id} deleteBillingReport
GET /billing/reports/{report_id} getBillingReport
GET /billing/reports/{report_id}/export exportBillingReportCsv
POST /billing/reports/{report_id}/regenerate regenerateBillingReport
GET /branding getBranding
POST /catalog/local/scan scanLocalPath
GET /catalog/remote-files listRemoteFiles
GET /catalog/search searchCatalog
GET /cloud/nodes listCloudNodes
POST /cloud/nodes createCloudNodes
DELETE /cloud/nodes/{cloud_node_id} deleteCloudNode
GET /cloud/nodes/{cloud_node_id} getCloudNode
PATCH /cloud/nodes/{cloud_node_id} updateCloudNode
POST /cloud/nodes/{cloud_node_id}/apply-price-plan applyCloudNodePricePlan
POST /cloud/nodes/{cloud_node_id}/refresh refreshCloudNode
POST /cloud/nodes/{cloud_node_id}/start startCloudNode
POST /cloud/nodes/{cloud_node_id}/stop stopCloudNode
POST /cloud/nodes/{cloud_node_id}/terminate terminateCloudNode
GET /cloud/providers listCloudProviders
POST /cloud/providers/{provider}/check-credential checkCloudCredential
GET /cloud/providers/{provider}/instance-types listCloudInstanceTypes
GET /cloud/providers/{provider}/quotas listCloudQuotas
GET /cloud/providers/{provider}/regions listCloudRegions
GET /clusters listClusters
POST /clusters createCluster
DELETE /clusters/{cluster_id} deleteCluster
GET /clusters/{cluster_id} getCluster
PATCH /clusters/{cluster_id} updateCluster
GET /clusters/{cluster_id}/nodes listClusterNodes
GET /deployments listDeployments
POST /deployments createDeployment
POST /deployments/preview previewPlacement
DELETE /deployments/{deployment_id} deleteDeployment
GET /deployments/{deployment_id} getDeployment
PATCH /deployments/{deployment_id} updateDeployment
GET /deployments/{deployment_id}/instances listDeploymentInstances
POST /deployments/{deployment_id}/restart restartDeployment
POST /deployments/{deployment_id}/scale scaleDeployment
POST /deployments/{deployment_id}/start startDeployment
POST /deployments/{deployment_id}/stop stopDeployment
GET /deployments/{deployment_id}/swaps listDeploymentSwaps
PUT /deployments/{deployment_id}/traffic setTrafficWeights
GET /device-slices listAllDeviceSlices
GET /devices listDevices
GET /devices/{device_id} getDevice
GET /devices/{device_id}/slices listDeviceSlices
POST /devices/{device_id}/slices createDeviceSlice
DELETE /devices/{device_id}/slices/{slice_id} deleteDeviceSlice
PATCH /devices/{device_id}/slices/{slice_id} updateDeviceSlice
GET /engines listEngines
POST /engines/auto-pick autoPickEngine
POST /engines/compat-check runCompatCheck
GET /engines/compat-checks listCompatChecks
GET /engines/{engine_id} getEngine
PATCH /engines/{engine_id} updateEngine
GET /engines/{engine_id}/versions listEngineVersions
POST /engines/{engine_id}/versions createEngineVersion
DELETE /engines/{engine_id}/versions/{version_id} deleteEngineVersion
GET /engines/{engine_id}/versions/{version_id} getEngineVersion
PATCH /engines/{engine_id}/versions/{version_id} updateEngineVersion
GET /gpu-instances listGpuInstances
POST /gpu-instances createGpuInstance
DELETE /gpu-instances/{instance_id} deleteGpuInstance
GET /gpu-instances/{instance_id} getGpuInstance
PATCH /gpu-instances/{instance_id} updateGpuInstance
GET /gpu-instances/{instance_id}/connection getGpuInstanceConnection
POST /gpu-instances/{instance_id}/start startGpuInstance
POST /gpu-instances/{instance_id}/stop stopGpuInstance
GET /health getHealth
GET /health/live getLiveness
GET /health/ready getReadiness
GET /instances listInstances
DELETE /instances/{instance_id} stopInstance
GET /instances/{instance_id} getInstance
PATCH /instances/{instance_id} updateInstance
POST /instances/{instance_id}/drain drainInstance
POST /instances/{instance_id}/restart restartInstance
GET /instances/{instance_id}/sessions getInstanceSessions
GET /ip-rules listIpRules
POST /ip-rules createIpRule
POST /ip-rules/check checkIp
DELETE /ip-rules/{rule_id} deleteIpRule
GET /ip-rules/{rule_id} getIpRule
PATCH /ip-rules/{rule_id} updateIpRule
GET /layouts listLayouts
POST /layouts createLayout
POST /layouts/capture captureLayout
DELETE /layouts/{layout_id} deleteLayout
GET /layouts/{layout_id} getLayout
PATCH /layouts/{layout_id} updateLayout
POST /layouts/{layout_id}/apply applyLayout
POST /layouts/{layout_id}/preview previewLayout
GET /live/events streamLiveEvents
GET /live/ws connectLiveWebSocket
GET /logs queryLogs
GET /logs/export exportLogs
GET /logs/stream streamLogs
GET /metrics/catalog listMetricNames
GET /metrics/gpu queryGpuMetrics
GET /metrics/inference queryInferenceMetrics
GET /metrics/overview getOverview
GET /metrics/prometheus getPrometheusExposition
GET /metrics/sessions querySessionMetrics
GET /metrics/sessions/live getLiveSessions
GET /metrics/usage queryUsageMetrics
GET /model-cache listCacheItems
POST /model-cache/evict evictCache
POST /model-cache/prefetch prefetchModel
GET /model-cache/summary getCacheSummary
DELETE /model-cache/{item_id} deleteCacheItem
POST /model-cache/{item_id}/pin pinCacheItem
POST /model-cache/{item_id}/unpin unpinCacheItem
GET /models listModels
POST /models registerModel
DELETE /models/{model_id} deleteModel
GET /models/{model_id} getModel
PATCH /models/{model_id} updateModel
GET /models/{model_id}/versions listModelVersions
POST /models/{model_id}/versions addModelVersion
DELETE /models/{model_id}/versions/{version_id} deleteModelVersion
GET /models/{model_id}/versions/{version_id} getModelVersion
PATCH /models/{model_id}/versions/{version_id} updateModelVersion
GET /models/{model_id}/versions/{version_id}/cache listModelVersionCache
GET /models/{model_id}/versions/{version_id}/files listModelFiles
POST /models/{model_id}/versions/{version_id}/verify verifyModelVersion
GET /nodes listNodes
GET /nodes/join-tokens listJoinTokens
POST /nodes/join-tokens createJoinToken
DELETE /nodes/join-tokens/{token_id} revokeJoinToken
DELETE /nodes/{node_id} removeNode
GET /nodes/{node_id} getNode
PATCH /nodes/{node_id} updateNode
GET /nodes/{node_id}/cache getNodeCache
GET /nodes/{node_id}/certificates listNodeCertificates
POST /nodes/{node_id}/certificates/rotate rotateNodeCertificate
POST /nodes/{node_id}/certificates/{cert_id}/revoke revokeNodeCertificate
GET /nodes/{node_id}/commands listNodeCommands
GET /nodes/{node_id}/commands/{command_id} getNodeCommand
GET /nodes/{node_id}/devices listNodeDevices
POST /nodes/{node_id}/drain drainNode
POST /nodes/{node_id}/hash hashNodeFiles
GET /nodes/{node_id}/instances listNodeInstances
POST /nodes/{node_id}/scan scanNodePath
POST /nodes/{node_id}/undrain undrainNode
GET /pki/ca getCaCertificate
GET /pki/certificates listCertificates
POST /pki/certificates/{cert_id}/revoke revokeCertificate
GET /pki/crl getCrl
POST /pki/service-certificates issueServiceCertificate
GET /price-plans listPricePlans
POST /price-plans createPricePlan
DELETE /price-plans/{plan_id} deletePricePlan
GET /price-plans/{plan_id} getPricePlan
PATCH /price-plans/{plan_id} updatePricePlan
GET /rule-runs listAllRuleRuns
GET /rule-runs/{run_id} getRuleRun
GET /rules listRules
POST /rules createRule
GET /rules/schemas getRuleSchemas
DELETE /rules/{rule_id} deleteRule
GET /rules/{rule_id} getRule
PATCH /rules/{rule_id} updateRule
POST /rules/{rule_id}/disable disableRule
POST /rules/{rule_id}/dry-run dryRunRule
POST /rules/{rule_id}/enable enableRule
POST /rules/{rule_id}/run runRuleNow
GET /rules/{rule_id}/runs listRuleRuns
GET /swaps listSwaps
POST /swaps createSwap
GET /swaps/{swap_id} getSwap
POST /swaps/{swap_id}/cancel cancelSwap
POST /swaps/{swap_id}/retry retrySwap
GET /system/info getSystemInfo
GET /topology getTopology
GET /traces listTraces
GET /traces/{trace_id} getTrace
GET /usage listUsageEvents
GET /usage/export exportUsageCsv
GET /usage/summary getUsageSummary
GET /vault listCredentials
POST /vault createCredential
GET /vault/metadata getVaultMetadata
GET /vault/stats getVaultStats
DELETE /vault/{credential_id} deleteCredential
GET /vault/{credential_id} getCredential
PATCH /vault/{credential_id} updateCredential
POST /vault/{credential_id}/reveal revealCredential
POST /vault/{credential_id}/test testCredential
GET /vault/{credential_id}/usage getCredentialUsage
"""
# END GENERATED


def _parse(table: str) -> Dict[Tuple[str, str], str]:
    out: Dict[Tuple[str, str], str] = {}
    for line in table.strip().splitlines():
        method, path, op_id = line.split()
        out[(method, path)] = op_id
    return out


OPERATION_IDS: Dict[Tuple[str, str], str] = _parse(_TABLE)


def contract_unique_id(route: APIRoute) -> str:
    """The contract's operationId for an ``/api/v1`` route, else FastAPI's default id."""
    from app.core.settings import settings

    prefix = settings.API_V1_PREFIX
    path = route.path_format
    if path.startswith(prefix):
        path = path[len(prefix) :] or "/"
        for method in sorted(route.methods or ()):
            op_id = OPERATION_IDS.get((method.upper(), path))
            if op_id:
                return op_id
    return generate_unique_id(route)


def contract_table(contract: Path = CONTRACT) -> str:
    """The generated table text from admin.yaml (sorted by path, then method)."""
    import yaml

    spec = yaml.safe_load(contract.read_text())
    rows = []
    for path, item in (spec.get("paths") or {}).items():
        for method in _METHODS:
            op = (item or {}).get(method)
            if isinstance(op, dict) and op.get("operationId"):
                rows.append((path, method.upper(), str(op["operationId"])))
    rows.sort()
    return "\n" + "".join(f"{m} {p} {i}\n" for p, m, i in rows)


def _write() -> int:
    source = Path(__file__)
    text = source.read_text()
    start = text.index('_TABLE = """') + len('_TABLE = """')
    end = text.index('"""', start)
    new = text[:start] + contract_table() + text[end:]
    if new != text:
        source.write_text(new)
        print(f"updated {source}")
    return 0


if __name__ == "__main__":
    if "--write" in sys.argv:
        sys.exit(_write())
    print(contract_table(), end="")
