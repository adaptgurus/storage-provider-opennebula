{{/*
CSI identity and kubelet path helpers.

The legacy chart default remains csi.opennebula.io for backward compatibility.
LayerSentry sets driver.name=csi.layersentry.io in its qualified profile. Every
identity-sensitive rendered resource must consume these helpers rather than
hard-coding either identity.
*/}}
{{- define "opennebula-csi.driverName" -}}
{{- $driver := (get .Values "driver") | default dict -}}
{{- $name := (get $driver "name") | default "csi.opennebula.io" -}}
{{- required "driver.name must not be empty" (trim (printf "%v" $name)) -}}
{{- end -}}

{{- define "opennebula-csi.kubeletRootDir" -}}
{{- $kubelet := (get .Values "kubelet") | default dict -}}
{{- $root := (get $kubelet "rootDir") | default "/var/lib/kubelet" -}}
{{- trimSuffix "/" (required "kubelet.rootDir must not be empty" (trim (printf "%v" $root))) -}}
{{- end -}}

{{- define "opennebula-csi.kubeletPluginDir" -}}
{{- printf "%s/plugins/%s" (include "opennebula-csi.kubeletRootDir" .) (include "opennebula-csi.driverName" .) -}}
{{- end -}}

{{- define "opennebula-csi.kubeletRegistrationDir" -}}
{{- printf "%s/plugins_registry" (include "opennebula-csi.kubeletRootDir" .) -}}
{{- end -}}

{{/* Reject a second identity source hidden in extraArgs. */}}
{{- define "opennebula-csi.validateDriverIdentityArgs" -}}
{{- $driver := (get .Values "driver") | default dict -}}
{{- range $arg := ((get $driver "extraArgs") | default list) -}}
  {{- $text := printf "%v" $arg -}}
  {{- if or (hasPrefix "--drivername=" $text) (eq $text "--drivername") -}}
    {{- fail "driver.extraArgs must not set --drivername; use driver.name as the single CSI identity source" -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/*
Allow release profiles to replace sidecar tags with immutable digest references
without changing the legacy chart defaults.
*/}}
{{- define "opennebula-csi.sidecarImage" -}}
{{- $root := .context -}}
{{- $sidecars := (get $root.Values "sidecars") | default dict -}}
{{- $entry := (get $sidecars .name) | default dict -}}
{{- (get $entry "image") | default .default -}}
{{- end -}}
