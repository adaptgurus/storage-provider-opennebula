# LayerSentry CSI live qualification — RKE2 v1.36.4+rke2r1

This document defines the **live** T4 qualification evidence. Source CI, Helm render tests, unit tests, the inherited Kubernetes v1.31.4 E2E harness, and successful image builds are not substitutes for these tests.

## Fixed target

- LayerSentry release: `v0.5.15-layersentry.1`
- RKE2: `v1.36.4+rke2r1`
- Kubernetes: `v1.36.4`
- RKE2 release-tag commit: `7479a59cdd2c8ce0b8871699a24daa4b7c28cc64`
- CSI identity: `csi.layersentry.io`
- kubelet root: record the real cluster value; candidate default is `/var/lib/kubelet`
- storage backend: exactly one named `selected_storage_profile` per qualification result

Do not reuse the result for another datastore type, vendor/model, firmware, transport, multipath policy, StorageClass parameter set, node OS/kernel, or topology unless that profile is separately qualified.

## Frozen release prerequisite

Before live testing, create the real `packaging/layersentry/release-lock.json`. It must contain the exact release version/tag, the **frozen binary/chart source commit**, RKE2 pin, driver image digest and all enabled sidecar digests. Generate and retain:

1. the LayerSentry driver `image:tag@sha256` reference;
2. exact sidecar digest references;
3. the generated SBOM and SLSA provenance evidence;
4. `source-provenance.json`;
5. `release-artifacts.sha256`;
6. the exact offline image manifest;
7. the selected storage-profile record;
8. a dedicated qualification StorageClass with `provisioner: csi.layersentry.io`, `reclaimPolicy: Delete`, and expansion disabled;
9. an immutable digest-pinned probe image available to the RKE2 cluster;
10. a non-production or explicitly approved qualification cluster/backend capacity.

The production release model is deliberate: build from a frozen source commit, then allow only release-lock, qualification-matrix, evidence, and qualified-profile files to change before the release tag. Any executable/chart/workflow change after the frozen source commit invalidates the production tag gate and requires rebuilding/requalifying the image.

Do not run worker replacement, API fault injection, or controller disruption against unrelated production workloads.

## Automated lifecycle/data-survival phases

`live_qualification.py` writes individual JSON evidence files and keeps state in the chosen evidence directory. Use a repository-relative evidence destination if the final evidence will be committed/frozen with the release.

### 1. Baseline

```bash
python3 packaging/layersentry/live_qualification.py \
  --kubeconfig /secure/path/rke2.yaml \
  --evidence-dir evidence/t4/<profile>/<run> \
  baseline \
  --driver-namespace <csi-namespace> \
  --storage-class <qualified-storageclass> \
  --workload-image <probe-image:tag@sha256:digest> \
  --allow-controller-restart
```

The baseline refuses the wrong RKE2 patch version and verifies CSIDriver/CSINode discovery, Ready-node version consistency, StorageClass ownership, PVC/PV creation, mount, marker write/checksum, pod restart, controller restart, detach and reattach with identical data.

The probe pod is non-root, drops all Linux capabilities, disables service-account token mounting, uses RuntimeDefault seccomp, and uses an `fsGroup` for writable filesystem volumes.

Immediately after baseline, bind this qualification run to the exact frozen release images:

```bash
python3 packaging/layersentry/verify_live_release.py \
  --kubeconfig /secure/path/rke2.yaml \
  --release-lock packaging/layersentry/release-lock.json \
  --evidence-dir evidence/t4/<profile>/<run> \
  --phase baseline
```

This requires the exact reviewed controller container set (`opennebula-csi`, provisioner, attacher, liveness probe), exact reviewed node set (`opennebula-csi`, node-driver-registrar, liveness probe), digest references exactly matching the release lock, no resizer/snapshotter, no unexpected regular/init containers, all pods Ready, and node coverage on all Ready nodes. It emits `release_identity_baseline.json` with the same `run_id` and exact release-lock SHA256.

### 2. Node restart

Restart **the reported Kubernetes node** using the approved OpenNebula/LayerSentry lifecycle. Do not replace it in this phase. Then run:

```bash
python3 packaging/layersentry/live_qualification.py \
  --kubeconfig /secure/path/rke2.yaml \
  --evidence-dir evidence/t4/<profile>/<run> \
  after-node-restart
```

PASS requires the same Kubernetes Node UID, node Ready, probe Ready, and the original marker checksum unchanged.

### 3. Prepare worker replacement

```bash
python3 packaging/layersentry/live_qualification.py \
  --kubeconfig /secure/path/rke2.yaml \
  --evidence-dir evidence/t4/<profile>/<run> \
  prepare-worker-replacement
```

The runner records the old node name/UID, removes the probe Pod, and requires the test PV to detach before infrastructure mutation.

### 4. Replace the worker

Use the authorized LayerSentry/OneKS worker replacement workflow. Replacement is an infrastructure lifecycle operation and is deliberately not implemented as an arbitrary shell command inside the CSI test runner.

Then verify replacement and data survival:

