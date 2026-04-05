{{- define "infrawatch-agent.name" -}}
{{- "infrawatch-agent" }}
{{- end }}

{{- define "infrawatch-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.name }}
{{- .Values.serviceAccount.name }}
{{- else }}
{{- "infrawatch-agent" }}
{{- end }}
{{- end }}
