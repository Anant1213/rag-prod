{{- define "rag-chatbot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rag-chatbot.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "rag-chatbot.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rag-chatbot.labels" -}}
app.kubernetes.io/name: {{ include "rag-chatbot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "rag-chatbot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "rag-chatbot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
