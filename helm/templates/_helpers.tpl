{{- define "pyxis-agent.name" -}}
{{- "pyxis-agent" }}
{{- end }}

{{- define "pyxis-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.name }}
{{- .Values.serviceAccount.name }}
{{- else }}
{{- "pyxis-agent" }}
{{- end }}
{{- end }}
