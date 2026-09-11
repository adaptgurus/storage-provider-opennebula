# LayerSentry CSI identity and migration contract

Status: source contract; live qualification still required.

## Single identity source

The Helm value `driver.name` is the deployment identity authority. If it is omitted, the chart deliberately defaults to the legacy upstream identity `csi.opennebula.io`. The LayerSentry release profile sets it to `csi.layersentry.io`.

The same resolved value is used for:

- controller `--drivername`;
- node `--drivername`;
- `CSIDriver.metadata.name`;
- every generated `StorageClass.provisioner`;
- kubelet CSI plugin directory and node-driver-registrar socket path;
- the identity registered by node-driver-registrar and therefore the resulting `CSINode.spec.drivers[].name` entry;
- attachment reconciliation ownership filters (`PersistentVolume.spec.csi.driver` and `VolumeAttachment.spec.attacher`).

`driver.extraArgs` is not allowed to contain `--drivername`; Helm rendering fails if it does. This prevents a second identity source from overriding only the binary while Kubernetes resources keep another identity.

Metric names retain the existing `opennebula_csi_*` prefix for time-series backward compatibility. They are observability names, not Kubernetes CSI ownership keys, and must not be used to decide resource ownership or reconciliation.

## Kubelet path

The chart derives CSI plugin and registration paths from `kubelet.rootDir`, defaulting to `/var/lib/kubelet`. The LayerSentry RKE2 1.36 release lock must record the exact kubelet root used by the target cluster. For the currently selected RKE2 v1.36.4+rke2r1 candidate, source/default verification uses `/var/lib/kubelet`; a site that overrides kubelet `--root-dir` must set the same value in the CSI profile.

## CSINode behavior

The chart does not create or patch `CSINode` resources. Kubernetes kubelet/node-driver-registrar owns those entries. Registering the socket under a different CSI driver name creates a distinct driver entry; it does not migrate existing PVs.

## Legacy bound PVs

Never rewrite an existing bound PV `spec.csi.driver` from `csi.opennebula.io` to `csi.layersentry.io` to simulate migration. Kubernetes treats the CSI driver name as the storage-provider identity, not a cosmetic label.

Legacy volumes must remain served by the legacy identity until one of these separately qualified paths exists:

1. a compatibility deployment intentionally running with `--drivername=csi.opennebula.io`; or
2. an application/data migration to a newly provisioned LayerSentry claim, with data verification and rollback evidence.

Do not run independent controllers with different identities against the same owned volume unless the coexistence design has explicitly proven that their resource ownership cannot overlap.

## Snapshot classes

The LayerSentry release profile does not advertise snapshots or clones. It therefore does not create a `VolumeSnapshotClass`. If snapshot support is later qualified, any `VolumeSnapshotClass.driver` must use the same resolved `driver.name`; snapshot/restore and clone tests must pass before the capability is enabled.

## Independent storage plugins

Direct Ceph CSI, vendor SAN/NAS CSI, NFS CSI, or other third-party storage drivers keep their own driver identities, lifecycle controllers and qualification matrices. LayerSentry CSI does not claim those paths and its attachment reconciler must ignore their PVs and VolumeAttachments.
