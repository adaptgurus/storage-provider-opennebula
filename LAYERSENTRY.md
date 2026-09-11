# LayerSentry CSI — production qualification lane

Status: SOURCE/RELEASE HARDENING / LIVE `NOT_QUALIFIED`.

This downstream lane preserves the existing OpenNebula CSI storage engine and qualifies only the OpenNebula-managed storage path that the driver actually implements. It is not a universal SAN/NAS driver and does not replace direct Ceph CSI, vendor array CSI, NFS CSI, or other independent storage plugins.

## Identity model

`driver.name` is the deployment identity source of truth. The chart default remains `csi.opennebula.io` for backward compatibility; the LayerSentry release profile sets `driver.name: csi.layersentry.io` and explicitly passes the same value to controller and node processes.

The `layersentry` Go build does not silently change the binary default. In driver mode it requires the explicit LayerSentry identity supplied by the chart. The resolved identity is also used by `CSIDriver`, generated `StorageClass` / optional `VolumeSnapshotClass`, kubelet CSI plugin directory and registrar socket. Attachment reconciliation filters both `PersistentVolume.spec.csi.driver` and `VolumeAttachment.spec.attacher` against the running driver identity. `driver.extraArgs` cannot introduce a second `--drivername` source.

Existing bound PVs are never rewritten to simulate migration. See `packaging/layersentry/IDENTITY_AND_MIGRATION.md` for legacy-volume coexistence.

## Fail-closed LayerSentry profile

When the resolved identity is `csi.layersentry.io`, Helm rejects site values that bypass the currently qualified surface. The current candidate:

- requires `credentials.existingSecret` and rejects `credentials.inlineAuth`;
- requires `controller.replicaCount: 1` until multi-controller driver/sidecar leadership is explicitly qualified;
- requires `resizer.enabled=false` while expansion is unqualified;
- rejects `snapshotter.enabled=true`;
- rejects CephFS snapshot/clone feature gates;
- rejects generated StorageClasses with `allowVolumeExpansion: true`;
- adds CSI `/healthz` liveness/readiness probes to the LayerSentry controller and node driver containers via the standard liveness sidecar endpoint.

These checks are identity-scoped. Legacy `csi.opennebula.io` rendering remains intentionally compatible and does not receive the LayerSentry health/topology restrictions.

## RKE2 target and kubelet paths

The verified target is official RKE2 `v1.36.4+rke2r1`, packaging Kubernetes `v1.36.4`, release-tag commit `7479a59cdd2c8ce0b8871699a24daa4b7c28cc64`. No `v1.36.5+rke2r1` release was present when revalidated on 11 September 2026.

The release lock records the kubelet root explicitly; the candidate default is `/var/lib/kubelet`. CSI plugin and `plugins_registry` paths derive from that one value. A site that overrides kubelet `--root-dir` must record the identical value and re-run qualification.

## Release and sidecar profile

Current release identity:

- release version: `0.5.15-layersentry.1`
- Git release tag: `v0.5.15-layersentry.1`
- driver image tag: `ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.1`

The enabled Kubernetes 1.36 sidecars are:

- `registry.k8s.io/sig-storage/csi-provisioner:v6.3.0`
- `registry.k8s.io/sig-storage/csi-attacher:v4.13.0`
- `registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.18.0`
- `registry.k8s.io/sig-storage/livenessprobe:v2.20.0`

Every production reference must include an immutable `@sha256:<digest>`. `csi-resizer` is omitted while expansion is unqualified; `csi-snapshotter` is omitted while snapshot capability is unqualified.

## Frozen-source release model

`release-lock.json` binds the release version/tag to a full 40-character `sourceCommit`. `build_chart.py` refuses to generate the candidate chart unless that commit equals the exact checked-out source HEAD. `build_release.sh` requires the image tag to equal `v<release-version>`, records source commit/release tag, and emits source provenance plus checksums.

The production tag workflow permits a later **evidence-only** release commit, because a commit cannot contain its own final SHA as data. The frozen source commit must be an ancestor of the release tag, and every path changed after that source commit must be limited to:

- `packaging/layersentry/release-lock.json`
- `packaging/layersentry/qualification-matrix.json`
- `packaging/layersentry/evidence/*`
- `packaging/layersentry/qualified-profiles/*`

Any executable, chart, workflow, documentation, or other source change after the frozen build commit invalidates promotion and requires a new build/qualification cycle.

## Release packaging and supply-chain evidence

`packaging/layersentry/build_chart.py` copies the identity-safe chart without string replacement and validates the reviewed identity/capability image set. `emit_offline_manifest.py` emits exactly the enabled digest-pinned release images.

`packaging/layersentry/build_release.sh` refuses a dirty worktree, requires digest-pinned builder/runtime images, builds an OCI archive with BuildKit SBOM and max-mode provenance, and records:

