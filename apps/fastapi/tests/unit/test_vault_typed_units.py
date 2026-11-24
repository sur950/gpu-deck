"""
Typed vault credentials (app/services/vault/types.py): every kind's JSON Schema
is valid, inputs are normalised / validated, the storage layout matches what
each consumer reads (cloud providers, catalog, alerts), update merges keep
secrets, and system credentials are recognised.
"""

import json
from types import SimpleNamespace

import pytest
from app.services.vault import types as kinds
from app.services.vault.service import CREDENTIAL_TYPES, compatible
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.unit

GCP_KEY = {
    "type": "service_account",
    "project_id": "gpu-proj",
    "private_key_id": "0123456789abcdef",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n",
    "client_email": "gpu-deck@gpu-proj.iam.gserviceaccount.com",
}
KUBECONFIG = """apiVersion: v1
kind: Config
clusters:
- name: prod
  cluster: {server: "https://10.0.0.1:6443"}
contexts:
- name: prod-admin
  context: {cluster: prod, user: admin}
current-context: prod-admin
users:
- name: admin
  user: {token: abc}
"""


def ssh_public_key(comment: str = "me@laptop") -> str:
    key = ed25519.Ed25519PrivateKey.generate().public_key()
    line = key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    return f"{line.decode()} {comment}"


VALID = {
    "aws": {
        "access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "secret_access_key": "wJalrXUtnFEMI/K7MDENG",
    },
    "azure": {
        "tenant_id": "11111111-2222-3333-4444-555555555555",
        "client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "client_secret": "s3cr3t-value",
        "subscription_id": "99999999-8888-7777-6666-555555555555",
    },
    "gcp": {"service_account_json": json.dumps(GCP_KEY)},
    "digitalocean": {"api_token": "dop_v1_" + "a" * 64},
    "huggingface": {"token": "hf_abcdefghijklmnop"},
    "modelscope": {"token": "ms-abcdefghijklmnop"},
    "slack": {"webhook_url": "https://hooks.slack.com/services/T0/B0/xyz"},
    "smtp": {"host": "smtp.example.com", "password": "mail-pass"},
    "webhook": {
        "url": "https://hooks.example.com/gd",
        "hmac_secret": "0123456789abcdef0123",
    },
    "ssh_key": {"public_key": ssh_public_key()},
    "container_registry": {
        "registry": "ghcr.io",
        "username": "bot",
        "password": "ghp_xyz",
    },
    "kubernetes": {"kubeconfig": KUBECONFIG},
    "generic": {"value": "anything"},
}


def test_every_kind_has_a_valid_json_schema_and_a_fixture():
    assert set(VALID) == set(kinds.KINDS)
    for kind in kinds.KINDS.values():
        Draft202012Validator.check_schema(kind.json_schema())
        info = kind.describe()
        assert info["json_schema"]["properties"].keys() == {f.name for f in kind.fields}
        assert kind.storage == "json" or kind.field(kind.storage) is not None
        assert kind.credential_type in CREDENTIAL_TYPES


@pytest.mark.parametrize("kind_id", sorted(VALID))
def test_valid_inputs_round_trip(kind_id):
    kind = kinds.KINDS[kind_id]
    fields = kinds.validate(kind, kinds.normalize(kind, VALID[kind_id]))
    enc = kinds.encode(kind, fields)
    assert len(enc.masked) <= 50
    for name in kind.secret_fields:
        assert name not in enc.public  # secrets never mirrored in plaintext metadata
    decoded = kinds.decode(kind, enc.plaintext, enc.public)
    for name, value in fields.items():
        assert decoded[name] == value


def test_storage_layout_matches_consumers():
    from app.services.cloud.credentials import parse_secret

    for kind_id in ("aws", "azure", "gcp", "digitalocean"):
        kind = kinds.KINDS[kind_id]
        enc = kinds.encode(kind, kinds.validate(kind, kinds.normalize(kind, VALID[kind_id])))
        parsed = parse_secret(kind_id, enc.plaintext)  # what the cloud provider reads
        assert parsed
    hf = kinds.KINDS["huggingface"]
    assert kinds.encode(hf, VALID["huggingface"]).plaintext == VALID["huggingface"]["token"]
    slack = kinds.KINDS["slack"]
    assert kinds.encode(slack, VALID["slack"]).plaintext.startswith("https://hooks.slack.com/")


@pytest.mark.parametrize(
    "kind_id,fields,message",
    [
        (
            "aws",
            {"access_key_id": "AKIAIOSFODNN7EXAMPLE"},
            "Secret access key is required",
        ),
        ("aws", {**VALID["aws"], "role_arn": "nope"}, "arn:aws:iam"),
        ("azure", {**VALID["azure"], "tenant_id": "x"}, "GUID"),
        ("gcp", {"service_account_json": "{not json"}, "not valid JSON"),
        (
            "gcp",
            {"service_account_json": json.dumps({"type": "user"})},
            "service_account",
        ),
        (
            "ssh_key",
            {"public_key": "-----BEGIN OPENSSH PRIVATE KEY-----"},
            "private key",
        ),
        ("ssh_key", {"public_key": "ssh-rsa notbase64!"}, "Public key"),
        ("smtp", {"host": "smtp.x.com", "password": "p", "port": 70000}, "Port"),
        ("webhook", {"url": "ftp://x", "hmac_secret": "0123456789abcdef"}, "http(s)"),
        ("kubernetes", {"kubeconfig": "just: text"}, "no clusters"),
        ("huggingface", {"token": "hf bad"}, "No spaces"),
        ("generic", {"value": "x", "extra": 1}, "unknown field"),
    ],
)
def test_invalid_inputs_are_rejected(kind_id, fields, message):
    kind = kinds.KINDS[kind_id]
    with pytest.raises(kinds.CredentialValidationError) as err:
        kinds.validate(kind, kinds.normalize(kind, fields))
    assert err.value.error_code == "CREDENTIAL_INVALID"
    assert message in str(err.value.detail)
    assert err.value.errors


