"""
Typed credentials: the catalog of credential kinds GPU Deck consumes.

A credential's kind lives in ``credentials.provider`` (``aws``, ``huggingface``,
``slack`` ...); ``credentials.type`` keeps the broad category enum. Every kind
declares its fields (which ones are secret), a JSON Schema built from them, and
how the value is laid out in ``encrypted_value``:

* ``storage = "<field>"``: the kind's one secret field is stored verbatim (HF /
  ModelScope token, Slack webhook URL, SMTP password, webhook HMAC secret, GCP
  key JSON, DigitalOcean token, ...), so consumers that read the plaintext with
  ``VaultService.get_secret`` keep working unchanged.
* ``storage = "json"``: all fields as one JSON object (AWS, Azure) — the shape
  the cloud providers' ``parse_credentials`` read.

Public (non-secret) fields plus derived facts (GCP ``project_id``, SSH key
fingerprint, kubeconfig context) are mirrored in ``extra_data.fields`` and
returned by the API. Secret fields are write-only: they leave the vault only
through the audited reveal or a consuming domain.

System credentials (internal CA, hub / API-layer TLS keys) are not kinds: they
are written by the PKI service, hidden from listings and refused by the admin
write / reveal endpoints.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.exceptions import ValidationError
from app.core.logger import get_logger
from jsonschema import Draft202012Validator

logger = get_logger(__name__)

SYSTEM_PROVIDERS = {"internal_ca", "agent_hub_tls", "api_layer_tls"}
SYSTEM_TYPES = {"ca_key", "tls_key"}

UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
NO_SPACE_PATTERN = r"^\S+$"


class CredentialValidationError(ValidationError):
    """422 CREDENTIAL_INVALID: the fields do not match the kind's schema."""

    def __init__(self, detail: str, errors: Optional[List[Dict[str, str]]] = None):
        super().__init__(detail)
        self.error_code = "CREDENTIAL_INVALID"
        self.errors = errors or []


@dataclass(frozen=True)
class FieldSpec:
    """One input of a credential kind (drives the JSON Schema and the web form)."""

    name: str
    label: str
    input: str = "text"  # text | password | textarea | json | number | boolean | url | email
    secret: bool = False
    required: bool = False
    help: Optional[str] = None
    placeholder: Optional[str] = None
    default: Any = None
    pattern: Optional[str] = None
    pattern_hint: Optional[str] = None
    min_length: Optional[int] = None
    max_length: int = 4096
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    upload_accept: Optional[str] = None

    def schema(self) -> Dict[str, Any]:
        if self.input == "number":
            out: Dict[str, Any] = {"type": "integer"}
            if self.minimum is not None:
                out["minimum"] = self.minimum
            if self.maximum is not None:
                out["maximum"] = self.maximum
        elif self.input == "boolean":
            out = {"type": "boolean"}
        else:
            out = {"type": "string", "maxLength": self.max_length}
            if self.min_length is not None:
                out["minLength"] = self.min_length
            if self.pattern:
                out["pattern"] = self.pattern
        out["title"] = self.label
        if self.help:
            out["description"] = self.help
        if self.default is not None:
            out["default"] = self.default
        if self.secret:
            out["writeOnly"] = True
        return out

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "input": self.input,
            "secret": self.secret,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
            "default": self.default,
            "pattern": self.pattern,
            "pattern_hint": self.pattern_hint,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "upload_accept": self.upload_accept,
        }


Derive = Callable[[Dict[str, Any]], Dict[str, Any]]
Check = Callable[[Dict[str, Any]], List[Tuple[str, str]]]
Text = Callable[[Dict[str, Any]], Optional[str]]


