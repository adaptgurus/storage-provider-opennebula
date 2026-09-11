# LayerSentry CSI live qualification — RKE2 v1.36.4+rke2r1

This document defines the **live** T4 qualification evidence. Source CI, Helm render tests, unit tests, the inherited Kubernetes v1.31.4 E2E harness, and successful image builds are not substitutes for these tests.

## Fixed target

- RKE2: `v1.36.4+rke2r1`
- Kubernetes: `v1.36.4`
- RKE2 release-tag commit: `7479a59cdd2c8ce0b8871699a24daa4b7c28cc64`
- CSI identity: `csi.layersentry.io`
- kubelet root: record the real cluster value; candidate default is `/var/lib/kubelet`
- storage backend: exactly one named `selected_storage_profile` per qualification result

Do not reuse the result for another datastore type, vendor/model, firmware, transport, multipath policy, StorageClass parameter set, node OS/kernel, or topology unless that profile is separately qualified.

## Prerequisites

Before running live tests, freeze and retain:

1. the exact LayerSentry source commit;
2. the LayerSentry driver `image:tag@sha256` reference;
3. exact sidecar digest references;
4. the generated SBOM and provenance evidence;
5. the offline image manifest;
6. the selected storage-profile record;
7. a dedicated qualification StorageClass with `provisioner: csi.layersentry.io`, `reclaimPolicy: Delete`, and expansion disabled for the current release profile;
8. an immutable digest-pinned probe image available to the RKE2 cluster;
9. a non-production or explicitly approved qualification cluster/backend capacity.

Do not run worker replacement, API fault injection, or controller disruption against an unrelated production workload.

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

The baseline refuses the wrong RKE2 patch version and verifies:

- `CSIDriver/csi.layersentry.io` exists;
- every Ready node reports the exact RKE2 kubelet version;
- every Ready node has the LayerSentry entry in `CSINode`;
- controller and node CSI pods are Ready;
- StorageClass ownership and current capability restrictions;
- both `Immediate` and `WaitForFirstConsumer` binding modes;
- PVC creation and PV binding;
- bound PV `spec.csi.driver == csi.layersentry.io`;
- pod mount;
- recognizable marker write and checksum;
- pod deletion/recreation with identical data;
- explicit CSI controller pod restart with identical data;
- detach with no matching `VolumeAttachment` remaining;
- reattach with matching `VolumeAttachment` and identical data.

The probe pod is non-root, drops all Linux capabilities, disables service-account token mounting, uses RuntimeDefault seccomp, and uses an `fsGroup` for writable filesystem volumes.

### 2. Node restart

After baseline, restart **the reported Kubernetes node** using the approved OpenNebula/LayerSentry lifecycle. Do not replace it in this phase. Then run:

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

Then verify the replacement:

```bash
python3 packaging/layersentry/live_qualification.py \
  --kubeconfig /secure/path/rke2.yaml \
  --evidence-dir evidence/t4/<profile>/<run> \
  after-worker-replacement \
  --replacement-node <replacement-kubernetes-node>
```

PASS requires:

- the replacement node is Ready;
- the replacement has a different Kubernetes node UID (or a different name with the old node removed);
- `CSINode` registers `csi.layersentry.io` on the replacement;
- the test Pod mounts the original PVC on the replacement;
- the marker checksum is identical to the pre-replacement checksum.

This produces the mandatory `worker_replacement` and `same_data_after_replacement` evidence.

### 5. Delete/cleanup

Run cleanup only after all fault/authorization evidence that needs the live PVC has been collected:

```bash
python3 packaging/layersentry/live_qualification.py \
  --kubeconfig /secure/path/rke2.yaml \
  --evidence-dir evidence/t4/<profile>/<run> \
  cleanup
```

PASS requires detach, PVC deletion, and disappearance of the dynamically provisioned PV under `reclaimPolicy: Delete`.

## Controlled tests that are not automatically marked PASS

The following tests remain production blockers until separately executed and evidenced. A screenshot or statement such as “tested OK” is not sufficient. Evidence must identify the run, exact source/image, request/object IDs, timestamps, expected behavior, actual behavior, and relevant Kubernetes/OpenNebula state before and after the fault.

### Idempotent retry

Exercise at least one mutating CSI operation where the first attempt reaches the backend and the client/sidecar retries because the acknowledgement is lost or delayed. Acceptable operations include CreateVolume, ControllerPublishVolume, ControllerUnpublishVolume, or DeleteVolume depending on the selected profile.

PASS requires:

- one logical Kubernetes volume/attachment outcome;
- no duplicate backend volume or attachment;
- retry returns the existing/successful state or safely converges to it;
- persistent data remains correct;
- reconciler state returns to healthy without operator database edits.

Do **not** count a second `kubectl delete` returning NotFound as CSI idempotency evidence.

### Duplicate operations

Generate genuinely overlapping/repeated CSI mutations for the same volume/target through an approved test mechanism. Source-level operation-lock tests are not live evidence.

PASS requires:

- duplicate requests are coalesced/rejected/idempotently resolved;
- no duplicate OpenNebula disk attachment or second volume is created;
- no persistent `VolumeAttachment`/backend metadata drift remains;
- the workload can remount and verify the marker afterward.

### UNKNOWN reconciliation / lost acknowledgement

Introduce a bounded communication failure between the CSI controller and the OpenNebula API **after mutation can occur but before the caller can rely on the reply**. The fault mechanism must be reversible and scoped to the qualification driver/controller; do not blackhole unrelated management traffic.

Capture Kubernetes events/logs and OpenNebula object state throughout the test.

PASS requires:

- the driver does not blindly repeat an unsafe mutation;
- the reconciler/diagnostic path determines or safely quarantines ambiguous state;
- no double attachment, duplicate volume, or destructive cleanup occurs;
- after communication is restored, state converges or remains explicitly quarantined for bounded operator review;
- the original marker is readable after safe recovery.

### Tenant isolation

Use two independently authorized tenant/user contexts that cannot administer each other's qualification resources. Do not emulate this by merely changing a namespace label while using the same admin credentials.

PASS requires negative attempts from tenant B against tenant A's PVC/PV-facing LayerSentry workflow and any exposed LayerSentry/API operation to be denied without mutation. Capture the denied request, authorization identity, resource IDs, and post-test state.

### Foreign CSI isolation

On a qualification cluster where an independent CSI plugin is installed, use a disposable volume owned by that foreign driver.

PASS requires the LayerSentry attachment reconciler to ignore its PV and VolumeAttachment during normal scans and during a stale/ambiguous LayerSentry attachment scenario. The foreign volume must remain mounted/healthy and unchanged. Do not create fake ownership by editing a bound PV's `spec.csi.driver`.

## Optional capabilities

Current LayerSentry release profile does not offer expansion, snapshots, snapshot restore, or clone. Keep the relevant matrix records explicitly `NOT_OFFERED` / `NOT_ADVERTISED`.

If a future backend profile promotes one of these capabilities, first make a deliberate source/release-profile change, then run the matching live data-integrity/recovery tests. Never enable them through site-values overrides alone.

## Evidence acceptance

The production validator requires repository-relative, existing, nonempty evidence paths. Before a production tag:

- set every mandatory passed test to `PASS` in `qualification-matrix.json` and list its evidence path(s);
- set `selected_storage_profile` to the exact profile ID;
- freeze `release-lock.json` with real image digests and the same profile ID;
- record generated SBOM, provenance, and offline-manifest paths;
- run `validate_qualification.py --evidence-root .`;
- keep stateful workloads `NOT_QUALIFIED` if **any** mandatory test remains missing, ambiguous, or unsupported.