def test_normalize_coerces_form_strings():
    smtp = kinds.KINDS["smtp"]
    out = kinds.normalize(
        smtp, {"host": " smtp.x.com ", "port": "465", "starttls": "false", "from": ""}
    )
    assert out == {"host": "smtp.x.com", "port": 465, "starttls": False}
    fields = kinds.validate(smtp, {**out, "password": "pw"})
    assert fields["port"] == 465 and fields["starttls"] is False
    defaults = kinds.validate(smtp, {"host": "h", "password": "pw"})
    assert defaults["port"] == 587 and defaults["starttls"] is True
    gcp = kinds.KINDS["gcp"]
    assert json.loads(
        kinds.normalize(gcp, {"service_account_json": GCP_KEY})["service_account_json"]
    )


def test_merge_keeps_secret_fields_left_out_and_clears_public_ones():
    aws = kinds.KINDS["aws"]
    current = {
        **VALID["aws"],
        "region": "us-east-1",
        "role_arn": "arn:aws:iam::123456789012:role/x",
    }
    merged = kinds.merge(aws, current, {"secret_access_key": "", "region": ""})
    assert merged["secret_access_key"] == VALID["aws"]["secret_access_key"]
    assert "region" not in merged and merged["role_arn"].endswith("/x")
    replaced = kinds.merge(aws, current, {"secret_access_key": "new-secret-value"})
    assert replaced["secret_access_key"] == "new-secret-value"


def test_derived_public_facts():
    gcp = kinds.KINDS["gcp"]
    enc = kinds.encode(gcp, kinds.validate(gcp, VALID["gcp"]))
    assert enc.public["project_id"] == "gpu-proj" and enc.summary == "project gpu-proj"
    assert "PRIVATE KEY" not in json.dumps(enc.public)
    ssh = kinds.KINDS["ssh_key"]
    enc = kinds.encode(ssh, kinds.validate(ssh, VALID["ssh_key"]))
    assert enc.public["fingerprint"].startswith("SHA256:") and enc.public["comment"] == "me@laptop"
    assert enc.public["public_key"].startswith("ssh-ed25519 ")
    kube = kinds.KINDS["kubernetes"]
    enc = kinds.encode(kube, kinds.validate(kube, VALID["kubernetes"]))
    assert enc.public == {
        "current_context": "prod-admin",
        "server": "https://10.0.0.1:6443",
    }
    assert "token" not in json.dumps(enc.public)


def test_legacy_value_input():
    aws = kinds.KINDS["aws"]
    raw = json.dumps(
        {"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE", "aws_secret_access_key": "x" * 20}
    )
    assert kinds.fields_from_value(aws, raw)["access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
    with pytest.raises(kinds.CredentialValidationError):
        kinds.fields_from_value(aws, "not-json")
    hf = kinds.KINDS["huggingface"]
    assert kinds.fields_from_value(hf, "hf_x") == {"token": "hf_x"}
    # A legacy raw value stored for a JSON kind decodes into its first secret field.
    assert kinds.decode(aws, "plain", {})["access_key_id"] == "plain"


def test_system_credentials_and_compatibility():
    assert kinds.is_system("internal_ca", "ca_key", {})
    assert kinds.is_system("agent_hub_tls", "tls_key", {})
    assert kinds.is_system(None, "other", {"managed_by": "pki"})
    assert not kinds.is_system("aws", "cloud_account", {})

    def row(provider, type_):
        return SimpleNamespace(provider=provider, type=type_, extra_data={})

    assert compatible(row("aws", "cloud_account"), ["aws"])
    assert not compatible(row("azure", "service_account"), ["aws"])
    assert not compatible(row("generic", "other"), ["slack"])
    assert compatible(row(None, "cloud_account"), ["aws"])  # untyped legacy, same category
    assert compatible(row("alerts", "webhook"), ["slack"])  # old inline alert secret
    assert not compatible(row("internal_ca", "ca_key"), ["aws"])
    assert kinds.CLUSTER_TYPE_KINDS["cloud_gcp"] == ["gcp"]
    assert kinds.kinds_for("alert_channel.email") == ["smtp"]


def test_catalog_metadata_shape():
    from app.schemas.vault_schema import CredentialKindInfo

    for info in kinds.catalog():
        model = CredentialKindInfo.model_validate(info)
        assert model.test in ("format", "cloud")
        assert (model.cloud_provider is not None) == (model.category == "cloud")


def test_alert_credential_config_mapping():
    from app.services.alerts.service import AlertService, credential_config

    cfg = credential_config(
        "email", {"host": "smtp.x.com", "port": 465, "username": "u", "starttls": False}
    )
    assert cfg == {"host": "smtp.x.com", "port": 465, "username": "u", "use_tls": False}
    assert credential_config("webhook", {"url": "https://h.example/x"}) == {
        "url": "https://h.example/x"
    }
    assert credential_config("slack", {"anything": 1}) == {}
    # The SMTP host / webhook URL may come from the credential instead of the channel config.
    out = AlertService._validate_config(
        "email", {"recipients": "a@x.com", "smtp_host": "legacy.x.com"}, True
    )
    assert out["host"] == "legacy.x.com" and "smtp_host" not in out
    ok = AlertService._validate_config("webhook", {}, True, {"url": "https://10.0.0.5/hook"})
    assert ok == {}
