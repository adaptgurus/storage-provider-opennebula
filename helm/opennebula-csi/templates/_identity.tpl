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
The current LayerSentry release profile has not qualified expansion, snapshots,
clones or a multi-controller topology. Site values must not be able to bypass
those decisions. LayerSentry also requires a pre-created/scoped Secret instead
of embedding provider credentials in release values. These checks are
identity-scoped so legacy upstream-compatible behavior remains unchanged.
*/}}
{{- define "opennebula-csi.validateLayerSentryProfile" -}}
{{- $driverName := include "opennebula-csi.driverName" . -}}
{{- if eq $driverName "csi.layersentry.io" -}}
  {{- $credentials := (get .Values "credentials") | default dict -}}
  {{- $existingSecret := (get $credentials "existingSecret") | default dict -}}
  {{- $inlineAuth := trim (printf "%v" ((get $credentials "inlineAuth") | default "")) -}}
  {{- if ne $inlineAuth "" -}}
    {{- fail "LayerSentry CSI forbids credentials.inlineAuth; use credentials.existingSecret with a scoped pre-created Secret" -}}
  {{- end -}}
  {{- $secretName := trim (printf "%v" ((get $existingSecret "name") | default "")) -}}
  {{- $secretKey := trim (printf "%v" ((get $existingSecret "key") | default "")) -}}
  {{- if eq $secretName "" -}}
    {{- fail "LayerSentry CSI requires credentials.existingSecret.name" -}}
  {{- end -}}
  {{- if eq $secretKey "" -}}
    {{- fail "LayerSentry CSI requires credentials.existingSecret.key" -}}
  {{- end -}}

  {{- $controller := (get .Values "controller") | default dict -}}
  {{- $replicas := (get $controller "replicaCount") | default 1 -}}
  {{- if ne (int $replicas) 1 -}}
    {{- fail "LayerSentry CSI multi-controller topology is not qualified; controller.replicaCount must remain 1" -}}
  {{- end -}}

  {{- $resizer := (get .Values "resizer") | default dict -}}
  {{- if and (hasKey $resizer "enabled") (get $resizer "enabled") -}}
    {{- fail "LayerSentry CSI expansion is not qualified; resizer.enabled must remain false" -}}
  {{- end -}}

  {{- $snapshotter := (get .Values "snapshotter") | default dict -}}
  {{- if ((get $snapshotter "enabled") | default false) -}}
    {{- fail "LayerSentry CSI snapshots are not qualified; snapshotter.enabled must remain false" -}}
  {{- end -}}

  {{- $featureGates := (get .Values "featureGates") | default dict -}}
  {{- if ((get $featureGates "cephfsSnapshots") | default false) -}}
    {{- fail "LayerSentry CSI snapshots are not qualified; featureGates.cephfsSnapshots must remain false" -}}
  {{- end -}}
  {{- if ((get $featureGates "cephfsClones") | default false) -}}
    {{- fail "LayerSentry CSI clones are not qualified; featureGates.cephfsClones must remain false" -}}
  {{- end -}}

  {{- range $class := ((get .Values "storageClasses") | default list) -}}
    {{- if ((get $class "allowVolumeExpansion") | default false) -}}
      {{- fail "LayerSentry CSI expansion is not qualified; storageClasses[].allowVolumeExpansion must remain false" -}}
    {{- end -}}
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