- immutable driver image reference;
- exact source commit and release tag;
- `source-provenance.json`;
- BuildKit metadata/result digest;
- OCI image/index digests;
- SPDX SBOM evidence;
- SLSA provenance evidence;
- `release-artifacts.sha256`.

`verify_oci_attestations.py` validates OCI blob digests, image/attestation linkage, SPDX SBOM and SLSA provenance before the artifacts are eligible for release evidence.

The generic upstream `.github/workflows/release-csi.yaml` skips `*-layersentry.*` tags so a LayerSentry release cannot accidentally use the upstream-compatible publisher.

## Live release identity and data-survival qualification

`live_qualification.py` covers bounded Kubernetes lifecycle/data-survival phases: install/discovery, StorageClass, PVC/PV, mount, recognizable-data checksum, Pod restart, controller restart, detach/attach, node restart verification, worker replacement, same-data-after-replacement and cleanup.

`verify_live_release.py` is required twice—baseline and final. It verifies the **running** controller/node Pods use exactly the digest-pinned images in `release-lock.json`, rejects resizer/snapshotter and unexpected containers, records runtime image IDs, and binds both checkpoints to the same qualification `run_id` and exact release-lock SHA256.

The remaining fault/isolation scenarios are deliberately controlled tests rather than fake automation:

- idempotent retry;
- genuinely duplicate/concurrent mutations;
- UNKNOWN/lost-ack reconciliation;
- two-context tenant isolation;
- live foreign-CSI coexistence/isolation.

`record_controlled_evidence.py` normalizes a reviewed PASS into the existing qualification run but cannot infer a PASS itself. Full acceptance criteria and command order are in `packaging/layersentry/LIVE_QUALIFICATION.md`.

## Backend-scoped qualification

A result applies to one machine-readable storage profile only. The qualified profile records OpenNebula datastore identity, backend/vendor/model/firmware, transport/multipath, node OS/kernel, StorageClass parameters, capabilities, limitations and evidence. A successful profile never certifies an entire SAN/NAS/vendor category.

Expansion, snapshots, restore and clone are currently **not offered**. Underlying implementation paths do not become LayerSentry production capabilities until the release source deliberately advertises them and that exact backend passes matching live integrity/recovery tests.

## Production promotion gates

A production tag must contain a real `packaging/layersentry/release-lock.json`, a completed qualification matrix, exact backend profile and materialized evidence. All three validators must pass:

```bash
python3 packaging/layersentry/validate_qualification.py \
  --matrix packaging/layersentry/qualification-matrix.json \
  --release-lock packaging/layersentry/release-lock.json \
  --evidence-root .

python3 packaging/layersentry/validate_storage_profile.py \
  --matrix packaging/layersentry/qualification-matrix.json \
  --release-lock packaging/layersentry/release-lock.json \
  --evidence-root .

python3 packaging/layersentry/validate_live_evidence.py \
  --matrix packaging/layersentry/qualification-matrix.json \
  --release-lock packaging/layersentry/release-lock.json \
  --evidence-root .
```

Promotion rejects:

- wrong release/tag/source commit;
- code changed after the frozen binary source commit;
- wrong RKE2/Kubernetes target;
- missing backend profile;
- missing/empty/non-repository evidence;
- mixed qualification run IDs or StorageClasses;
- baseline/final running images that differ from the release lock;
- floating/extra/missing image references;
- offline manifest differences;
- source-provenance mismatches;
- artifact checksum mismatch;
- missing lifecycle/recovery/failure/isolation evidence;
- an advertised capability without matching backend-specific PASS evidence.

`.github/workflows/layersentry-production-gate.yaml` enforces these rules for `v*-layersentry.*` tags. It validates recorded evidence; it does not itself deploy or publish the release.

## Current limitation: controller topology

The LayerSentry profile is intentionally restricted to one CSI controller Pod. The driver has its own mutation leadership while standard CSI sidecars also perform their own leader elections. Until the combined multi-replica behavior is explicitly designed and live-qualified, simply increasing replicas would create an unproven topology. Helm therefore fails closed for LayerSentry when `controller.replicaCount != 1`.

This is a known availability limitation, not hidden HA. Node DaemonSet rolling updates remain one unavailable node at a time.

## Evidence hierarchy and current state

The inherited `pkg/csi/test/e2e` environment still provisions Kubernetes `v1.31.4`; it is useful regression coverage but is **not** RKE2 1.36.4 T4 production evidence. Source/unit/render CI cannot substitute for the named-backend worker-replacement, failure-reconciliation, authorization and data-survival gates.

Until a real release image/digest set and one exact production backend profile complete the live matrix, persistent stateful workloads remain `NOT_QUALIFIED` even when the source/release path is CI-verified.
