# LayerSentry CSI — production qualification lane

Status: SOURCE IN PROGRESS / LIVE `NOT_QUALIFIED`.

This downstream lane preserves the existing OpenNebula CSI storage engine and qualifies only the OpenNebula-managed storage path that the driver actually implements. It is not a universal SAN/NAS driver and does not replace direct Ceph CSI, vendor array CSI, NFS CSI, or other independent storage plugins.

## Identity model

The `layersentry` Go build tag keeps `csi.layersentry.io` as the LayerSentry binary default, but the Helm chart no longer relies on a binary-only rename. `driver.name` is the deployment identity source of truth. The chart default remains `csi.opennebula.io` for backward compatibility; the LayerSentry release profile sets `driver.name: csi.layersentry.io` and explicitly passes the same value to both controller and node processes.

The resolved identity is also used by `CSIDriver`, generated `StorageClass` and optional `VolumeSnapshotClass` objects, the kubelet CSI plugin directory and registrar socket. Attachment reconciliation now filters both `PersistentVolume.spec.csi.driver` and `VolumeAttachment.spec.attacher` against the running driver identity before it can detach or delete anything. `driver.extraArgs` cannot set `--drivername`; Helm rendering fails if a second identity source is attempted.

See `packaging/layersentry/IDENTITY_AND_MIGRATION.md` for `CSINode` behavior and legacy-volume coexistence. Existing bound PVs are never rewritten to simulate migration.

## RKE2 target and kubelet paths

The current release candidate target is RKE2 `v1.36.4+rke2r1`, source commit `7479a59cdd2c8ce0b8871699a24daa4b7c28cc64`. The profile records the kubelet root explicitly and defaults to `/var/lib/kubelet`; CSI plugin and `plugins_registry` host paths are derived from that one value. A site that overrides kubelet `--root-dir` must put the identical path in the CSI release lock/profile.

## Release packaging

`packaging/layersentry/build_chart.py` copies the identity-safe source chart without string replacement. It validates all required identity surfaces and requires a release lock containing immutable digest references for the LayerSentry driver image and every enabled CSI sidecar. The LayerSentry release lock keeps expansion, snapshots and clones false until those capabilities are separately qualified and creates no StorageClass by default.

Start from `packaging/layersentry/release-lock.template.json`, replace every digest placeholder with a verified digest, and then run:

```bash
python3 -m unittest discover -s packaging/layersentry -p 'test_*.py' -v
go test ./...
go test -tags layersentry ./cmd/opennebula-csi ./pkg/csi/driver

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

The upstream-compatible engine contains backend-specific expansion plus feature-gated CephFS snapshot/clone paths. The LayerSentry-tagged release deliberately advertises only `CREATE_DELETE_VOLUME`, `PUBLISH_UNPUBLISH_VOLUME`, `LIST_VOLUMES` and `GET_CAPACITY`. It does **not** advertise `EXPAND_VOLUME`, `CREATE_DELETE_SNAPSHOT` or `CLONE_VOLUME` until a named production storage profile has passed the matching live qualification. This is enforced in the tagged binary, not only in Helm values.

Do not infer qualification of one datastore/backend from another. The selected production storage profile is recorded in `packaging/layersentry/qualification-matrix.json`. Until a concrete backend is selected and live evidence is attached, it remains null and the overall status stays `NOT_QUALIFIED`.

## Required live qualification

At minimum, a named backend profile must pass install, discovery, StorageClass, PVC create, PV bind, Pod mount, recognizable-data write, Pod restart, node restart, controller restart, detach/attach, worker replacement, identical data after replacement, delete, idempotent retry, duplicate operations, UNKNOWN reconciliation and tenant isolation. Expansion, snapshot, clone and snapshot restore are tested only after they are intentionally promoted for a backend; before that they remain unadvertised.

Source/unit/render success never substitutes for the worker-replacement and data-survival gates. Stateful workloads remain `NOT_QUALIFIED` until the live matrix and evidence requirements are complete.
