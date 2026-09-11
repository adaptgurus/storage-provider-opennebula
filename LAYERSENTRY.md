# LayerSentry CSI — production qualification lane

Status: SOURCE IN PROGRESS / LIVE `NOT_QUALIFIED`.

This downstream lane preserves the existing OpenNebula CSI storage engine and qualifies only the OpenNebula-managed storage path that the driver actually implements. It is not a universal SAN/NAS driver and does not replace direct Ceph CSI, vendor array CSI, NFS CSI, or other independent storage plugins.

## Identity model

The `layersentry` Go build tag keeps `csi.layersentry.io` as the LayerSentry binary default, but the Helm chart no longer relies on a binary-only rename. `driver.name` is the deployment identity source of truth. The chart default remains `csi.opennebula.io` for backward compatibility; the LayerSentry release profile sets `driver.name: csi.layersentry.io` and explicitly passes the same value to both controller and node processes.

The resolved identity is also used by `CSIDriver`, generated `StorageClass` and optional `VolumeSnapshotClass` objects, the kubelet CSI plugin directory and registrar socket. Attachment reconciliation now filters both `PersistentVolume.spec.csi.driver` and `VolumeAttachment.spec.attacher` against the running driver identity before it can detach or delete anything. `driver.extraArgs` cannot set `--drivername`; Helm rendering fails if a second identity source is attempted.

See `packaging/layersentry/IDENTITY_AND_MIGRATION.md` for `CSINode` behavior and legacy-volume coexistence. Existing bound PVs are never rewritten to simulate migration.

## RKE2 target and kubelet paths

The current published stable 1.36 target is RKE2 `v1.36.3+rke2r1`, source commit `c4f306e6c5fa18dfb447bf6b8a0423f2da68c939`. The profile records the kubelet root explicitly and defaults to `/var/lib/kubelet`; CSI plugin and `plugins_registry` host paths are derived from that one value. A site that overrides kubelet `--root-dir` must put the identical path in the CSI release lock/profile.

## Kubernetes 1.36 sidecar release profile

The upstream chart defaults remain unchanged for backward compatibility. The LayerSentry release lock instead requires the reviewed maintained sidecar tag lines for the Kubernetes 1.36 target:

- `registry.k8s.io/sig-storage/csi-provisioner:v6.3.0`
- `registry.k8s.io/sig-storage/csi-attacher:v4.13.0`
- `registry.k8s.io/sig-storage/csi-resizer:v2.2.1`
- `registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.18.0`
- `registry.k8s.io/sig-storage/livenessprobe:v2.20.0`

The release lock accepts those tag lines only when an immutable `@sha256:<digest>` is supplied. The current official snapshotter line reviewed during this work is v8.6.0, but LayerSentry does not include a snapshotter image while snapshot capability is unqualified.

## Release packaging

`packaging/layersentry/build_chart.py` copies the identity-safe source chart without string replacement. It validates all required identity surfaces and requires a release lock containing the exact reviewed image tag plus immutable digest for the LayerSentry driver and every enabled CSI sidecar. The LayerSentry release lock keeps expansion, snapshots and clones false until those capabilities are separately qualified and creates no StorageClass by default.

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

The offline manifest contains only immutable image references. Mirror/preload those exact images into the approved disconnected registry/bundle; do not replace them with floating tags.

## LayerSentry-owned image SBOM and provenance

`packaging/layersentry/build_release.sh` refuses a dirty worktree and requires digest-pinned builder/runtime images. It creates a local OCI image archive using `docker buildx` with `--sbom=true` and `--provenance=mode=max`, plus source metadata and archive SHA-256 evidence. Running the script produces build artifacts; merely having the script in source is not evidence that an SBOM/provenance artifact exists.

## Capabilities and storage profiles

The upstream-compatible engine contains backend-specific expansion plus feature-gated CephFS snapshot/clone paths. The LayerSentry-tagged **controller** advertises only `CREATE_DELETE_VOLUME`, `PUBLISH_UNPUBLISH_VOLUME`, `LIST_VOLUMES` and `GET_CAPACITY`; its plugin identity service does not advertise online expansion. Therefore LayerSentry does not offer end-to-end expansion, snapshots or clones before backend qualification. The existing node service still advertises its implemented `NodeExpandVolume` capability; that node-local RPC is retained rather than falsified, but it does not make expansion a qualified LayerSentry storage capability by itself.

`EXPAND_VOLUME`, `CREATE_DELETE_SNAPSHOT` and `CLONE_VOLUME` are promoted only after a named production storage profile passes the matching live qualification. Snapshotter deployment, snapshot classes and `StorageClass.allowVolumeExpansion` must remain disabled before that promotion.

Do not infer qualification of one datastore/backend from another. The selected production storage profile is recorded in `packaging/layersentry/qualification-matrix.json`. Until a concrete backend is selected and live evidence is attached, it remains null and the overall status stays `NOT_QUALIFIED`.

## Required live qualification

At minimum, a named backend profile must pass install, discovery, StorageClass, PVC create, PV bind, Pod mount, recognizable-data write, Pod restart, node restart, controller restart, detach/attach, worker replacement, identical data after replacement, delete, idempotent retry, duplicate operations, UNKNOWN reconciliation and tenant isolation. Expansion, snapshot, clone and snapshot restore are tested only after they are intentionally promoted for a backend; before that they remain unavailable to the LayerSentry release profile.

Source/unit/render success never substitutes for the worker-replacement and data-survival gates. Stateful workloads remain `NOT_QUALIFIED` until the live matrix and evidence requirements are complete.