@dataclass(frozen=True)
class CredentialKind:
    """A credential type: fields, storage layout and the consumers that read it."""

    id: str
    label: str
    category: str  # cloud | model_source | notification | access | other
    credential_type: str  # credentials.type enum value
    description: str
    fields: Tuple[FieldSpec, ...]
    storage: str  # a secret field stored verbatim, or "json"
    docs_url: Optional[str] = None
    used_for: Tuple[str, ...] = ()
    consumers: Tuple[str, ...] = ()
    cloud_provider: Optional[str] = None
    derive: Optional[Derive] = None
    check: Optional[Check] = None
    preview: Optional[Text] = None
    summary: Optional[Text] = None
    derived_fields: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def secret_fields(self) -> List[str]:
        return [f.name for f in self.fields if f.secret]

    @property
    def public_fields(self) -> List[str]:
        return [f.name for f in self.fields if not f.secret]

    def field(self, name: str) -> Optional[FieldSpec]:
        return next((f for f in self.fields if f.name == name), None)

    def json_schema(self) -> Dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"gpu-deck:credential:{self.id}",
            "title": self.label,
            "type": "object",
            "properties": {f.name: f.schema() for f in self.fields},
            "required": [f.name for f in self.fields if f.required],
            "additionalProperties": False,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "credential_type": self.credential_type,
            "description": self.description,
            "fields": [f.describe() for f in self.fields],
            "secret_fields": self.secret_fields,
            "derived_fields": list(self.derived_fields),
            "json_schema": self.json_schema(),
            "docs_url": self.docs_url,
            "used_for": list(self.used_for),
            "consumers": list(self.consumers),
            "cloud_provider": self.cloud_provider,
            "test": "cloud" if self.cloud_provider else "format",
        }


# ---------------------------------------------------------------------------
# helpers used by the kinds
# ---------------------------------------------------------------------------


def mask(value: Optional[str]) -> str:
    """Same rule as crypto.mask_value: 'sk-abc123def456' -> 'sk-abc...f456'."""
    text = str(value or "")
    if len(text) <= 4:
        return "****"
    prefix = text[:6] if len(text) > 10 else text[:3]
    return f"{prefix}...{text[-4:]}"


def _clip(text: Optional[str], limit: int = 50) -> Optional[str]:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _host_of(url: Optional[str]) -> Optional[str]:
    match = re.match(r"^[a-z]+://([^/:?#]+)", str(url or ""), re.IGNORECASE)
    return match.group(1) if match else None


