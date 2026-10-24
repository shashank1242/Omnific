{{/* Project id (placeholder PROJECT_ID by default; set global.project). */}}
{{- define "omnific.project" -}}
{{- .Values.global.project | required "global.project is required" -}}
{{- end -}}

{{- define "omnific.registry" -}}
{{- if .Values.global.registry -}}
{{- .Values.global.registry -}}
{{- else -}}
{{- printf "%s-docker.pkg.dev/%s/omnific" .Values.global.region (include "omnific.project" .) -}}
{{- end -}}
{{- end -}}

{{- define "omnific.bucket" -}}
{{- default (printf "%s-omnific-artifacts" (include "omnific.project" .)) .Values.artifacts.bucket -}}
{{- end -}}

{{- define "omnific.gsa" -}}
{{- default (printf "omnific-artifacts@%s.iam.gserviceaccount.com" (include "omnific.project" .)) .Values.artifacts.googleServiceAccount -}}
{{- end -}}

{{- define "omnific.runnerImage" -}}
{{- default (printf "%s/sandbox-runner:%s" (include "omnific.registry" .) .Values.global.imageTag) .Values.sandboxRuns.image -}}
{{- end -}}

{{/* Common labels. Call with (dict "name" $name "root" $) */}}
{{- define "omnific.labels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/part-of: omnific
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | quote }}
{{- end -}}

{{- define "omnific.selectorLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}

{{/* True (non-empty) when the service needs egress to port 443 */}}
{{- define "omnific.needs443" -}}
{{- if or .svc.needsOpenAI .svc.needsArtifacts .svc.external443 -}}true{{- end -}}
{{- end -}}

{{/* Namespace PodSecurity + name labels. Call with (dict "ns" name "root" $) */}}
{{- define "omnific.namespace" -}}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .ns }}
  labels:
    app.kubernetes.io/part-of: omnific
    {{- if .root.Values.podSecurity.labelNamespaces }}
    pod-security.kubernetes.io/enforce: restricted
    {{- end }}
{{- end -}}
