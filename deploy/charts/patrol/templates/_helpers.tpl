{{/*
Shared snippets ("stamps") reused by every template.
Call with a dict: (dict "root" $ "component" "worker").
*/}}

{{- define "patrol.selectorLabels" -}}
app.kubernetes.io/name: patrol
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "patrol.labels" -}}
{{ include "patrol.selectorLabels" . }}
app.kubernetes.io/part-of: patrol
app.kubernetes.io/version: {{ .root.Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version }}
{{- end }}

{{/* Stops a render in the wrong namespace or without an image tag, with a clear message. */}}
{{- define "patrol.guard" -}}
{{- if ne .Release.Namespace .Values.expectedNamespace }}
{{- fail (printf "Patrol must be installed in namespace %q (its IAM roles only trust that namespace); got %q" .Values.expectedNamespace .Release.Namespace) }}
{{- end }}
{{- end }}

{{- define "patrol.image" -}}
{{- $tag := required "image.tag is required: set it to the Git commit ID printed by ./app/push-images.sh" .root.Values.image.tag -}}
{{ printf "%s/patrol/%s:%s" .root.Values.image.registry .component $tag }}
{{- end }}

{{/* Pod-level security: non-root numeric user from the Dockerfile, default seccomp filter. */}}
{{- define "patrol.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* Container-level security: no privilege escalation, no Linux capabilities, read-only filesystem. */}}
{{- define "patrol.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{- define "patrol.awsEnv" -}}
- name: AWS_REGION
  value: {{ .Values.aws.region | quote }}
- name: PATROL_TABLE_NAME
  value: {{ .Values.aws.tableName | quote }}
{{- end }}

{{/* Restart pods when the site list changes (the status page reads it once at startup). */}}
{{- define "patrol.sitesChecksum" -}}
checksum/sites: {{ toYaml .Values.sites | sha256sum }}
{{- end }}

{{/* Spread copies across zones; prefer, but don't insist, so a lost zone can't block rescheduling. */}}
{{- define "patrol.topologySpread" -}}
- maxSkew: 1
  topologyKey: topology.kubernetes.io/zone
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      {{- include "patrol.selectorLabels" . | nindent 6 }}
- maxSkew: 1
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      {{- include "patrol.selectorLabels" . | nindent 6 }}
{{- end }}

{{/* Scratch space: the root filesystem is read-only, so anything that needs /tmp gets a small scratch folder (on node disk, capped, deleted with the pod). */}}
{{- define "patrol.tmpVolume" -}}
- name: tmp
  emptyDir:
    sizeLimit: 16Mi
{{- end }}
