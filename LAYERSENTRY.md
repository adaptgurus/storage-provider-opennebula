# LayerSentry CSI — initial downstream build variant

Status: implementation bootstrap, NOT production-qualified. Prepared 10 September 2026.

This is LayerSentry-owned packaging and a distinct opt-in CSI identity built on the existing OpenNebula CSI engine. It does not rewrite provisioning, attachment, filesystem or block operations, and it does not implement a universal replacement for Ceph/HPE/Dell/IBM/vendor CSI. Preserve upstream Apache-2.0 notices and provenance. The original chart, default build and default branch behavior remain unchanged.

## What changed
The `layersentry` Go build tag selects `csi.layersentry.io` as the default identity. Confirm control of that identifier namespace before the first production deployment; it is a proposed technical identity, not a domain-ownership claim. Build the package (`./cmd/opennebula-csi`), not just main.go, so the tagged file is included. Explicit --drivername remains available for a separately qualified legacy identity profile.

`packaging/layersentry/build_chart.py` generates a separate candidate chart from the exact inspected upstream chart tree. It aligns CSIDriver/StorageClass/registrar/socket identity and supplies the same flag to node and controller. It preserves upstream provider API groups and ordinary storage metadata. It refuses changed chart inputs, uncommitted chart edits, output inside the source repository and non-digest driver-image references. No cluster operation occurs. The supplied overlay disables the optional snapshotter and creates no StorageClass by default; this does not disable every snapshot RPC in the underlying engine.

## Candidate build and checks
Run in this repository:

```bash
python3 -m unittest discover -s packaging/layersentry -p 'test_*.py' -v
go test -tags layersentry ./cmd/opennebula-csi
go test ./...
```

The Dockerfile requires separately verified GO_IMAGE and RUNTIME_IMAGE build arguments. Supply compatible immutable image@sha256 references; do not substitute unverified floating defaults. The runtime must already contain the required Ceph/mount/filesystem tools. The complete image/dependency/sidecar release lock remains a separate gate. No ready-made published LayerSentry image is claimed.

After building, scanning and publishing a candidate image, generate the chart with its actual digest:

```bash
python3 packaging/layersentry/build_chart.py \
  --output ../layersentry-csi-candidate \
  --image-repository "$LAYER_CSI_IMAGE_REPOSITORY" \
  --image-tag-digest "$LAYER_CSI_IMAGE_TAG_AT_SHA256"
helm template layersentry-csi ../layersentry-csi-candidate \
  -f ../layersentry-csi-candidate/layersentry-values.json \
  -f /path/to/reviewed-site-values.yaml
```

The site values must supply trusted endpoint, scoped secret reference, datastore allowlist, resources, storage/topology and private registry settings. They must not override the identity flag inconsistently. Run Helm lint/render/schema and driver-identity checks before installation. The unmodified upstream defaults are not an approved site profile. Review inherited sidecar images and feature gates. Production enablement remains blocked until the tests below pass.

## Compatibility and data safety
Existing volumes using `csi.opennebula.io` do not become LayerSentry volumes by changing a label. Retain their driver and identity until a tested migration moves them to new claims, or qualify a compatibility build that preserves the old identity. Never bulk-edit PV driver/volume handles or allow two independent controllers to mutate the same backend volumes. All driver-related identities, classes and socket paths must agree.

Inherited limitations remain: local image SIZE can be stale after expansion; detached local persistent expansion is rejected; CephFS node expansion can produce NodeResizeError; local storage is not replicated by CSI. Replacing a VM must not delete independent volumes, but loss of an unreplicated physical datastore cannot be repaired by branding a driver. Direct Ceph/vendor CSI keep their own technical driver identities and supported capabilities.

## Required next gates
Full Go build/tests, current dependency and sidecar qualification, Helm render/schema, CSI sanity/conformance, provision/attach/mount/detach/resize, node replacement with identical data, controller failover, duplicate requests, negative tenant authorization, explicit retention/deletion, legacy identity coexistence/migration, snapshot/restore where advertised and denied-external-egress operation. Each is separately evidenced. No new backup-chain, DR or migration capability is delivered by this build variant.

## Validation performed for this initial change
Ten Python packaging unit tests passed; Python syntax checks and gofmt passed. Tests exercise synthetic template fixtures and profile validation, not a real Helm render or provider operation. Full Go dependencies/build, container build, Helm, CSI and live tests were not executed in this preparation environment. The Go identity regression test is written but not run.
