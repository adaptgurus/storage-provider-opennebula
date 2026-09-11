# LayerSentry CSI — production qualification lane

Status: SOURCE IN PROGRESS / LIVE `NOT_QUALIFIED`.

This downstream lane preserves the existing OpenNebula CSI storage engine and qualifies only the OpenNebula-managed storage path that the driver actually implements. It is not a universal SAN/NAS driver and does not replace direct Ceph CSI, vendor array CSI, NFS CSI, or other independent storage plugins.

## Identity model

`driver.name` is the deployment identity source of truth. The chart default remains `csi.opennebula.io` for backward compatibility; the LayerSentry release profile sets `driver.name: csi.layersentry.io` and explicitly passes the same value to controller and node processes.

The `layersentry` Go build no longer silently changes the binary default. It instead fails startup unless the explicit driver flag equals `csi.layersentry.io`. This prevents an unmodified upstream chart from appearing to be a valid LayerSentry deployment and removes a hidden second identity default. The upstream-compatible build remains the deliberate legacy path for `csi.opennebula.io` PVs.

The resolved identity is also used by `CSIDriver`, generated `StorageClass` and optional `VolumeSnapshotClass` objects, the kubelet CSI plugin directory and registrar socket. Attachment reconciliation filters both `PersistentVolume.spec.csi.driver` and `VolumeAttachment.spec.attacher` against the running driver identity before it can detach or delete anything. `driver.extraArgs` cannot set `--drivername`; Helm rendering fails if a second deployment identity source is attempted.

See `packaging/layersentry/IDENTITY_AND_MIGRATION.md` for `CSINode` behavior and legacy-volume coexistence. Existing bound PVs are never rewritten to simulate migration.

## Fail-closed LayerSentry profile

When the resolved identity is `csi.layersentry.io`, Helm rejects site values that would bypass the current qualification state. The current candidate:

- requires `credentials.existingSecret` and rejects `credentials.inlineAuth`;
- rejects `snapshotter.enabled=true`;
- rejects `featureGates.cephfsSnapshots=true` and `featureGates.cephfsClones=true`;
- rejects any generated StorageClass with `allowVolumeExpansion: true`.

These checks are identity-scoped. They do not change the legacy `csi.opennebula.io` chart defaults. Optional functionality can be promoted only by an explicit release-source change after the matching backend-specific live tests pass; a site-values override cannot promote an unqualified capability.

## RKE2 target and kubelet paths

The current verified 1.36 target is official RKE2 `v1.36.4+rke2r1`, which packages Kubernetes `v1.36.4`. Its release tag resolves to commit `7479a59cdd2c8ce0b8871699a24daa4b7c28cc64`. No `v1.36.5+rke2r1` release was present when this target was revalidated on 11 September 2026.

The profile records the kubelet root explicitly and defaults to `/var/lib/kubelet`; CSI plugin and `plugins_registry` host paths are derived from that one value. A site that overrides kubelet `--root-dir` must put the identical path in the CSI release lock/profile and re-run qualification.

## Kubernetes 1.36 sidecar release profile

The upstream chart defaults remain unchanged for backward compatibility. The LayerSentry release lock requires these revalidated GA sidecar tag lines for the Kubernetes 1.36 target:

- `registry.k8s.io/sig-storage/csi-provisioner:v6.3.0`
- `registry.k8s.io/sig-storage/csi-attacher:v4.13.0`
- `registry.k8s.io/sig-storage/csi-resizer:v2.2.1`
- `registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.18.0`
- `registry.k8s.io/sig-storage/livenessprobe:v2.20.0`

The release lock accepts those tag lines only when an immutable `@sha256:<digest>` is supplied. Snapshotter is omitted while snapshot capability is unqualified.

## Release packaging

`packaging/layersentry/build_chart.py` copies the identity-safe source chart without string replacement. It validates all required identity surfaces, requires the fail-closed LayerSentry profile admission helper, and requires a release lock containing the exact reviewed image tag plus immutable digest for the LayerSentry driver and every enabled CSI sidecar. The LayerSentry release lock keeps expansion, snapshots and clones false until those capabilities are separately qualified and creates no StorageClass by default.

Start from `packaging/layersentry/release-lock.template.json`, replace every digest placeholder with a verified digest, and then run:

```bash
python3 -m unittest discover -s packaging/layersentry -p 'test_*.py' -v
go test ./...
go build -trimpath -tags layersentry ./cmd/opennebula-csi
go test -tags layersentry ./cmd/opennebula-csi
go test -tags layersentry ./pkg/csi/driver -run '^(TestLayerSentry|TestAttachmentReconciler)'

python3 packaging/layersentry/build_chart.py \
  --release-lock /path/to/release-lock.json \
  --output /path/outside/repository/layersentry-csi

python3 packaging/layersentry/emit_offline_manifest.py \
  --release-lock /path/to/release-lock.json \
  --output /path/outside/repository/images.txt

helm template layersentry /path/outside/repository/layersentry-csi \
  -f /path/outside/repository/layersentry-csi/layersentry-values.json \
  -f /path/to/reviewed-site-values.yaml
```

The reviewed site values must reference a scoped pre-created provider credential Secret. The offline manifest contains only immutable image references. Mirror/preload those exact images into the approved disconnected registry/bundle; do not replace them with floating tags.

## LayerSentry-owned image SBOM and provenance

`packaging/layersentry/build_release.sh` refuses a dirty worktree and requires digest-pinned builder/runtime images. It builds a local OCI image archive with BuildKit SBOM and max-mode provenance attestations. Release engineering must retain the actual immutable image digest and the materialized SBOM/provenance evidence produced from that OCI archive; descriptive claims are not accepted as evidence.

The generic upstream `.github/workflows/release-csi.yaml` explicitly skips `*-layersentry.*` tags so it cannot publish a LayerSentry release through the upstream-compatible build path.

## Production promotion gate

A production release must include `packaging/layersentry/release-lock.json` with real immutable digests and must pass:

```bash
python3 packaging/layersentry/validate_qualification.py \
  --matrix packaging/layersentry/qualification-matrix.json \
  --release-lock packaging/layersentry/release-lock.json \
  --evidence-root .
```

The validator rejects production promotion unless:

- the target is exactly Kubernetes `1.36.4` / RKE2 `v1.36.4+rke2r1` at the verified tag commit;
- `selected_storage_profile` names the exact backend tested;
- every mandatory live lifecycle/recovery/isolation test is `PASS` and has evidence;
- every advertised optional capability has matching `PASS` evidence;
- unadvertised optional capabilities remain explicitly not offered;
- the driver image in the matrix exactly matches the digest-pinned release lock;
- SBOM, provenance and offline-manifest evidence are recorded;
- every recorded evidence path is repository-relative, exists, and is nonempty in the checked-out release source.

`.github/workflows/layersentry-production-gate.yaml` runs this validation for `v*-layersentry.*` tags. It does not deploy or publish anything by itself. The current repository intentionally has no production `release-lock.json`, so a production tag remains blocked until a real candidate image and live backend evidence exist.

## Capabilities and storage profiles

The upstream-compatible engine contains backend-specific expansion plus feature-gated CephFS snapshot/clone paths. The LayerSentry-tagged **controller** advertises only `CREATE_DELETE_VOLUME`, `PUBLISH_UNPUBLISH_VOLUME`, `LIST_VOLUMES` and `GET_CAPACITY`; its plugin identity service does not advertise online expansion. Therefore LayerSentry does not offer end-to-end expansion, snapshots or clones before backend qualification. The existing node service still advertises its implemented `NodeExpandVolume` capability; that node-local RPC is retained rather than falsified, but it does not make expansion a qualified LayerSentry storage capability by itself.

`EXPAND_VOLUME`, `CREATE_DELETE_SNAPSHOT` and `CLONE_VOLUME` are promoted only after a named production storage profile passes the matching live qualification. Snapshotter deployment, snapshot classes and `StorageClass.allowVolumeExpansion` remain blocked before that promotion.

Do not infer qualification of one datastore/backend from another. The selected production storage profile is recorded in `packaging/layersentry/qualification-matrix.json`. Until a concrete backend is selected and live evidence is attached, it remains null and the overall status stays `NOT_QUALIFIED`.

## Required live qualification

At minimum, a named backend profile must pass install, discovery, StorageClass, PVC create, PV bind, Pod mount, recognizable-data write, Pod restart, node restart, controller restart, detach/attach, worker replacement, identical data after replacement, delete, idempotent retry, duplicate operations, UNKNOWN reconciliation and tenant isolation. Expansion, snapshot, clone and snapshot restore are tested only after they are intentionally promoted for a backend; before that they remain unavailable to the LayerSentry release profile.

The inherited `pkg/csi/test/e2e` suite remains useful upstream regression coverage, but it currently provisions its own Kubernetes `v1.31.4` CAPONE environment. It is not evidence for the T4 RKE2 1.36.4 production matrix. T4 qualification must execute against the named RKE2 `v1.36.4+rke2r1` cluster and selected backend.

Source/unit/render success never substitutes for the worker-replacement and data-survival gates. Stateful workloads remain `NOT_QUALIFIED` until the live matrix and evidence requirements are complete.
