# LayerSentry CSI — initial product identity and build foundation

Owner: adaptgurus / LayerSentry. Review date: 10 September 2026.

This is a controlled downstream distribution of the existing OpenNebula CSI implementation, not a new block-storage engine. The existing source tree and Apache-2.0 provenance are retained. The tagged build reuses controller/node/identity and provider implementations, but has an explicit LayerSentry default identity and version/commit metadata. Untagged builds are unchanged.

## Implemented in this initial change

- `go build -tags layersentry` opts into the `csi.layersentry.io` default and matching flag help. An explicit `--drivername=csi.opennebula.io` still permits a future qualified compatibility-mode deployment.
- `layersentry/build.sh X.Y.Z-layersentry.N` builds `bin/layersentry-csi` with provenance from a clean checkout and read-only module dependencies. It does not download an arbitrary third-party driver image, install a chart, push an image or mutate volumes.
- Three unit tests exercise the default, help and explicit legacy override.

## DO NOT DEPLOY THIS TAGGED BINARY WITH THE UNMODIFIED CHART

The inspected chart still hardcodes `csi.opennebula.io` in the CSIDriver object, StorageClass provisioner and kubelet registration paths. Replacing only the container image with this tagged build would cause an identity mismatch. This initial source change deliberately does not pretend that chart integration or production qualification is complete.

Before a new-identity release, use one validated Helm `driver.name` source for: both controller and node `--drivername`, CSIDriver metadata, generated StorageClasses, kubelet socket/registration paths, VolumeSnapshotClasses, CSINode registration and attachment/reconciliation filters. Audit all hardcoded runtime identity uses. Keep the upstream name as the backward-compatible default for unmodified charts. Make the LayerSentry product profile explicitly select the new name. Reject conflicting per-container extra arguments. Verify the actual RKE2 kubelet root path and node identity mapping.

Build/publish an owner-controlled image, for example the planned `ghcr.io/adaptgurus/layersentry-csi`, only after review. That image is NOT published by this change. Pin its actual digest and every sidecar/offline artifact; the inspected chart's `nudevco/opennebula-csi:v0.5.15` default is not the LayerSentry release. Image provenance does not certify backend semantics.

## Existing volumes and vendor drivers

A product rename is not an in-place volume migration. Do not edit bound PV CSI driver fields, rewrite volume handles, delete VolumeAttachments, or run independent drivers against the same backend volume to make it look rebranded. Keep a compatible old-identity deployment for existing volumes until a tested retain/import or data-copy migration is designed. A new greenfield cluster may use the new identity after all gates pass. Evaluate compatibility mode separately; support is not established by the override unit test.

LayerSentry CSI owns Kubernetes volumes backed through the qualified OpenNebula path. Direct Ceph CSI, HPE/Dell/IBM/NetApp/Pure/Hitachi and NFS drivers remain separate optional providers. Do not fabricate a universal array adapter or claim changing the CSI name creates missing migration, snapshot or backup capabilities. Worker replacement must preserve independently owned application volumes.

## Remaining acceptance gates

Full tagged Go build and package tests; rendered chart identity consistency; Kubernetes CSI sanity/e2e; create/delete idempotency; attach/detach/mount/expand where offered; per-tenant isolation; controller restart/UNKNOWN state reconciliation; fenced worker replacement with identical data; legacy compatibility; selected snapshots/clones; pinned sidecar compatibility with RKE2 1.36; offline install/repair/upgrade; signed image/SBOM and real restore tests. New automatic recovery paths remain unqualified until fault tests pass.

## Evidence from this authoring session

The three added tests passed in an isolated standard-library flag harness containing the actual added files and a substitute for main.go's flag registration. `bash -n layersentry/build.sh` passed. This is NOT a full module build, Helm render, CI execution, CSI conformance test or live storage qualification. Container GitHub DNS and external module access were unavailable, so those checks were not run. No published image, cluster deployment or volume migration occurred.

Canonical product contract: adaptgurus/codexagentlogic. Read the latest assigned central task before extending this branch; preserve the actual upstream base and do not reset shared refs.