```bash
python3 packaging/layersentry/live_qualification.py \
  --kubeconfig /secure/path/rke2.yaml \
  --evidence-dir evidence/t4/<profile>/<run> \
  after-worker-replacement \
  --replacement-node <replacement-kubernetes-node>
```

PASS requires a Ready replacement node with changed node identity, LayerSentry CSINode registration, remount of the original PVC, and an identical marker checksum.

After replacement, revalidate the exact running release:

```bash
python3 packaging/layersentry/verify_live_release.py \
  --kubeconfig /secure/path/rke2.yaml \
  --release-lock packaging/layersentry/release-lock.json \
  --evidence-dir evidence/t4/<profile>/<run> \
  --phase final
```

The baseline and final identity records must share the same run ID and exact release-lock SHA256. The production gate rejects mixed runs or a changed release lock.

### 5. Controlled failure/isolation tests

The following remain mandatory production blockers and require deliberate fault/authorization scenarios:

- `idempotent_retry`
- `duplicate_operations`
- `unknown_reconciliation`
- `tenant_isolation`
- `foreign_csi_isolation`

A screenshot or statement such as “tested OK” is insufficient. Evidence must include request/object IDs, timestamps, expected/actual behavior, relevant Kubernetes/OpenNebula state and post-test data-integrity result.

After a documented scenario passes, normalize it into the current qualification run with:

```bash
python3 packaging/layersentry/record_controlled_evidence.py \
  --evidence-dir evidence/t4/<profile>/<run> \
  --test-id <idempotent_retry|duplicate_operations|unknown_reconciliation|tenant_isolation|foreign_csi_isolation> \
  --details-json /path/to/reviewed-details.json \
  --confirm-pass
```

The helper never performs or infers a PASS; it only records an explicitly confirmed result and binds it to the existing state `run_id`, exact RKE2 target, CSI identity and StorageClass.

#### Idempotent retry

Cause a retry where the backend mutation may have happened but the caller cannot rely on the first acknowledgement. PASS requires one logical volume/attachment result, no duplicate backend resource, safe convergence and correct persistent data.

#### Duplicate operations

Generate genuinely overlapping/repeated CSI mutations for the same volume/target. PASS requires coalesced/rejected/idempotent resolution, no duplicate attachment/volume or persistent metadata drift, and successful remount/data verification.

#### UNKNOWN reconciliation / lost acknowledgement

Introduce a bounded, reversible communication failure after mutation can occur but before the caller can rely on the reply. PASS requires no blind destructive repetition, safe reconciliation/quarantine, no duplicate/double attachment and intact data after recovery.

#### Tenant isolation

Use two independently authorized tenant/user contexts. Tenant B's negative attempts against tenant A's LayerSentry/PVC-facing resources must be denied without mutation. Same-admin namespace relabeling is not sufficient.

#### Foreign CSI isolation

Use a disposable volume owned by an independent CSI plugin. LayerSentry reconciliation must ignore the foreign PV/VolumeAttachment during both normal and stale/ambiguous LayerSentry scenarios; the foreign volume must remain healthy and unchanged.

### 6. Delete/cleanup

Run cleanup after all fault/authorization evidence that needs the live PVC is collected:

```bash
python3 packaging/layersentry/live_qualification.py \
  --kubeconfig /secure/path/rke2.yaml \
  --evidence-dir evidence/t4/<profile>/<run> \
  cleanup
```

PASS requires detach, PVC deletion, and disappearance of the dynamically provisioned PV under `reclaimPolicy: Delete`.

## Current capability and topology restrictions

The current release does **not** offer expansion, snapshots, snapshot restore or clone. Keep those records explicitly `NOT_OFFERED` / `NOT_ADVERTISED`.

The LayerSentry profile currently requires `controller.replicaCount: 1`. Multi-controller operation is not claimed as production HA because the driver and standard CSI sidecars use separate leader-election mechanisms; replica counts greater than one are rejected until that combined topology is explicitly qualified.

LayerSentry controller and node driver containers use the CSI liveness sidecar `/healthz` endpoint for Kubernetes liveness/readiness checks. This health behavior is LayerSentry-identity-scoped; legacy chart rendering remains unchanged.

## Evidence acceptance and production promotion

Before a production tag:

- every mandatory lifecycle/failure/isolation test must be `PASS` with repository-relative JSON evidence;
- `release_identity_baseline` and `release_identity_final` must both be `PASS`;
- all mandatory evidence must share exactly one `run_id` and one StorageClass;
- baseline/final identity evidence must match the exact frozen release-lock bytes and driver image;
- `selected_storage_profile` / `selected_storage_profile_file` must identify the exact backend profile;
- `release-lock.json` must contain real immutable digests and the frozen source commit;
- SBOM, provenance, source-provenance, artifact-checksum and offline-manifest paths must be materialized;
- run all three gates:

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

If any mandatory test, exact-image checkpoint, artifact or backend-profile gate is missing/ambiguous, stateful workloads remain `NOT_QUALIFIED`.
