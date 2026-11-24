/**
 * GET /vault/metadata as the control plane returns it: the typed credential
 * catalog (fields, JSON Schemas), pickers and implicit defaults. Generated from
 * apps/fastapi/app/services/vault/types.py (`kinds.catalog()`) — regenerate when
 * a kind changes (see the vault mock in lib/mocks/handlers/vault.ts).
 */

import type { components } from '@/lib/api/generated/admin';

export const VAULT_METADATA: components['schemas']['VaultMetadata'] = {
  "types": [
    "api_key",
    "token",
    "oauth",
    "service_account",
    "ssh_key",
    "cloud_account",
    "smtp",
    "webhook",
    "tls_key",
    "ca_key",
    "other"
  ],
  "providers": [
    "huggingface",
    "modelscope",
    "aws",
    "azure",
    "gcp",
    "digitalocean",
    "kubernetes",
    "slack",
    "smtp",
    "webhook",
    "ssh_key",
    "container_registry",
    "generic",
    "internal_ca",
    "agent_hub_tls",
    "api_layer_tls"
  ],
  "statuses": [
    "active",
    "expired",
    "revoked"
  ],
  "kinds": [
    {
      "id": "aws",
      "label": "AWS access key",
      "category": "cloud",
      "credential_type": "cloud_account",
      "description": "IAM access key for AWS cloud clusters: browse GPU instance types, provision and stop EC2 GPU VMs.",
      "fields": [
        {
          "name": "access_key_id",
          "label": "Access key ID",
          "input": "text",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": "AKIA…",
          "default": null,
          "pattern": "^[A-Z0-9]{8,128}$",
          "pattern_hint": "Upper-case letters and digits, usually 20 characters (AKIA… / ASIA…)",
          "min_length": null,
          "max_length": 128,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "secret_access_key",
          "label": "Secret access key",
          "input": "password",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": 8,
          "max_length": 256,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "session_token",
          "label": "Session token",
          "input": "textarea",
          "secret": true,
          "required": false,
          "help": "Only for temporary (ASIA…) keys.",
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 4096,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "role_arn",
          "label": "Role to assume",
          "input": "text",
          "secret": false,
          "required": false,
          "help": "Optional: GPU Deck assumes this role with the key above (STS).",
          "placeholder": "arn:aws:iam::123456789012:role/gpu-deck",
          "default": null,
          "pattern": "^arn:aws[a-z-]*:iam::\\d{12}:role/\\S+$",
          "pattern_hint": "arn:aws:iam::<12-digit account>:role/<name>",
          "min_length": null,
          "max_length": 2048,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "external_id",
          "label": "External ID",
          "input": "text",
          "secret": false,
          "required": false,
          "help": "Only when the role's trust policy requires one.",
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 1224,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "region",
          "label": "Default region",
          "input": "text",
          "secret": false,
          "required": false,
          "help": "Informational; each cluster sets the region it launches in.",
          "placeholder": "us-east-1",
          "default": null,
          "pattern": "^[a-z]{2}(-[a-z]+)+-\\d+$",
          "pattern_hint": "An AWS region id such as us-east-1",
          "min_length": null,
          "max_length": 64,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "access_key_id",
        "secret_access_key",
        "session_token"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:aws",
        "title": "AWS access key",
        "type": "object",
        "properties": {
          "access_key_id": {
            "type": "string",
            "maxLength": 128,
            "pattern": "^[A-Z0-9]{8,128}$",
            "title": "Access key ID",
            "writeOnly": true
          },
          "secret_access_key": {
            "type": "string",
            "maxLength": 256,
            "minLength": 8,
            "title": "Secret access key",
            "writeOnly": true
          },
          "session_token": {
            "type": "string",
            "maxLength": 4096,
            "title": "Session token",
            "description": "Only for temporary (ASIA…) keys.",
            "writeOnly": true
          },
          "role_arn": {
            "type": "string",
            "maxLength": 2048,
            "pattern": "^arn:aws[a-z-]*:iam::\\d{12}:role/\\S+$",
            "title": "Role to assume",
            "description": "Optional: GPU Deck assumes this role with the key above (STS)."
          },
          "external_id": {
            "type": "string",
            "maxLength": 1224,
            "title": "External ID",
            "description": "Only when the role's trust policy requires one."
          },
          "region": {
            "type": "string",
            "maxLength": 64,
            "pattern": "^[a-z]{2}(-[a-z]+)+-\\d+$",
            "title": "Default region",
            "description": "Informational; each cluster sets the region it launches in."
          }
        },
        "required": [
          "access_key_id",
          "secret_access_key"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html",
      "used_for": [
        "AWS cloud clusters",
        "Cloud GPU VM provisioning"
      ],
      "consumers": [
        "cluster.cloud_aws",
        "cloud.aws"
      ],
      "cloud_provider": "aws",
      "test": "cloud"
    },
    {
      "id": "azure",
      "label": "Azure service principal",
      "category": "cloud",
      "credential_type": "service_account",
      "description": "Entra ID app registration (client secret) with Contributor on the subscription GPU Deck launches NC/ND VMs in.",
      "fields": [
        {
          "name": "tenant_id",
          "label": "Tenant ID",
          "input": "text",
          "secret": false,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
          "pattern_hint": "A GUID",
          "min_length": null,
          "max_length": 36,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "client_id",
          "label": "Client (application) ID",
          "input": "text",
          "secret": false,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
          "pattern_hint": "A GUID",
          "min_length": null,
          "max_length": 36,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "client_secret",
          "label": "Client secret",
          "input": "password",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": 8,
          "max_length": 1024,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "subscription_id",
          "label": "Subscription ID",
          "input": "text",
          "secret": false,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
          "pattern_hint": "A GUID",
          "min_length": null,
          "max_length": 36,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "client_secret"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:azure",
        "title": "Azure service principal",
        "type": "object",
        "properties": {
          "tenant_id": {
            "type": "string",
            "maxLength": 36,
            "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            "title": "Tenant ID"
          },
          "client_id": {
            "type": "string",
            "maxLength": 36,
            "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            "title": "Client (application) ID"
          },
          "client_secret": {
            "type": "string",
            "maxLength": 1024,
            "minLength": 8,
            "title": "Client secret",
            "writeOnly": true
          },
          "subscription_id": {
            "type": "string",
            "maxLength": 36,
            "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            "title": "Subscription ID"
          }
        },
        "required": [
          "tenant_id",
          "client_id",
          "client_secret",
          "subscription_id"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal",
      "used_for": [
        "Azure cloud clusters",
        "Cloud GPU VM provisioning"
      ],
      "consumers": [
        "cluster.cloud_azure",
        "cloud.azure"
      ],
      "cloud_provider": "azure",
      "test": "cloud"
    },
    {
      "id": "gcp",
      "label": "Google Cloud service account",
      "category": "cloud",
      "credential_type": "service_account",
      "description": "Service account JSON key with Compute Admin on the project GPU Deck launches GPU VMs in. The project comes from the key.",
      "fields": [
        {
          "name": "service_account_json",
          "label": "Service account key (JSON)",
          "input": "json",
          "secret": true,
          "required": true,
          "help": "Upload the key file from IAM → Service accounts → Keys, or paste it.",
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 16384,
          "minimum": null,
          "maximum": null,
          "upload_accept": ".json,application/json"
        }
      ],
      "secret_fields": [
        "service_account_json"
      ],
      "derived_fields": [
        "project_id",
        "client_email",
        "private_key_id"
      ],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:gcp",
        "title": "Google Cloud service account",
        "type": "object",
        "properties": {
          "service_account_json": {
            "type": "string",
            "maxLength": 16384,
            "title": "Service account key (JSON)",
            "description": "Upload the key file from IAM → Service accounts → Keys, or paste it.",
            "writeOnly": true
          }
        },
        "required": [
          "service_account_json"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://cloud.google.com/iam/docs/keys-create-delete",
      "used_for": [
        "Google Cloud clusters",
        "Cloud GPU VM provisioning"
      ],
      "consumers": [
        "cluster.cloud_gcp",
        "cloud.gcp"
      ],
      "cloud_provider": "gcp",
      "test": "cloud"
    },
    {
      "id": "digitalocean",
      "label": "DigitalOcean API token",
      "category": "cloud",
      "credential_type": "token",
      "description": "Personal access token with read + write scope for GPU Droplets.",
      "fields": [
        {
          "name": "api_token",
          "label": "API token",
          "input": "password",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": "dop_v1_…",
          "default": null,
          "pattern": "^\\S+$",
          "pattern_hint": "No spaces",
          "min_length": 20,
          "max_length": 512,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "api_token"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:digitalocean",
        "title": "DigitalOcean API token",
        "type": "object",
        "properties": {
          "api_token": {
            "type": "string",
            "maxLength": 512,
            "minLength": 20,
            "pattern": "^\\S+$",
            "title": "API token",
            "writeOnly": true
          }
        },
        "required": [
          "api_token"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://docs.digitalocean.com/reference/api/create-personal-access-token/",
      "used_for": [
        "DigitalOcean cloud clusters",
        "Cloud GPU VM provisioning"
      ],
      "consumers": [
        "cluster.cloud_do",
        "cloud.digitalocean"
      ],
      "cloud_provider": "digitalocean",
      "test": "cloud"
    },
    {
      "id": "huggingface",
      "label": "Hugging Face token",
      "category": "model_source",
      "credential_type": "token",
      "description": "Read token for gated or private repos: catalog search, file listings and the downloads agents make for that model.",
      "fields": [
        {
          "name": "token",
          "label": "Access token",
          "input": "password",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": "hf_…",
          "default": null,
          "pattern": "^\\S+$",
          "pattern_hint": "No spaces",
          "min_length": 8,
          "max_length": 512,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "token"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:huggingface",
        "title": "Hugging Face token",
        "type": "object",
        "properties": {
          "token": {
            "type": "string",
            "maxLength": 512,
            "minLength": 8,
            "pattern": "^\\S+$",
            "title": "Access token",
            "writeOnly": true
          }
        },
        "required": [
          "token"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://huggingface.co/docs/hub/security-tokens",
      "used_for": [
        "Catalog search and registration",
        "Model downloads on nodes"
      ],
      "consumers": [
        "catalog.huggingface",
        "model_cache.huggingface"
      ],
      "cloud_provider": null,
      "test": "format"
    },
    {
      "id": "modelscope",
      "label": "ModelScope token",
      "category": "model_source",
      "credential_type": "token",
      "description": "SDK token for private ModelScope repos: catalog search, file listings and node downloads.",
      "fields": [
        {
          "name": "token",
          "label": "SDK token",
          "input": "password",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": "^\\S+$",
          "pattern_hint": "No spaces",
          "min_length": 8,
          "max_length": 512,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "token"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:modelscope",
        "title": "ModelScope token",
        "type": "object",
        "properties": {
          "token": {
            "type": "string",
            "maxLength": 512,
            "minLength": 8,
            "pattern": "^\\S+$",
            "title": "SDK token",
            "writeOnly": true
          }
        },
        "required": [
          "token"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://modelscope.cn/my/myaccesstoken",
      "used_for": [
        "Catalog search and registration",
        "Model downloads on nodes"
      ],
      "consumers": [
        "catalog.modelscope",
        "model_cache.modelscope"
      ],
      "cloud_provider": null,
      "test": "format"
    },
    {
      "id": "slack",
      "label": "Slack incoming webhook",
      "category": "notification",
      "credential_type": "webhook",
      "description": "Incoming-webhook URL of the Slack app that posts GPU Deck alerts.",
      "fields": [
        {
          "name": "webhook_url",
          "label": "Webhook URL",
          "input": "password",
          "secret": true,
          "required": true,
          "help": "Slack app incoming webhook; Mattermost and Rocket.Chat incoming webhooks work too.",
          "placeholder": "https://hooks.slack.com/services/…",
          "default": null,
          "pattern": "^https?://\\S+$",
          "pattern_hint": "An http(s) URL (https://hooks.slack.com/… for Slack)",
          "min_length": null,
          "max_length": 2048,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "webhook_url"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:slack",
        "title": "Slack incoming webhook",
        "type": "object",
        "properties": {
          "webhook_url": {
            "type": "string",
            "maxLength": 2048,
            "pattern": "^https?://\\S+$",
            "title": "Webhook URL",
            "description": "Slack app incoming webhook; Mattermost and Rocket.Chat incoming webhooks work too.",
            "writeOnly": true
          }
        },
        "required": [
          "webhook_url"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://api.slack.com/messaging/webhooks",
      "used_for": [
        "Slack alert channels"
      ],
      "consumers": [
        "alert_channel.slack"
      ],
      "cloud_provider": null,
      "test": "format"
    },
    {
      "id": "smtp",
      "label": "SMTP server",
      "category": "notification",
      "credential_type": "smtp",
      "description": "Mail server login for email alert channels. Recipients are set on each channel.",
      "fields": [
        {
          "name": "host",
          "label": "Host",
          "input": "text",
          "secret": false,
          "required": true,
          "help": null,
          "placeholder": "smtp.office365.com",
          "default": null,
          "pattern": "^[A-Za-z0-9.-]+$",
          "pattern_hint": "A host name or IP address",
          "min_length": null,
          "max_length": 255,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "port",
          "label": "Port",
          "input": "number",
          "secret": false,
          "required": false,
          "help": null,
          "placeholder": null,
          "default": 587,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 4096,
          "minimum": 1,
          "maximum": 65535,
          "upload_accept": null
        },
        {
          "name": "username",
          "label": "Username",
          "input": "text",
          "secret": false,
          "required": false,
          "help": null,
          "placeholder": "alerts@example.com",
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 320,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "password",
          "label": "Password",
          "input": "password",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 1024,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "from",
          "label": "From address",
          "input": "email",
          "secret": false,
          "required": false,
          "help": null,
          "placeholder": "gpudeck@example.com",
          "default": null,
          "pattern": "^[^@\\s]+@[^@\\s]+$",
          "pattern_hint": "An email address",
          "min_length": null,
          "max_length": 320,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "starttls",
          "label": "STARTTLS",
          "input": "boolean",
          "secret": false,
          "required": false,
          "help": "Implicit TLS is used on port 465.",
          "placeholder": null,
          "default": true,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 4096,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "password"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:smtp",
        "title": "SMTP server",
        "type": "object",
        "properties": {
          "host": {
            "type": "string",
            "maxLength": 255,
            "pattern": "^[A-Za-z0-9.-]+$",
            "title": "Host"
          },
          "port": {
            "type": "integer",
            "minimum": 1,
            "maximum": 65535,
            "title": "Port",
            "default": 587
          },
          "username": {
            "type": "string",
            "maxLength": 320,
            "title": "Username"
          },
          "password": {
            "type": "string",
            "maxLength": 1024,
            "title": "Password",
            "writeOnly": true
          },
          "from": {
            "type": "string",
            "maxLength": 320,
            "pattern": "^[^@\\s]+@[^@\\s]+$",
            "title": "From address"
          },
          "starttls": {
            "type": "boolean",
            "title": "STARTTLS",
            "description": "Implicit TLS is used on port 465.",
            "default": true
          }
        },
        "required": [
          "host",
          "password"
        ],
        "additionalProperties": false
      },
      "docs_url": null,
      "used_for": [
        "Email alert channels"
      ],
      "consumers": [
        "alert_channel.email"
      ],
      "cloud_provider": null,
      "test": "format"
    },
    {
      "id": "webhook",
      "label": "Signed webhook",
      "category": "notification",
      "credential_type": "webhook",
      "description": "Destination URL plus the HMAC-SHA256 secret that signs every payload (X-GPUDeck-Signature).",
      "fields": [
        {
          "name": "url",
          "label": "URL",
          "input": "url",
          "secret": false,
          "required": true,
          "help": null,
          "placeholder": "https://hooks.example.com/gpu-deck",
          "default": null,
          "pattern": "^https?://\\S+$",
          "pattern_hint": "An http(s) URL",
          "min_length": null,
          "max_length": 2048,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "hmac_secret",
          "label": "Signing secret",
          "input": "password",
          "secret": true,
          "required": true,
          "help": "Receivers recompute HMAC-SHA256(secret, timestamp + '.' + body).",
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": 16,
          "max_length": 1024,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "hmac_secret"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:webhook",
        "title": "Signed webhook",
        "type": "object",
        "properties": {
          "url": {
            "type": "string",
            "maxLength": 2048,
            "pattern": "^https?://\\S+$",
            "title": "URL"
          },
          "hmac_secret": {
            "type": "string",
            "maxLength": 1024,
            "minLength": 16,
            "title": "Signing secret",
            "description": "Receivers recompute HMAC-SHA256(secret, timestamp + '.' + body).",
            "writeOnly": true
          }
        },
        "required": [
          "url",
          "hmac_secret"
        ],
        "additionalProperties": false
      },
      "docs_url": null,
      "used_for": [
        "Webhook alert channels"
      ],
      "consumers": [
        "alert_channel.webhook"
      ],
      "cloud_provider": null,
      "test": "format"
    },
    {
      "id": "ssh_key",
      "label": "SSH public key",
      "category": "access",
      "credential_type": "ssh_key",
      "description": "An OpenSSH public key. GPU Deck only ever needs the public half: it is added to SSH GPU instances and to cloud VMs (Azure, Google Cloud).",
      "fields": [
        {
          "name": "public_key",
          "label": "Public key",
          "input": "textarea",
          "secret": false,
          "required": true,
          "help": "One line from a .pub file. Private keys are refused.",
          "placeholder": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA… you@laptop",
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 16384,
          "minimum": null,
          "maximum": null,
          "upload_accept": ".pub,text/plain"
        }
      ],
      "secret_fields": [],
      "derived_fields": [
        "key_type",
        "fingerprint",
        "comment"
      ],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:ssh_key",
        "title": "SSH public key",
        "type": "object",
        "properties": {
          "public_key": {
            "type": "string",
            "maxLength": 16384,
            "title": "Public key",
            "description": "One line from a .pub file. Private keys are refused."
          }
        },
        "required": [
          "public_key"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://man.openbsd.org/ssh-keygen",
      "used_for": [
        "Cluster install helper",
        "SSH GPU instances",
        "Cloud VM login"
      ],
      "consumers": [
        "cluster.bare_metal",
        "cluster.vm",
        "cluster.ssh_key",
        "gpu_instance.authorized_keys"
      ],
      "cloud_provider": null,
      "test": "format"
    },
    {
      "id": "container_registry",
      "label": "Container registry",
      "category": "access",
      "credential_type": "token",
      "description": "Login for a private image registry (docker login).",
      "fields": [
        {
          "name": "registry",
          "label": "Registry",
          "input": "text",
          "secret": false,
          "required": true,
          "help": null,
          "placeholder": "ghcr.io",
          "default": null,
          "pattern": "^[A-Za-z0-9.-]+(:\\d+)?(/\\S*)?$",
          "pattern_hint": "A registry host such as ghcr.io or registry.example.com:5000",
          "min_length": null,
          "max_length": 255,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "username",
          "label": "Username",
          "input": "text",
          "secret": false,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 255,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "password",
          "label": "Password or token",
          "input": "password",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 4096,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "password"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:container_registry",
        "title": "Container registry",
        "type": "object",
        "properties": {
          "registry": {
            "type": "string",
            "maxLength": 255,
            "pattern": "^[A-Za-z0-9.-]+(:\\d+)?(/\\S*)?$",
            "title": "Registry"
          },
          "username": {
            "type": "string",
            "maxLength": 255,
            "title": "Username"
          },
          "password": {
            "type": "string",
            "maxLength": 4096,
            "title": "Password or token",
            "writeOnly": true
          }
        },
        "required": [
          "registry",
          "username",
          "password"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://docs.docker.com/reference/cli/docker/login/",
      "used_for": [
        "Private engine / instance images"
      ],
      "consumers": [
        "registry"
      ],
      "cloud_provider": null,
      "test": "format"
    },
    {
      "id": "kubernetes",
      "label": "Kubeconfig",
      "category": "access",
      "credential_type": "service_account",
      "description": "Kubeconfig for a Kubernetes cluster that runs the gpudeck-agent DaemonSet.",
      "fields": [
        {
          "name": "kubeconfig",
          "label": "Kubeconfig",
          "input": "textarea",
          "secret": true,
          "required": true,
          "help": "Upload ~/.kube/config or paste it (YAML).",
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 65536,
          "minimum": null,
          "maximum": null,
          "upload_accept": ".yaml,.yml,.conf,.config,text/yaml,text/plain"
        }
      ],
      "secret_fields": [
        "kubeconfig"
      ],
      "derived_fields": [
        "current_context",
        "server"
      ],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:kubernetes",
        "title": "Kubeconfig",
        "type": "object",
        "properties": {
          "kubeconfig": {
            "type": "string",
            "maxLength": 65536,
            "title": "Kubeconfig",
            "description": "Upload ~/.kube/config or paste it (YAML).",
            "writeOnly": true
          }
        },
        "required": [
          "kubeconfig"
        ],
        "additionalProperties": false
      },
      "docs_url": "https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/",
      "used_for": [
        "Kubernetes clusters"
      ],
      "consumers": [
        "cluster.kubernetes"
      ],
      "cloud_provider": null,
      "test": "format"
    },
    {
      "id": "generic",
      "label": "Generic secret",
      "category": "other",
      "credential_type": "other",
      "description": "Any other secret, e.g. the API key benchmarks send (BENCHMARK_API_KEY_CREDENTIAL).",
      "fields": [
        {
          "name": "key",
          "label": "Key / username",
          "input": "text",
          "secret": false,
          "required": false,
          "help": "Optional, non-secret label such as a user name or header name.",
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 255,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        },
        {
          "name": "value",
          "label": "Secret value",
          "input": "password",
          "secret": true,
          "required": true,
          "help": null,
          "placeholder": null,
          "default": null,
          "pattern": null,
          "pattern_hint": null,
          "min_length": null,
          "max_length": 65536,
          "minimum": null,
          "maximum": null,
          "upload_accept": null
        }
      ],
      "secret_fields": [
        "value"
      ],
      "derived_fields": [],
      "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "gpu-deck:credential:generic",
        "title": "Generic secret",
        "type": "object",
        "properties": {
          "key": {
            "type": "string",
            "maxLength": 255,
            "title": "Key / username",
            "description": "Optional, non-secret label such as a user name or header name."
          },
          "value": {
            "type": "string",
            "maxLength": 65536,
            "title": "Secret value",
            "writeOnly": true
          }
        },
        "required": [
          "value"
        ],
        "additionalProperties": false
      },
      "docs_url": null,
      "used_for": [
        "Named lookups (benchmarks, integrations)"
      ],
      "consumers": [
        "generic"
      ],
      "cloud_provider": null,
      "test": "format"
    }
  ],
  "categories": [
    {
      "id": "cloud",
      "label": "Cloud accounts"
    },
    {
      "id": "model_source",
      "label": "Model sources"
    },
    {
      "id": "notification",
      "label": "Notifications"
    },
    {
      "id": "access",
      "label": "Access"
    },
    {
      "id": "other",
      "label": "Other"
    }
  ],
  "pickers": {
    "cluster.cloud_aws": [
      "aws"
    ],
    "cluster.cloud_azure": [
      "azure"
    ],
    "cluster.cloud_gcp": [
      "gcp"
    ],
    "cluster.cloud_do": [
      "digitalocean"
    ],
    "cluster.kubernetes": [
      "kubernetes"
    ],
    "cluster.bare_metal": [
      "ssh_key"
    ],
    "cluster.vm": [
      "ssh_key"
    ],
    "cluster.ssh_key": [
      "ssh_key"
    ],
    "alert_channel.slack": [
      "slack"
    ],
    "alert_channel.email": [
      "smtp"
    ],
    "alert_channel.webhook": [
      "webhook"
    ],
    "model.huggingface": [
      "huggingface"
    ],
    "model.modelscope": [
      "modelscope"
    ],
    "gpu_instance.authorized_keys": [
      "ssh_key"
    ]
  },
  "defaults": [
    {
      "setting": "HF_TOKEN_CREDENTIAL",
      "name": "huggingface-token",
      "used_for": "Hugging Face downloads (default token)",
      "kind": "huggingface"
    },
    {
      "setting": "MODELSCOPE_TOKEN_CREDENTIAL",
      "name": "modelscope-token",
      "used_for": "ModelScope downloads (default token)",
      "kind": "modelscope"
    },
    {
      "setting": "SMTP_PASSWORD_CREDENTIAL",
      "name": "smtp-password",
      "used_for": "Email alerts (default SMTP password)",
      "kind": null
    },
    {
      "setting": "SLACK_DEFAULT_WEBHOOK_CREDENTIAL",
      "name": null,
      "used_for": "Slack alerts (default webhook)",
      "kind": null
    },
    {
      "setting": "WEBHOOK_SIGNING_SECRET_CREDENTIAL",
      "name": null,
      "used_for": "Webhook alerts (default signing secret)",
      "kind": null
    },
    {
      "setting": "BENCHMARK_API_KEY_CREDENTIAL",
      "name": "benchmark-api-key",
      "used_for": "Benchmark runner API key",
      "kind": null
    }
  ]
};