def _gcp_key(fields: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(fields.get("service_account_json") or "")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _gcp_derive(fields: Dict[str, Any]) -> Dict[str, Any]:
    key = _gcp_key(fields)
    return {
        "project_id": key.get("project_id"),
        "client_email": key.get("client_email"),
        "private_key_id": (mask(key.get("private_key_id")) if key.get("private_key_id") else None),
    }


def _gcp_check(fields: Dict[str, Any]) -> List[Tuple[str, str]]:
    raw = fields.get("service_account_json") or ""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return [("service_account_json", f"is not valid JSON ({exc.msg})")]
    if not isinstance(data, dict):
        return [("service_account_json", "must be a JSON object (the downloaded key file)")]
    errors: List[Tuple[str, str]] = []
    if data.get("type") != "service_account":
        errors.append(("service_account_json", 'needs "type" set to "service_account"'))
    for key in ("project_id", "client_email", "private_key"):
        if not data.get(key):
            errors.append(("service_account_json", f'is missing "{key}"'))
    if data.get("private_key") and "PRIVATE KEY" not in str(data["private_key"]):
        errors.append(("service_account_json", '"private_key" is not a PEM private key'))
    return errors


def _ssh_parse(fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from app.services.gpu_instances.keys import parse_key

    try:
        return parse_key(str(fields.get("public_key") or ""))
    except ValidationError:
        return None


def _ssh_check(fields: Dict[str, Any]) -> List[Tuple[str, str]]:
    from app.services.gpu_instances.keys import parse_key

    text = str(fields.get("public_key") or "").strip()
    if "PRIVATE KEY" in text:
        return [("public_key", "is a private key; paste the public key (.pub) instead")]
    try:
        parse_key(text)
    except ValidationError as exc:
        return [("public_key", str(exc.detail))]
    return []


def _ssh_derive(fields: Dict[str, Any]) -> Dict[str, Any]:
    parsed = _ssh_parse(fields)
    if not parsed:
        return {}
    return {
        "public_key": parsed["key"],
        "key_type": parsed["type"],
        "fingerprint": parsed["fingerprint"],
        "comment": parsed["comment"],
    }


def _kube(fields: Dict[str, Any]) -> Dict[str, Any]:
    import yaml

    try:
        data = yaml.safe_load(str(fields.get("kubeconfig") or ""))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _kube_check(fields: Dict[str, Any]) -> List[Tuple[str, str]]:
    data = _kube(fields)
    if not data:
        return [("kubeconfig", "is not a YAML kubeconfig")]
    if not isinstance(data.get("clusters"), list) or not data["clusters"]:
        return [("kubeconfig", "has no clusters")]
    return []


def _kube_derive(fields: Dict[str, Any]) -> Dict[str, Any]:
    data = _kube(fields)
    if not data:
        return {}
    context = data.get("current-context")
    server = None
    for item in data.get("clusters") or []:
        if isinstance(item, dict) and isinstance(item.get("cluster"), dict):
            server = item["cluster"].get("server")
            break
    return {"current_context": context, "server": server}


def _url_check(name: str) -> Check:
    def check(fields: Dict[str, Any]) -> List[Tuple[str, str]]:
        value = str(fields.get(name) or "")
        if value and not _host_of(value):
            return [(name, "must be an http(s) URL with a host")]
        return []

    return check


# ---------------------------------------------------------------------------
# the kinds
# ---------------------------------------------------------------------------

KINDS: Dict[str, CredentialKind] = {}


def _register(kind: CredentialKind) -> None:
    KINDS[kind.id] = kind


_register(
    CredentialKind(
        id="aws",
        label="AWS access key",
        category="cloud",
        credential_type="cloud_account",
        description="IAM access key for AWS cloud clusters: browse GPU instance types, "
        "provision and stop EC2 GPU VMs.",
        fields=(
            FieldSpec(
                "access_key_id",
                "Access key ID",
                secret=True,
                required=True,
                placeholder="AKIA…",
                pattern=r"^[A-Z0-9]{8,128}$",
                pattern_hint="Upper-case letters and digits, usually 20 characters (AKIA… / ASIA…)",
                max_length=128,
            ),
            FieldSpec(
                "secret_access_key",
                "Secret access key",
                input="password",
                secret=True,
                required=True,
                min_length=8,
                max_length=256,
            ),
            FieldSpec(
                "session_token",
                "Session token",
                input="textarea",
                secret=True,
                help="Only for temporary (ASIA…) keys.",
                max_length=4096,
            ),
            FieldSpec(
                "role_arn",
                "Role to assume",
                placeholder="arn:aws:iam::123456789012:role/gpu-deck",
                help="Optional: GPU Deck assumes this role with the key above (STS).",
                pattern=r"^arn:aws[a-z-]*:iam::\d{12}:role/\S+$",
                pattern_hint="arn:aws:iam::<12-digit account>:role/<name>",
                max_length=2048,
            ),
            FieldSpec(
                "external_id",
                "External ID",
                help="Only when the role's trust policy requires one.",
                max_length=1224,
            ),
            FieldSpec(
                "region",
                "Default region",
                placeholder="us-east-1",
                help="Informational; each cluster sets the region it launches in.",
                pattern=r"^[a-z]{2}(-[a-z]+)+-\d+$",
                pattern_hint="An AWS region id such as us-east-1",
                max_length=64,
            ),
        ),
        storage="json",
        docs_url="https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html",
        used_for=("AWS cloud clusters", "Cloud GPU VM provisioning"),
        consumers=("cluster.cloud_aws", "cloud.aws"),
        cloud_provider="aws",
        preview=lambda f: mask(f.get("access_key_id")),
        summary=lambda f: " · ".join(
            x
            for x in (
                (f"role {str(f['role_arn']).rsplit('/', 1)[-1]}" if f.get("role_arn") else None),
                f.get("region"),
            )
            if x
        )
        or None,
    )
)

_register(
    CredentialKind(
        id="azure",
        label="Azure service principal",
        category="cloud",
        credential_type="service_account",
        description="Entra ID app registration (client secret) with Contributor on the "
        "subscription GPU Deck launches NC/ND VMs in.",
        fields=(
            FieldSpec(
                "tenant_id",
                "Tenant ID",
                required=True,
                pattern=UUID_PATTERN,
                pattern_hint="A GUID",
                max_length=36,
            ),
            FieldSpec(
                "client_id",
                "Client (application) ID",
                required=True,
                pattern=UUID_PATTERN,
                pattern_hint="A GUID",
                max_length=36,
            ),
            FieldSpec(
                "client_secret",
                "Client secret",
                input="password",
                secret=True,
                required=True,
                min_length=8,
                max_length=1024,
            ),
            FieldSpec(
                "subscription_id",
                "Subscription ID",
                required=True,
                pattern=UUID_PATTERN,
                pattern_hint="A GUID",
                max_length=36,
            ),
        ),
        storage="json",
        docs_url="https://learn.microsoft.com/en-us/entra/identity-platform/"
        "howto-create-service-principal-portal",
        used_for=("Azure cloud clusters", "Cloud GPU VM provisioning"),
        consumers=("cluster.cloud_azure", "cloud.azure"),
        cloud_provider="azure",
        preview=lambda f: mask(f.get("client_secret")),
        summary=lambda f: (
            f"subscription {str(f.get('subscription_id') or '')[:8]}…"
            if f.get("subscription_id")
            else None
        ),
    )
)

_register(
    CredentialKind(
        id="gcp",
        label="Google Cloud service account",
        category="cloud",
        credential_type="service_account",
        description="Service account JSON key with Compute Admin on the project GPU Deck "
        "launches GPU VMs in. The project comes from the key.",
        fields=(
            FieldSpec(
                "service_account_json",
                "Service account key (JSON)",
                input="json",
                secret=True,
                required=True,
                help="Upload the key file from IAM → Service accounts → Keys, or paste it.",
                max_length=16384,
                upload_accept=".json,application/json",
            ),
        ),
        storage="service_account_json",
        docs_url="https://cloud.google.com/iam/docs/keys-create-delete",
        used_for=("Google Cloud clusters", "Cloud GPU VM provisioning"),
        consumers=("cluster.cloud_gcp", "cloud.gcp"),
        cloud_provider="gcp",
        derive=_gcp_derive,
        check=_gcp_check,
        derived_fields=("project_id", "client_email", "private_key_id"),
        preview=lambda f: _clip(_gcp_derive(f).get("client_email")) or "service account",
        summary=lambda f: (
            f"project {_gcp_derive(f).get('project_id')}"
            if _gcp_derive(f).get("project_id")
            else None
        ),
    )
)

_register(
    CredentialKind(
        id="digitalocean",
        label="DigitalOcean API token",
        category="cloud",
        credential_type="token",
        description="Personal access token with read + write scope for GPU Droplets.",
        fields=(
            FieldSpec(
                "api_token",
                "API token",
                input="password",
                secret=True,
                required=True,
                placeholder="dop_v1_…",
                pattern=NO_SPACE_PATTERN,
                pattern_hint="No spaces",
                min_length=20,
                max_length=512,
            ),
        ),
        storage="api_token",
        docs_url="https://docs.digitalocean.com/reference/api/create-personal-access-token/",
        used_for=("DigitalOcean cloud clusters", "Cloud GPU VM provisioning"),
        consumers=("cluster.cloud_do", "cloud.digitalocean"),
        cloud_provider="digitalocean",
        preview=lambda f: mask(f.get("api_token")),
    )
)

_register(
    CredentialKind(
        id="huggingface",
        label="Hugging Face token",
        category="model_source",
        credential_type="token",
        description="Read token for gated or private repos: catalog search, file listings "
        "and the downloads agents make for that model.",
        fields=(
            FieldSpec(
                "token",
                "Access token",
                input="password",
                secret=True,
                required=True,
                placeholder="hf_…",
                pattern=NO_SPACE_PATTERN,
                pattern_hint="No spaces",
                min_length=8,
                max_length=512,
            ),
        ),
        storage="token",
        docs_url="https://huggingface.co/docs/hub/security-tokens",
        used_for=("Catalog search and registration", "Model downloads on nodes"),
        consumers=("catalog.huggingface", "model_cache.huggingface"),
        preview=lambda f: mask(f.get("token")),
    )
)

_register(
    CredentialKind(
        id="modelscope",
        label="ModelScope token",
        category="model_source",
        credential_type="token",
        description="SDK token for private ModelScope repos: catalog search, file listings "
        "and node downloads.",
        fields=(
            FieldSpec(
                "token",
                "SDK token",
                input="password",
                secret=True,
                required=True,
                pattern=NO_SPACE_PATTERN,
                pattern_hint="No spaces",
                min_length=8,
                max_length=512,
            ),
        ),
        storage="token",
        docs_url="https://modelscope.cn/my/myaccesstoken",
        used_for=("Catalog search and registration", "Model downloads on nodes"),
        consumers=("catalog.modelscope", "model_cache.modelscope"),
        preview=lambda f: mask(f.get("token")),
    )
)

_register(
    CredentialKind(
        id="slack",
        label="Slack incoming webhook",
        category="notification",
        credential_type="webhook",
        description="Incoming-webhook URL of the Slack app that posts GPU Deck alerts.",
        fields=(
            FieldSpec(
                "webhook_url",
                "Webhook URL",
                input="password",
                secret=True,
                required=True,
                placeholder="https://hooks.slack.com/services/…",
                pattern=r"^https?://\S+$",
                pattern_hint="An http(s) URL (https://hooks.slack.com/… for Slack)",
                help="Slack app incoming webhook; Mattermost and Rocket.Chat incoming "
                "webhooks work too.",
                max_length=2048,
            ),
        ),
        storage="webhook_url",
        docs_url="https://api.slack.com/messaging/webhooks",
        used_for=("Slack alert channels",),
        consumers=("alert_channel.slack",),
        preview=lambda f: _clip(f"{_host_of(f.get('webhook_url')) or 'webhook'}/…", 44)
        + str(f.get("webhook_url") or "")[-4:],
    )
)

_register(
    CredentialKind(
        id="smtp",
        label="SMTP server",
        category="notification",
        credential_type="smtp",
        description="Mail server login for email alert channels. Recipients are set on "
        "each channel.",
        fields=(
            FieldSpec(
                "host",
                "Host",
                required=True,
                placeholder="smtp.office365.com",
                pattern=r"^[A-Za-z0-9.-]+$",
                pattern_hint="A host name or IP address",
                max_length=255,
            ),
            FieldSpec("port", "Port", input="number", default=587, minimum=1, maximum=65535),
            FieldSpec("username", "Username", placeholder="alerts@example.com", max_length=320),
            FieldSpec(
                "password",
                "Password",
                input="password",
                secret=True,
                required=True,
                max_length=1024,
            ),
            FieldSpec(
                "from",
                "From address",
                input="email",
                placeholder="gpudeck@example.com",
                pattern=r"^[^@\s]+@[^@\s]+$",
                pattern_hint="An email address",
                max_length=320,
            ),
            FieldSpec(
                "starttls",
                "STARTTLS",
                input="boolean",
                default=True,
                help="Implicit TLS is used on port 465.",
            ),
        ),
        storage="password",
        docs_url=None,
        used_for=("Email alert channels",),
        consumers=("alert_channel.email",),
        preview=lambda f: mask(f.get("password")),
        summary=lambda f: (f"{f['username']} @ " if f.get("username") else "")
        + f"{f.get('host')}:{f.get('port') or 587}",
    )
)

_register(
    CredentialKind(
        id="webhook",
        label="Signed webhook",
        category="notification",
        credential_type="webhook",
        description="Destination URL plus the HMAC-SHA256 secret that signs every payload "
        "(X-GPUDeck-Signature).",
        fields=(
            FieldSpec(
                "url",
                "URL",
                input="url",
                required=True,
                placeholder="https://hooks.example.com/gpu-deck",
                pattern=r"^https?://\S+$",
                pattern_hint="An http(s) URL",
                max_length=2048,
            ),
            FieldSpec(
                "hmac_secret",
                "Signing secret",
                input="password",
                secret=True,
                required=True,
                help="Receivers recompute HMAC-SHA256(secret, timestamp + '.' + body).",
                min_length=16,
                max_length=1024,
            ),
        ),
        storage="hmac_secret",
        used_for=("Webhook alert channels",),
        consumers=("alert_channel.webhook",),
        check=_url_check("url"),
        preview=lambda f: mask(f.get("hmac_secret")),
        summary=lambda f: _host_of(f.get("url")),
    )
)

_register(
    CredentialKind(
        id="ssh_key",
        label="SSH public key",
        category="access",
        credential_type="ssh_key",
        description="An OpenSSH public key. GPU Deck only ever needs the public half: it is "
        "added to SSH GPU instances and to cloud VMs (Azure, Google Cloud).",
        fields=(
            FieldSpec(
                "public_key",
                "Public key",
                input="textarea",
                required=True,
                placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA… you@laptop",
                help="One line from a .pub file. Private keys are refused.",
                max_length=16384,
                upload_accept=".pub,text/plain",
            ),
        ),
        storage="public_key",
        docs_url="https://man.openbsd.org/ssh-keygen",
        used_for=("Cluster install helper", "SSH GPU instances", "Cloud VM login"),
        consumers=(
            "cluster.bare_metal",
            "cluster.vm",
            "cluster.ssh_key",
            "gpu_instance.authorized_keys",
        ),
        derive=_ssh_derive,
        check=_ssh_check,
        derived_fields=("key_type", "fingerprint", "comment"),
        preview=lambda f: _clip(_ssh_derive(f).get("fingerprint"), 50) or "ssh key",
        summary=lambda f: " · ".join(
            str(x) for x in (_ssh_derive(f).get("key_type"), _ssh_derive(f).get("comment")) if x
        )
        or None,
    )
)

_register(
    CredentialKind(
        id="container_registry",
        label="Container registry",
        category="access",
        credential_type="token",
        description="Login for a private image registry (docker login).",
        fields=(
            FieldSpec(
                "registry",
                "Registry",
                required=True,
                placeholder="ghcr.io",
                pattern=r"^[A-Za-z0-9.-]+(:\d+)?(/\S*)?$",
                pattern_hint="A registry host such as ghcr.io or registry.example.com:5000",
                max_length=255,
            ),
            FieldSpec("username", "Username", required=True, max_length=255),
            FieldSpec(
                "password",
                "Password or token",
                input="password",
                secret=True,
                required=True,
                max_length=4096,
            ),
        ),
        storage="password",
        docs_url="https://docs.docker.com/reference/cli/docker/login/",
        used_for=("Private engine / instance images",),
        consumers=("registry",),
        preview=lambda f: mask(f.get("password")),
        summary=lambda f: f"{f.get('username')} @ {f.get('registry')}",
    )
)

_register(
    CredentialKind(
        id="kubernetes",
        label="Kubeconfig",
        category="access",
        credential_type="service_account",
        description="Kubeconfig for a Kubernetes cluster that runs the gpudeck-agent " "DaemonSet.",
        fields=(
            FieldSpec(
                "kubeconfig",
                "Kubeconfig",
                input="textarea",
                secret=True,
                required=True,
                help="Upload ~/.kube/config or paste it (YAML).",
                max_length=65536,
                upload_accept=".yaml,.yml,.conf,.config,text/yaml,text/plain",
            ),
        ),
        storage="kubeconfig",
        docs_url="https://kubernetes.io/docs/concepts/configuration/"
        "organize-cluster-access-kubeconfig/",
        used_for=("Kubernetes clusters",),
        consumers=("cluster.kubernetes",),
        derive=_kube_derive,
        check=_kube_check,
        derived_fields=("current_context", "server"),
        preview=lambda f: _clip(f"kubeconfig · {_kube_derive(f).get('current_context') or '—'}"),
        summary=lambda f: _host_of(_kube_derive(f).get("server")),
    )
)

_register(
    CredentialKind(
        id="generic",
        label="Generic secret",
        category="other",
        credential_type="other",
        description="Any other secret, e.g. the API key benchmarks send "
        "(BENCHMARK_API_KEY_CREDENTIAL).",
        fields=(
            FieldSpec(
                "key",
                "Key / username",
                help="Optional, non-secret label such as a user name or header name.",
                max_length=255,
            ),
            FieldSpec(
                "value",
                "Secret value",
                input="password",
                secret=True,
                required=True,
                max_length=65536,
            ),
        ),
        storage="value",
        used_for=("Named lookups (benchmarks, integrations)",),
        consumers=("generic",),
        preview=lambda f: mask(f.get("value")),
        summary=lambda f: f.get("key") or None,
    )
)


# Which kinds each picker accepts (consumer id -> kinds). Mirrors ``consumers`` above.
def kinds_for(consumer: str) -> List[str]:
    return [k.id for k in KINDS.values() if consumer in k.consumers]


CLUSTER_TYPE_KINDS: Dict[str, List[str]] = {
    "cloud_aws": ["aws"],
    "cloud_azure": ["azure"],
    "cloud_gcp": ["gcp"],
    "cloud_do": ["digitalocean"],
    "kubernetes": ["kubernetes"],
    "bare_metal": ["ssh_key"],
    "vm": ["ssh_key"],
}
ALERT_CHANNEL_KINDS: Dict[str, List[str]] = {
    "slack": ["slack"],
    "email": ["smtp"],
    "webhook": ["webhook"],
}
MODEL_SOURCE_KINDS: Dict[str, List[str]] = {
    "huggingface": ["huggingface"],
    "modelscope": ["modelscope"],
}


# ---------------------------------------------------------------------------
# validation / storage
# ---------------------------------------------------------------------------


def get_kind(provider: Optional[str]) -> Optional[CredentialKind]:
    return KINDS.get(provider or "")


def is_system(provider: Optional[str], type_: Optional[str], extra: Optional[dict]) -> bool:
    """Managed by GPU Deck itself (internal CA, TLS keys): hidden and read-only in the API."""
    if (provider or "") in SYSTEM_PROVIDERS or str(provider or "").endswith("_tls"):
        return True
    if (type_ or "") in SYSTEM_TYPES:
        return True
    return isinstance(extra, dict) and extra.get("managed_by") == "pki"


def _coerce(spec: FieldSpec, value: Any) -> Any:
    if value is None:
        return None
    if spec.input == "number":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text == "":
                return None
            return int(text) if re.fullmatch(r"-?\d+", text) else text
        return value
    if spec.input == "boolean":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
            if lowered == "":
                return None
        return value
    if isinstance(value, (dict, list)) and spec.input == "json":
        return json.dumps(value)
    if isinstance(value, str):
        text = value if spec.input in ("textarea", "json") else value.strip()
        text = text.strip("\n") if spec.input in ("textarea", "json") else text
        return text if text.strip() else None
    return value


def normalize(kind: CredentialKind, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce inputs (numbers from strings, trimmed text), drop empties, keep unknown keys
    so validation can report them."""
    out: Dict[str, Any] = {}
    for key, value in (raw or {}).items():
        spec = kind.field(key)
        coerced = _coerce(spec, value) if spec else value
        if coerced is not None:
            out[key] = coerced
    if kind.id == "ssh_key" and out.get("public_key"):
        out["public_key"] = str(out["public_key"]).strip()
    return out


def validate(kind: CredentialKind, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Validate against the kind's JSON Schema + semantic checks; fill defaults."""
    data = {k: v for k, v in fields.items() if k not in kind.derived_fields}
    for spec in kind.fields:
        if spec.name not in data and spec.default is not None:
            data[spec.name] = spec.default
    errors: List[Dict[str, str]] = []
    validator = Draft202012Validator(kind.json_schema())
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        name = str(err.path[0]) if err.path else ""
        spec = kind.field(name)
        label = spec.label if spec else name
        if err.validator == "required":
            missing = str(err.message).split("'")[1] if "'" in err.message else ""
            mspec = kind.field(missing)
            errors.append(
                {
                    "field": missing,
                    "message": f"{mspec.label if mspec else missing} " "is required",
                }
            )
        elif err.validator == "additionalProperties":
            extra = sorted(set(data) - {f.name for f in kind.fields})
            errors.append({"field": ",".join(extra), "message": f"unknown field(s) {extra}"})
        elif err.validator == "pattern" and spec is not None:
            errors.append(
                {
                    "field": name,
                    "message": f"{label}: {spec.pattern_hint or 'bad format'}",
                }
            )
        elif err.validator in ("minLength", "maxLength", "minimum", "maximum", "type"):
            errors.append({"field": name, "message": f"{label}: {err.message}"})
        else:
            errors.append({"field": name, "message": f"{label}: {err.message}"})
    if not errors and kind.check:
        for name, message in kind.check(data):
            spec = kind.field(name)
            errors.append({"field": name, "message": f"{spec.label if spec else name} {message}"})
    if errors:
        seen = set()
        unique = []
        for item in errors:
            if item["message"] not in seen:
                seen.add(item["message"])
                unique.append(item)
        raise CredentialValidationError(
            f"Invalid {kind.label}: " + "; ".join(e["message"] for e in unique), unique
        )
    return data


def fields_from_value(kind: CredentialKind, value: str) -> Dict[str, Any]:
    """Legacy ``value`` input: the stored secret for single-secret kinds, a JSON object for
    ``json`` kinds."""
    if kind.storage != "json":
        return {kind.storage: value}
    try:
        data = json.loads(value)
    except ValueError:
        raise CredentialValidationError(
            f"Invalid {kind.label}: value must be a JSON object with "
            f"{', '.join(f.name for f in kind.fields if f.required)}"
        ) from None
    if not isinstance(data, dict):
        raise CredentialValidationError(f"Invalid {kind.label}: value must be a JSON object")
    aliases = {
        "aws_access_key_id": "access_key_id",
        "aws_secret_access_key": "secret_access_key",
    }
    aliases["aws_session_token"] = "session_token"
    return {aliases.get(k, k): v for k, v in data.items()}


def merge(
    kind: CredentialKind, current: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    """Update semantics: secret fields left out (or empty) keep their stored value ("replace"
    only when typed); public fields present in ``incoming`` replace, empty clears."""
    out = {k: v for k, v in current.items() if k not in kind.derived_fields}
    for key, value in (incoming or {}).items():
        spec = kind.field(key)
        if spec is None:
            out[key] = value  # validation reports it
            continue
        coerced = _coerce(spec, value)
        if spec.secret and coerced is None:
            continue
        if coerced is None:
            out.pop(key, None)
        else:
            out[key] = coerced
    return out


@dataclass
class Encoded:
    plaintext: str
    public: Dict[str, Any]
    masked: str
    summary: Optional[str]


def encode(kind: CredentialKind, fields: Dict[str, Any]) -> Encoded:
    """Validated fields -> ciphertext input + public mirror + display strings."""
    if kind.storage == "json":
        plaintext = json.dumps(
            {k: v for k, v in fields.items() if v is not None},
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        plaintext = str(fields[kind.storage])
    public = {k: fields[k] for k in kind.public_fields if fields.get(k) is not None}
    if kind.derive:
        public.update({k: v for k, v in kind.derive(fields).items() if v is not None})
    masked = (kind.preview(fields) if kind.preview else None) or mask(plaintext)
    summary = kind.summary(fields) if kind.summary else None
    return Encoded(plaintext, public, (masked or "****")[:50], _clip(summary, 200))


def decode(
    kind: CredentialKind, plaintext: str, public: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Stored plaintext (+ public mirror) -> the kind's fields (legacy raw values map to the
    kind's primary secret)."""
    base = {k: v for k, v in (public or {}).items() if kind.field(k) and not kind.field(k).secret}
    if kind.storage != "json":
        base[kind.storage] = plaintext
        return base
    try:
        data = json.loads(plaintext)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return {**base, **fields_from_value(kind, plaintext)}
    primary = kind.secret_fields[0] if kind.secret_fields else "value"
    base[primary] = plaintext
    return base


def catalog() -> List[Dict[str, Any]]:
    return [kind.describe() for kind in KINDS.values()]


__all__ = [
    "KINDS",
    "SYSTEM_PROVIDERS",
    "CLUSTER_TYPE_KINDS",
    "ALERT_CHANNEL_KINDS",
    "MODEL_SOURCE_KINDS",
    "CredentialKind",
    "CredentialValidationError",
    "Encoded",
    "FieldSpec",
    "catalog",
    "decode",
    "encode",
    "fields_from_value",
    "get_kind",
    "is_system",
    "kinds_for",
    "mask",
    "merge",
    "normalize",
    "validate",
]
