{{/* Common names and labels. */}}
{{- define "gpudeck.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "gpudeck.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "gpudeck.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "gpudeck.labels" -}}
helm.sh/chart: {{ include "gpudeck.chart" . }}
app.kubernetes.io/name: {{ include "gpudeck.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: gpu-deck
{{- end -}}

{{- define "gpudeck.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gpudeck.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* kebab-case component name: adminApi -> admin-api */}}
{{- define "gpudeck.componentName" -}}
{{- regexReplaceAll "([a-z0-9])([A-Z])" . "${1}-${2}" | lower -}}
{{- end -}}

{{- define "gpudeck.componentSelector" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
{{ include "gpudeck.selectorLabels" $root }}
app.kubernetes.io/component: {{ $component }}
{{- end -}}

{{- define "gpudeck.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "gpudeck.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "gpudeck.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-env" (include "gpudeck.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* image reference: registry/repository:tag or @digest. Args: (list root imageValues) */}}
{{- define "gpudeck.image" -}}
{{- $root := index . 0 -}}
{{- $img := index . 1 -}}
{{- $registry := $root.Values.global.imageRegistry -}}
{{- $repo := ternary (printf "%s/%s" $registry $img.repository) $img.repository (ne $registry "") -}}
{{- if $img.digest -}}
{{- printf "%s@%s" $repo $img.digest -}}
{{- else -}}
{{- printf "%s:%s" $repo (default $root.Chart.AppVersion $root.Values.global.imageTag) -}}
{{- end -}}
{{- end -}}

{{- define "gpudeck.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Validate required settings early with readable errors. */}}
{{- define "gpudeck.validate" -}}
{{- if and (not .Values.secrets.existingSecret) (not .Values.secrets.create) -}}
{{- fail "set secrets.existingSecret (SECRET_KEY, VAULT_KEYS, ADMIN_USERNAME, ADMIN_PASSWORD, DATABASE_URL, REDIS_URL) or secrets.create=true for a throwaway install" -}}
{{- end -}}
{{- if and .Values.nodeAgent.enabled (not .Values.nodeAgent.joinTokenSecret) -}}
{{- fail "nodeAgent.enabled requires nodeAgent.joinTokenSecret (Secret with GPUDECK_AGENT_JOIN_TOKEN)" -}}
{{- end -}}
{{- end -}}

{{/* map -> "k=v,k2=v2" (sorted) */}}
{{- define "gpudeck.kvList" -}}
{{- $out := list -}}
{{- range $k, $v := . -}}
{{- $out = append $out (printf "%s=%s" $k $v) -}}
{{- end -}}
{{- join "," $out -}}
{{- end -}}
