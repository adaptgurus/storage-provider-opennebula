#!/usr/bin/env python3
"""Phased live qualification for LayerSentry CSI on an existing RKE2 cluster.

The runner mutates only a generated qualification namespace plus, when explicitly
acknowledged, the LayerSentry controller pod(s). It never reboots or replaces
infrastructure. Node restart and worker replacement are operator-controlled
steps; later phases verify persistent-data survival after those actions.

The runner intentionally does not mark idempotent-retry, duplicate-operation,
UNKNOWN-outcome, tenant-isolation, or foreign-CSI-isolation tests PASS. Those
need the controlled scenarios documented in LIVE_QUALIFICATION.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

DRIVER = "csi.layersentry.io"
RKE2_VERSION = "v1.36.4+rke2r1"
KUBERNETES_VERSION = "v1.36.4"
DIGEST_IMAGE = re.compile(r"^\S+:[^/@\s]+@sha256:[0-9a-f]{64}$")


class QualificationError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


class Kubectl:
    def __init__(self, kubeconfig: str | None, context: str | None):
        self.prefix = ["kubectl"]
        if kubeconfig:
            self.prefix += ["--kubeconfig", kubeconfig]
        if context:
            self.prefix += ["--context", context]

    def run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                self.prefix + args,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QualificationError(f"kubectl failed to execute {args}: {exc}") from exc
        if check and result.returncode != 0:
            raise QualificationError(
                f"kubectl {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result

    def json(self, args: list[str], timeout: int = 120) -> dict[str, Any]:
        result = self.run(args + ["-o", "json"], timeout=timeout)
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise QualificationError(f"kubectl returned invalid JSON for {args}: {exc}") from exc
        if not isinstance(value, dict):
            raise QualificationError(f"kubectl JSON result for {args} is not an object")
        return value

    def apply(self, obj: dict[str, Any]) -> None:
        self.run(["apply", "-f", "-"], input_text=json.dumps(obj))


def node_ready(node: dict[str, Any]) -> bool:
    return any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in node.get("status", {}).get("conditions", [])
    )


def pod_ready(pod: dict[str, Any]) -> bool:
    statuses = pod.get("status", {}).get("containerStatuses") or []
    return (
        pod.get("status", {}).get("phase") == "Running"
        and bool(statuses)
        and all(bool(item.get("ready")) for item in statuses)
    )


def wait_for(getter: Callable[[], Any], predicate: Callable[[Any], bool], what: str, timeout: int) -> Any:
    deadline = time.time() + timeout
    last: Any = None
    while time.time() < deadline:
        try:
            last = getter()
            if predicate(last):
                return last
        except QualificationError:
            last = None
        time.sleep(2)
    raise QualificationError(f"timeout waiting for {what}; last={last!r}")


def record(root: Path, state: dict[str, Any], test_id: str, details: dict[str, Any]) -> None:
    write_json(
        root / f"{test_id}.json",
        {
            "schema_version": 1,
            "product": "LayerSentry CSI",
            "test_id": test_id,
            "status": "PASS",
            "recorded_at": now(),
            "run_id": state["run_id"],
            "target": {
                "rke2": RKE2_VERSION,
                "kubernetes": KUBERNETES_VERSION,
                "driver": DRIVER,
                "storage_class": state["storage_class"],
            },
            "details": details,
        },
    )


def ns_manifest(name: str, run_id: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": name,
            "labels": {
                "layersentry.io/qualification": "true",
                "layersentry.io/qualification-run": run_id,
            },
        },
    }


def pvc_manifest(namespace: str, storage_class: str, size: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": "data", "namespace": namespace},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": size}},
            "storageClassName": storage_class,
        },
    }


def probe_pod(namespace: str, image: str, node_name: str | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "securityContext": {
            "fsGroup": 65534,
            "fsGroupChangePolicy": "OnRootMismatch",
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "probe",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["sh", "-c", "trap : TERM INT; sleep 2147483647 & wait"],
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "runAsNonRoot": True,
                    "runAsUser": 65534,
                    "runAsGroup": 65534,
                },
                "volumeMounts": [{"name": "data", "mountPath": "/data"}],
            }
        ],
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data"}}],
    }
    if node_name:
        spec["nodeName"] = node_name
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "probe",
            "namespace": namespace,
            "labels": {"layersentry.io/qualification": "true"},
        },
        "spec": spec,
    }


def load_state(evidence_dir: str) -> tuple[Path, dict[str, Any]]:
    root = Path(evidence_dir).resolve()
    path = root / "state.json"
    if not path.is_file():
        raise QualificationError(f"missing qualification state {path}")
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise QualificationError(f"invalid qualification state: {exc}") from exc
    if not isinstance(state, dict):
        raise QualificationError("qualification state must be an object")
    return root, state


def get_pod(k: Kubectl, state: dict[str, Any]) -> dict[str, Any]:
    return k.json(["get", "pod", "probe", "-n", state["namespace"]])


def wait_pod(k: Kubectl, state: dict[str, Any], timeout: int) -> dict[str, Any]:
    return wait_for(lambda: get_pod(k, state), pod_ready, "probe pod Ready", timeout)


def delete_pod(k: Kubectl, state: dict[str, Any]) -> None:
    k.run(["delete", "pod", "probe", "-n", state["namespace"], "--ignore-not-found=true"])


def create_pod(k: Kubectl, state: dict[str, Any], node_name: str | None = None, wait: bool = True) -> dict[str, Any] | None:
    k.apply(probe_pod(state["namespace"], state["workload_image"], node_name))
    return wait_pod(k, state, state["timeout_seconds"]) if wait else None


def attachments(k: Kubectl, pv: str) -> list[dict[str, Any]]:
    items = k.json(["get", "volumeattachments"]).get("items") or []
    return [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("spec", {}).get("source", {}).get("persistentVolumeName") == pv
    ]


def wait_detached(k: Kubectl, state: dict[str, Any], timeout: int) -> None:
    wait_for(lambda: attachments(k, state["pv"]), lambda value: not value, "volume detached", timeout)


def marker_value(k: Kubectl, state: dict[str, Any]) -> str:
    return k.run(
        ["exec", "-n", state["namespace"], "probe", "--", "cat", "/data/layersentry-marker.txt"]
    ).stdout.strip()


def verify_marker(k: Kubectl, state: dict[str, Any]) -> None:
    actual = hashlib.sha256(marker_value(k, state).encode()).hexdigest()
    if actual != state["marker_sha256"]:
        raise QualificationError(f"marker checksum mismatch: {actual} != {state['marker_sha256']}")


def verify_cluster(k: Kubectl, root: Path, state: dict[str, Any]) -> None:
    version = k.json(["version"])
    server = str(version.get("serverVersion", {}).get("gitVersion") or "")
    if server != RKE2_VERSION:
        raise QualificationError(f"expected server {RKE2_VERSION}, got {server!r}")
    k.json(["get", "csidriver", DRIVER])

    nodes = k.json(["get", "nodes"]).get("items") or []
    ready = [node for node in nodes if isinstance(node, dict) and node_ready(node)]
    if not ready:
        raise QualificationError("no Ready Kubernetes nodes")
    versions = {
        str(node.get("metadata", {}).get("name")): str(
            node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion") or ""
        )
        for node in ready
    }
    wrong = {name: value for name, value in versions.items() if value != RKE2_VERSION}
    if wrong:
        raise QualificationError(f"node version skew detected: {wrong}")

    csinodes = k.json(["get", "csinodes"]).get("items") or []
    registered = {
        str(item.get("metadata", {}).get("name"))
        for item in csinodes
        if isinstance(item, dict)
        and DRIVER in {entry.get("name") for entry in item.get("spec", {}).get("drivers", [])}
    }
    missing = sorted(name for name in versions if name not in registered)
    if missing:
        raise QualificationError(f"CSI not registered on Ready nodes: {missing}")

    selector = "app.kubernetes.io/name=layersentry-csi"
    pods = k.json(
        ["get", "pods", "-n", state["driver_namespace"], "-l", selector]
    ).get("items") or []
    controllers = [
        pod
        for pod in pods
        if pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "controller"
    ]
    node_pods = [
        pod
        for pod in pods
        if pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "node"
    ]
    if not controllers or not all(pod_ready(pod) for pod in controllers):
        raise QualificationError("controller pod(s) are not Ready")
    if len(node_pods) < len(ready) or not all(pod_ready(pod) for pod in node_pods):
        raise QualificationError("node DaemonSet is not Ready on all Ready nodes")

    record(
        root,
        state,
        "install",
        {
            "server_version": server,
            "driver_namespace": state["driver_namespace"],
            "controller_pods": [pod.get("metadata", {}).get("name") for pod in controllers],
            "node_pods": [pod.get("metadata", {}).get("name") for pod in node_pods],
        },
    )
    record(root, state, "discovery", {"node_versions": versions, "registered_nodes": sorted(registered)})


def verify_storage_class(k: Kubectl, root: Path, state: dict[str, Any]) -> str:
    sc = k.json(["get", "storageclass", state["storage_class"]])
    if sc.get("provisioner") != DRIVER:
        raise QualificationError(f"StorageClass provisioner must be {DRIVER}")
    if sc.get("allowVolumeExpansion") is True:
        raise QualificationError("qualification StorageClass must not enable expansion")
    if sc.get("reclaimPolicy", "Delete") != "Delete":
        raise QualificationError("qualification StorageClass must use reclaimPolicy=Delete")
    mode = str(sc.get("volumeBindingMode") or "Immediate")
    if mode not in {"Immediate", "WaitForFirstConsumer"}:
        raise QualificationError(f"unsupported StorageClass volumeBindingMode {mode!r}")
    state["volume_binding_mode"] = mode
    record(
        root,
        state,
        "storageclass",
        {
            "name": state["storage_class"],
            "provisioner": DRIVER,
            "allow_volume_expansion": False,
            "reclaim_policy": "Delete",
            "volume_binding_mode": mode,
        },
    )
    return mode


def node_uid(k: Kubectl, name: str) -> str:
    node = k.json(["get", "node", name])
    if not node_ready(node):
        raise QualificationError(f"node {name} is not Ready")
    return str(node.get("metadata", {}).get("uid") or "")


def baseline(args: argparse.Namespace) -> None:
    if not args.allow_controller_restart:
        raise QualificationError("baseline requires explicit --allow-controller-restart")
    if not DIGEST_IMAGE.fullmatch(args.workload_image):
        raise QualificationError("--workload-image must be image:tag@sha256:<64 lowercase hex>")

    root = Path(args.evidence_dir).resolve()
    state_file = root / "state.json"
    if state_file.exists():
        raise QualificationError(f"state already exists at {state_file}; use a fresh evidence directory")
    run_id = uuid.uuid4().hex[:12]
    state = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": now(),
        "namespace": f"layersentry-csi-qual-{run_id}",
        "storage_class": args.storage_class,
        "workload_image": args.workload_image,
        "driver_namespace": args.driver_namespace,
        "size": args.size,
        "timeout_seconds": args.timeout,
    }
    write_json(state_file, state)
    k = Kubectl(args.kubeconfig, args.context)

    verify_cluster(k, root, state)
    mode = verify_storage_class(k, root, state)
    write_json(state_file, state)
    k.apply(ns_manifest(state["namespace"], run_id))
    k.apply(pvc_manifest(state["namespace"], state["storage_class"], state["size"]))
    record(root, state, "pvc_create", {"namespace": state["namespace"], "size": state["size"]})

    # WaitForFirstConsumer cannot bind until a schedulable consumer exists.
    if mode == "WaitForFirstConsumer":
        create_pod(k, state, wait=False)

    pvc = wait_for(
        lambda: k.json(["get", "pvc", "data", "-n", state["namespace"]]),
        lambda value: value.get("status", {}).get("phase") == "Bound",
        "PVC Bound",
        args.timeout,
    )
    state["pv"] = str(pvc.get("spec", {}).get("volumeName") or "")
    if not state["pv"]:
        raise QualificationError("Bound PVC has no PV name")
    pv = k.json(["get", "pv", state["pv"]])
    csi = pv.get("spec", {}).get("csi", {})
    if csi.get("driver") != DRIVER:
        raise QualificationError(f"PV CSI driver is {csi.get('driver')!r}, expected {DRIVER}")
    state["volume_handle"] = csi.get("volumeHandle")
    write_json(state_file, state)
    record(
        root,
        state,
        "pv_bind",
        {"pv": state["pv"], "volume_handle": state["volume_handle"], "driver": DRIVER},
    )

    pod = wait_pod(k, state, args.timeout) if mode == "WaitForFirstConsumer" else create_pod(k, state)
    if pod is None:
        raise QualificationError("probe pod was not created")
    state["current_node"] = str(pod.get("spec", {}).get("nodeName") or "")
    state["current_node_uid"] = node_uid(k, state["current_node"])
    record(root, state, "pod_mount", {"node": state["current_node"], "pv": state["pv"]})

    marker = f"layersentry-csi:{run_id}:{uuid.uuid4().hex}"
    k.run(
        [
            "exec",
            "-n",
            state["namespace"],
            "probe",
            "--",
            "sh",
            "-c",
            f"printf '%s' '{marker}' > /data/layersentry-marker.txt && sync",
        ]
    )
    state["marker_sha256"] = hashlib.sha256(marker.encode()).hexdigest()
    write_json(state_file, state)
    verify_marker(k, state)
    record(root, state, "write_recognizable_data", {"marker_sha256": state["marker_sha256"]})

    old_uid = str(pod.get("metadata", {}).get("uid") or "")
    delete_pod(k, state)
    pod = create_pod(k, state)
    if pod is None or str(pod.get("metadata", {}).get("uid") or "") == old_uid:
        raise QualificationError("pod restart did not create a new pod UID")
    verify_marker(k, state)
    record(
        root,
        state,
        "pod_restart",
        {"old_uid": old_uid, "new_uid": pod.get("metadata", {}).get("uid")},
    )

    selector = "app.kubernetes.io/name=layersentry-csi,app.kubernetes.io/component=controller"
    old = k.json(["get", "pods", "-n", state["driver_namespace"], "-l", selector]).get("items") or []
    old_uids = {str(pod.get("metadata", {}).get("uid") or "") for pod in old if isinstance(pod, dict)}
    if not old_uids:
        raise QualificationError("no controller pod found for restart test")
    k.run(["delete", "pod", "-n", state["driver_namespace"], "-l", selector, "--wait=false"])

    def new_controllers() -> list[dict[str, Any]]:
        items = k.json(["get", "pods", "-n", state["driver_namespace"], "-l", selector]).get("items") or []
        return [pod for pod in items if isinstance(pod, dict) and pod_ready(pod)]

    controllers = wait_for(
        new_controllers,
        lambda items: bool(items)
        and {str(pod.get("metadata", {}).get("uid") or "") for pod in items}.isdisjoint(old_uids),
        "replacement controller Ready",
        args.timeout,
    )
    verify_marker(k, state)
    record(
        root,
        state,
        "controller_restart",
        {
            "old_uids": sorted(old_uids),
            "new_uids": sorted(str(pod.get("metadata", {}).get("uid") or "") for pod in controllers),
        },
    )

    delete_pod(k, state)
    wait_detached(k, state, args.detach_timeout)
    record(root, state, "detach", {"pv": state["pv"], "volume_attachments": []})
    pod = create_pod(k, state)
    if pod is None:
        raise QualificationError("probe pod did not recreate after detach")
    verify_marker(k, state)
    vas = wait_for(lambda: attachments(k, state["pv"]), bool, "VolumeAttachment present", args.timeout)
    state["current_node"] = str(pod.get("spec", {}).get("nodeName") or "")
    state["current_node_uid"] = node_uid(k, state["current_node"])
    write_json(state_file, state)
    record(
        root,
        state,
        "attach",
        {
            "pv": state["pv"],
            "node": state["current_node"],
            "volume_attachments": [item.get("metadata", {}).get("name") for item in vas],
        },
    )

    print(f"BASELINE_PASS evidence={root}")
    print(f"NEXT: restart node {state['current_node']} through the authorized infrastructure lifecycle, then run after-node-restart.")


def after_node_restart(args: argparse.Namespace) -> None:
    root, state = load_state(args.evidence_dir)
    k = Kubectl(args.kubeconfig, args.context)
    node = str(state.get("current_node") or "")
    old_uid = str(state.get("current_node_uid") or "")
    new_uid = wait_for(
        lambda: node_uid(k, node),
        lambda uid: bool(uid),
        f"node {node} Ready",
        args.timeout,
    )
    if old_uid and new_uid != old_uid:
        raise QualificationError("node UID changed during restart test; this is replacement, not restart")
    pod = wait_pod(k, state, args.timeout)
    verify_marker(k, state)
    record(
        root,
        state,
        "node_restart",
        {
            "node": node,
            "node_uid": new_uid,
            "pod_node": pod.get("spec", {}).get("nodeName"),
            "marker_sha256": state["marker_sha256"],
        },
    )
    print("NODE_RESTART_PASS")
    print("NEXT: run prepare-worker-replacement, replace the worker, then run after-worker-replacement.")


def prepare_worker_replacement(args: argparse.Namespace) -> None:
    root, state = load_state(args.evidence_dir)
    k = Kubectl(args.kubeconfig, args.context)
    pod = get_pod(k, state)
    state["replacement_old_node"] = str(pod.get("spec", {}).get("nodeName") or "")
    state["replacement_old_node_uid"] = node_uid(k, state["replacement_old_node"])
    delete_pod(k, state)
    wait_detached(k, state, args.detach_timeout)
    state["worker_replacement_prepared_at"] = now()
    write_json(root / "state.json", state)
    print(
        "WORKER_REPLACEMENT_PREPARED "
        f"old_node={state['replacement_old_node']} uid={state['replacement_old_node_uid']}"
    )


def after_worker_replacement(args: argparse.Namespace) -> None:
    root, state = load_state(args.evidence_dir)
    old_name = str(state.get("replacement_old_node") or "")
    old_uid = str(state.get("replacement_old_node_uid") or "")
    if not old_name or not old_uid:
        raise QualificationError("prepare-worker-replacement has not been completed")
    k = Kubectl(args.kubeconfig, args.context)
    replacement = args.replacement_node
    replacement_uid = wait_for(
        lambda: node_uid(k, replacement),
        lambda uid: bool(uid),
        f"replacement node {replacement} Ready",
        args.timeout,
    )
    if replacement == old_name and replacement_uid == old_uid:
        raise QualificationError("worker replacement did not change the node UID")
    if replacement != old_name:
        old_result = k.run(["get", "node", old_name], check=False)
        if old_result.returncode == 0:
            raise QualificationError(f"old node {old_name} still exists; replacement is not complete")

    csinode = k.json(["get", "csinode", replacement])
    names = {entry.get("name") for entry in csinode.get("spec", {}).get("drivers", [])}
    if DRIVER not in names:
        raise QualificationError(f"replacement node {replacement} has not registered {DRIVER}")

    pod = create_pod(k, state, replacement)
    if pod is None or pod.get("spec", {}).get("nodeName") != replacement:
        raise QualificationError("probe did not run on the replacement node")
    verify_marker(k, state)
    state["current_node"] = replacement
    state["current_node_uid"] = replacement_uid
    write_json(root / "state.json", state)
    details = {
        "old_node": old_name,
        "old_node_uid": old_uid,
        "replacement_node": replacement,
        "replacement_node_uid": replacement_uid,
        "marker_sha256": state["marker_sha256"],
    }
    record(root, state, "worker_replacement", details)
    record(root, state, "same_data_after_replacement", details)
    print("WORKER_REPLACEMENT_DATA_SURVIVAL_PASS")


def cleanup(args: argparse.Namespace) -> None:
    root, state = load_state(args.evidence_dir)
    k = Kubectl(args.kubeconfig, args.context)
    delete_pod(k, state)
    wait_detached(k, state, args.detach_timeout)
    k.run(["delete", "pvc", "data", "-n", state["namespace"], "--ignore-not-found=true"])

    def pv_gone() -> bool:
        return k.run(["get", "pv", state["pv"]], check=False).returncode != 0

    wait_for(pv_gone, bool, f"PV {state['pv']} deletion", args.timeout)
    record(root, state, "delete", {"pv": state["pv"], "reclaim_policy": "Delete"})
    k.run(["delete", "namespace", state["namespace"], "--ignore-not-found=true"])
    state["cleaned_up_at"] = now()
    write_json(root / "state.json", state)
    print("DELETE_AND_CLEANUP_PASS")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig")
    parser.add_argument("--context")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--detach-timeout", type=int, default=600)
    phases = parser.add_subparsers(dest="phase", required=True)

    base = phases.add_parser("baseline")
    base.add_argument("--driver-namespace", required=True)
    base.add_argument("--storage-class", required=True)
    base.add_argument("--workload-image", required=True)
    base.add_argument("--size", default="1Gi")
    base.add_argument("--allow-controller-restart", action="store_true")

    phases.add_parser("after-node-restart")
    phases.add_parser("prepare-worker-replacement")
    replacement = phases.add_parser("after-worker-replacement")
    replacement.add_argument("--replacement-node", required=True)
    phases.add_parser("cleanup")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        {
            "baseline": baseline,
            "after-node-restart": after_node_restart,
            "prepare-worker-replacement": prepare_worker_replacement,
            "after-worker-replacement": after_worker_replacement,
            "cleanup": cleanup,
        }[args.phase](args)
    except (QualificationError, KeyError) as exc:
        print(f"QUALIFICATION_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
