#!/usr/bin/env python3
"""Phased live qualification runner for LayerSentry CSI on an existing RKE2 cluster.

The runner intentionally does not provision, reboot, or replace infrastructure.
It performs Kubernetes-scoped lifecycle tests and writes per-test JSON evidence.
Node restart and worker replacement remain explicit operator actions; later
phases verify recovery and recognizable-data survival after those actions.

This runner does not mark idempotent-retry, duplicate-operation, UNKNOWN-outcome,
or tenant-isolation tests PASS. Those require controlled fault/authorization
scenarios described in LIVE_QUALIFICATION.md and separate evidence.
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
from typing import Any

DRIVER = "csi.layersentry.io"
RKE2_VERSION = "v1.36.4+rke2r1"
KUBERNETES_VERSION = "v1.36.4"
DIGEST_IMAGE = re.compile(r"^\S+:[^/@\s]+@sha256:[0-9a-f]{64}$")


class QualificationError(RuntimeError):
    pass


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
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
            raise QualificationError(f"kubectl execution failed for {args}: {exc}") from exc
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
            raise QualificationError(f"kubectl JSON for {args} is not an object")
        return value

    def apply(self, obj: dict[str, Any]) -> None:
        self.run(["apply", "-f", "-"], input_text=json.dumps(obj))


def condition_ready(node: dict[str, Any]) -> bool:
    for condition in node.get("status", {}).get("conditions", []):
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    return False


def pod_ready(pod: dict[str, Any]) -> bool:
    if pod.get("status", {}).get("phase") != "Running":
        return False
    statuses = pod.get("status", {}).get("containerStatuses") or []
    return bool(statuses) and all(bool(status.get("ready")) for status in statuses)


def wait_for(getter, predicate, description: str, timeout: int) -> Any:
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
    raise QualificationError(f"timed out after {timeout}s waiting for {description}; last={last!r}")


def evidence_record(
    evidence_dir: Path,
    test_id: str,
    status: str,
    state: dict[str, Any],
    details: dict[str, Any],
) -> str:
    record = {
        "schema_version": 1,
        "product": "LayerSentry CSI",
        "test_id": test_id,
        "status": status,
        "recorded_at": utcnow(),
        "run_id": state["run_id"],
        "target": {
            "rke2": RKE2_VERSION,
            "kubernetes": KUBERNETES_VERSION,
            "driver": DRIVER,
            "storage_class": state["storage_class"],
        },
        "details": details,
    }
    path = evidence_dir / f"{test_id}.json"
    atomic_json(path, record)
    return str(path)


def require_digest_image(image: str) -> None:
    if not DIGEST_IMAGE.fullmatch(image):
        raise QualificationError("--workload-image must be image:tag@sha256:<64 lowercase hex>")


def namespace_manifest(name: str, run_id: str) -> dict[str, Any]:
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


def pvc_manifest(namespace: str, name: str, storage_class: str, size: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": size}},
            "storageClassName": storage_class,
        },
    }


def pod_manifest(
    namespace: str,
    name: str,
    pvc: str,
    image: str,
    node_name: str | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
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
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": pvc}}],
    }
    if node_name:
        spec["nodeName"] = node_name
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"layersentry.io/qualification": "true"},
        },
        "spec": spec,
    }


def verify_cluster(k: Kubectl, state: dict[str, Any], evidence_dir: Path, driver_namespace: str) -> None:
    version = k.json(["version"])
    server = str(version.get("serverVersion", {}).get("gitVersion") or "")
    if server != RKE2_VERSION:
        raise QualificationError(f"expected Kubernetes/RKE2 server {RKE2_VERSION}, got {server!r}")

    csidriver = k.json(["get", "csidriver", DRIVER])
    if csidriver.get("metadata", {}).get("name") != DRIVER:
        raise QualificationError("LayerSentry CSIDriver is not installed")

    nodes = k.json(["get", "nodes"]).get("items") or []
    ready_nodes = [node for node in nodes if isinstance(node, dict) and condition_ready(node)]
    if not ready_nodes:
        raise QualificationError("cluster has no Ready nodes")
    node_versions = {
        node.get("metadata", {}).get("name"): node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion")
        for node in ready_nodes
    }
    wrong_versions = {name: version for name, version in node_versions.items() if version != RKE2_VERSION}
    if wrong_versions:
        raise QualificationError(f"Ready nodes are not all on {RKE2_VERSION}: {wrong_versions}")

    csinodes = k.json(["get", "csinodes"]).get("items") or []
    registered = set()
    for item in csinodes:
        if not isinstance(item, dict):
            continue
        names = {driver.get("name") for driver in item.get("spec", {}).get("drivers", [])}
        if DRIVER in names:
            registered.add(item.get("metadata", {}).get("name"))
    missing = sorted(name for name in node_versions if name not in registered)
    if missing:
        raise QualificationError(f"LayerSentry CSI is not registered on Ready nodes: {missing}")

    pods = k.json(
        [
            "get",
            "pods",
            "-n",
            driver_namespace,
            "-l",
            "app.kubernetes.io/name=layersentry-csi",
        ]
    ).get("items") or []
    controllers = [
        pod for pod in pods
        if pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "controller"
    ]
    node_pods = [
        pod for pod in pods
        if pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "node"
    ]
    if not controllers or not all(pod_ready(pod) for pod in controllers):
        raise QualificationError("LayerSentry controller pod(s) are not Ready")
    if len(node_pods) < len(ready_nodes) or not all(pod_ready(pod) for pod in node_pods):
        raise QualificationError("LayerSentry node DaemonSet is not Ready on all Ready nodes")

    evidence_record(
        evidence_dir,
        "install",
        "PASS",
        state,
        {
            "server_version": server,
            "driver_namespace": driver_namespace,
            "controller_pods": [pod.get("metadata", {}).get("name") for pod in controllers],
            "node_pods": [pod.get("metadata", {}).get("name") for pod in node_pods],
        },
    )
    evidence_record(
        evidence_dir,
        "discovery",
        "PASS",
        state,
        {"ready_node_versions": node_versions, "csinode_registered_nodes": sorted(registered)},
    )


def verify_storage_class(k: Kubectl, state: dict[str, Any], evidence_dir: Path) -> None:
    storage_class = k.json(["get", "storageclass", state["storage_class"]])
    if storage_class.get("provisioner") != DRIVER:
        raise QualificationError(
            f"StorageClass provisioner is {storage_class.get('provisioner')!r}, expected {DRIVER}"
        )
    if storage_class.get("allowVolumeExpansion") is True:
        raise QualificationError("LayerSentry production candidate must not advertise volume expansion")
    reclaim = storage_class.get("reclaimPolicy", "Delete")
    if reclaim != "Delete":
        raise QualificationError("qualification StorageClass must use reclaimPolicy=Delete to test DeleteVolume")
    evidence_record(
        evidence_dir,
        "storageclass",
        "PASS",
        state,
        {
            "name": state["storage_class"],
            "provisioner": DRIVER,
            "allow_volume_expansion": storage_class.get("allowVolumeExpansion", False),
            "reclaim_policy": reclaim,
            "volume_binding_mode": storage_class.get("volumeBindingMode", "Immediate"),
        },
    )


def get_pod(k: Kubectl, state: dict[str, Any]) -> dict[str, Any]:
    return k.json(["get", "pod", state["pod"], "-n", state["namespace"]])


def wait_pod_ready(k: Kubectl, state: dict[str, Any], timeout: int) -> dict[str, Any]:
    return wait_for(lambda: get_pod(k, state), pod_ready, f"pod {state['pod']} Ready", timeout)


def read_marker(k: Kubectl, state: dict[str, Any]) -> str:
    result = k.run(
        [
            "exec",
            "-n",
            state["namespace"],
            state["pod"],
            "--",
            "sh",
            "-c",
            "cat /data/layersentry-marker.txt",
        ]
    )
    return result.stdout.strip()


def verify_marker(k: Kubectl, state: dict[str, Any]) -> None:
    observed = read_marker(k, state)
    observed_sha = hashlib.sha256(observed.encode()).hexdigest()
    if observed_sha != state["marker_sha256"]:
        raise QualificationError(
            f"persistent marker checksum mismatch: expected {state['marker_sha256']}, got {observed_sha}"
        )


def volume_attachments(k: Kubectl, pv: str) -> list[dict[str, Any]]:
    items = k.json(["get", "volumeattachments"]).get("items") or []
    return [
        item for item in items
        if isinstance(item, dict)
        and item.get("spec", {}).get("source", {}).get("persistentVolumeName") == pv
    ]


def wait_detached(k: Kubectl, pv: str, timeout: int) -> None:
    wait_for(lambda: volume_attachments(k, pv), lambda items: not items, f"PV {pv} detached", timeout)


def create_probe_pod(k: Kubectl, state: dict[str, Any], node_name: str | None = None) -> dict[str, Any]:
    k.apply(
        pod_manifest(
            state["namespace"],
            state["pod"],
            state["pvc"],
            state["workload_image"],
            node_name,
        )
    )
    return wait_pod_ready(k, state, state["timeout_seconds"])


def delete_probe_pod(k: Kubectl, state: dict[str, Any]) -> None:
    k.run(
        ["delete", "pod", state["pod"], "-n", state["namespace"], "--ignore-not-found=true"],
        check=True,
    )


def baseline(args: argparse.Namespace) -> None:
    evidence_dir = Path(args.evidence_dir).resolve()
    state_path = evidence_dir / "state.json"
    if state_path.exists():
        raise QualificationError(f"state already exists: {state_path}; use a new evidence directory")
    require_digest_image(args.workload_image)
    run_id = uuid.uuid4().hex[:12]
    state = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": utcnow(),
        "namespace": f"layersentry-csi-qual-{run_id}",
        "pvc": "data",
        "pod": "probe",
        "storage_class": args.storage_class,
        "workload_image": args.workload_image,
        "driver_namespace": args.driver_namespace,
        "size": args.size,
        "timeout_seconds": args.timeout,
    }
    atomic_json(state_path, state)
    k = Kubectl(args.kubeconfig, args.context)

    verify_cluster(k, state, evidence_dir, args.driver_namespace)
    verify_storage_class(k, state, evidence_dir)
    k.apply(namespace_manifest(state["namespace"], run_id))
    k.apply(pvc_manifest(state["namespace"], state["pvc"], state["storage_class"], state["size"]))
    evidence_record(
        evidence_dir,
        "pvc_create",
        "PASS",
        state,
        {"namespace": state["namespace"], "pvc": state["pvc"], "size": state["size"]},
    )

    pvc = wait_for(
        lambda: k.json(["get", "pvc", state["pvc"], "-n", state["namespace"]]),
        lambda obj: obj.get("status", {}).get("phase") == "Bound",
        "PVC bound",
        args.timeout,
    )
    pv_name = str(pvc.get("spec", {}).get("volumeName") or "")
    if not pv_name:
        raise QualificationError("bound PVC has no volumeName")
    pv = k.json(["get", "pv", pv_name])
    if pv.get("spec", {}).get("csi", {}).get("driver") != DRIVER:
        raise QualificationError("bound PV does not use the LayerSentry CSI identity")
    state["pv"] = pv_name
    state["volume_handle"] = pv.get("spec", {}).get("csi", {}).get("volumeHandle")
    atomic_json(state_path, state)
    evidence_record(
        evidence_dir,
        "pv_bind",
        "PASS",
        state,
        {"pv": pv_name, "volume_handle": state["volume_handle"], "csi_driver": DRIVER},
    )

    pod = create_probe_pod(k, state)
    state["initial_node"] = pod.get("spec", {}).get("nodeName")
    evidence_record(
        evidence_dir,
        "pod_mount",
        "PASS",
        state,
        {"pod": state["pod"], "node": state["initial_node"], "pv": state["pv"]},
    )

    marker = f"layersentry-csi:{run_id}:{uuid.uuid4().hex}"
    k.run(
        [
            "exec",
            "-n",
            state["namespace"],
            state["pod"],
            "--",
            "sh",
            "-c",
            f"printf '%s' '{marker}' > /data/layersentry-marker.txt && sync",
        ]
    )
    state["marker_sha256"] = hashlib.sha256(marker.encode()).hexdigest()
    atomic_json(state_path, state)
    verify_marker(k, state)
    evidence_record(
        evidence_dir,
        "write_recognizable_data",
        "PASS",
        state,
        {"marker_sha256": state["marker_sha256"], "path": "/data/layersentry-marker.txt"},
    )

    old_uid = pod.get("metadata", {}).get("uid")
    delete_probe_pod(k, state)
    pod = create_probe_pod(k, state)
    if pod.get("metadata", {}).get("uid") == old_uid:
        raise QualificationError("pod restart did not create a new pod UID")
    verify_marker(k, state)
    evidence_record(
        evidence_dir,
        "pod_restart",
        "PASS",
        state,
        {"old_uid": old_uid, "new_uid": pod.get("metadata", {}).get("uid")},
    )

    controller_selector = "app.kubernetes.io/name=layersentry-csi,app.kubernetes.io/component=controller"
    controllers = k.json(
        ["get", "pods", "-n", args.driver_namespace, "-l", controller_selector]
    ).get("items") or []
    old_controller_uids = sorted(
        str(pod.get("metadata", {}).get("uid")) for pod in controllers if isinstance(pod, dict)
    )
    if not old_controller_uids:
        raise QualificationError("cannot find LayerSentry controller pod for restart test")
    k.run(
        [
            "delete",
            "pod",
            "-n",
            args.driver_namespace,
            "-l",
            controller_selector,
            "--wait=false",
        ]
    )

    def replacement_controllers():
        items = k.json(
            ["get", "pods", "-n", args.driver_namespace, "-l", controller_selector]
        ).get("items") or []
        new_ready = [pod for pod in items if isinstance(pod, dict) and pod_ready(pod)]
        new_uids = sorted(str(pod.get("metadata", {}).get("uid")) for pod in new_ready)
        return {"pods": new_ready, "uids": new_uids}

    restarted = wait_for(
        replacement_controllers,
        lambda data: bool(data["uids"]) and set(data["uids"]).isdisjoint(old_controller_uids),
        "replacement LayerSentry controller Ready",
        args.timeout,
    )
    verify_marker(k, state)
    evidence_record(
        evidence_dir,
        "controller_restart",
        "PASS",
        state,
        {"old_uids": old_controller_uids, "new_uids": restarted["uids"]},
    )

    delete_probe_pod(k, state)
    wait_detached(k, state["pv"], args.detach_timeout)
    evidence_record(
        evidence_dir,
        "detach",
        "PASS",
        state,
        {"pv": state["pv"], "volume_attachments_after_detach": []},
    )
    pod = create_probe_pod(k, state)
    verify_marker(k, state)
    attachments = wait_for(
        lambda: volume_attachments(k, state["pv"]),
        lambda items: bool(items),
        f"PV {state['pv']} attached",
        args.timeout,
    )
    state["current_node"] = pod.get("spec", {}).get("nodeName")
    atomic_json(state_path, state)
    evidence_record(
        evidence_dir,
        "attach",
        "PASS",
        state,
        {
            "pv": state["pv"],
            "node": state["current_node"],
            "volume_attachments": [item.get("metadata", {}).get("name") for item in attachments],
        },
    )

    print(f"BASELINE_PASS evidence={evidence_dir}")
    print(f"NEXT: restart Kubernetes node {state['current_node']} using the authorized infrastructure lifecycle, then run after-node-restart.")


def load_state(evidence_dir: str) -> tuple[Path, dict[str, Any]]:
    root = Path(evidence_dir).resolve()
    path = root / "state.json"
    if not path.is_file():
        raise QualificationError(f"missing qualification state: {path}")
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise QualificationError(f"invalid qualification state {path}: {exc}") from exc
    if not isinstance(state, dict):
        raise QualificationError("qualification state must be a JSON object")
    return root, state


def after_node_restart(args: argparse.Namespace) -> None:
    evidence_dir, state = load_state(args.evidence_dir)
    k = Kubectl(args.kubeconfig, args.context)
    node_name = str(state.get("current_node") or "")
    if not node_name:
        raise QualificationError("state does not contain current_node")
    node = wait_for(
        lambda: k.json(["get", "node", node_name]),
        condition_ready,
        f"node {node_name} Ready after restart",
        args.timeout,
    )
    pod = wait_pod_ready(k, state, args.timeout)
    verify_marker(k, state)
    evidence_record(
        evidence_dir,
        "node_restart",
        "PASS",
        state,
        {
            "node": node_name,
            "node_uid": node.get("metadata", {}).get("uid"),
            "pod_node_after_restart": pod.get("spec", {}).get("nodeName"),
            "marker_sha256": state["marker_sha256"],
        },
    )
    print("NODE_RESTART_PASS")
    print("NEXT: run prepare-worker-replacement before replacing/removing the current worker.")


def prepare_worker_replacement(args: argparse.Namespace) -> None:
    evidence_dir, state = load_state(args.evidence_dir)
    k = Kubectl(args.kubeconfig, args.context)
    pod = get_pod(k, state)
    state["replacement_old_node"] = pod.get("spec", {}).get("nodeName")
    delete_probe_pod(k, state)
    wait_detached(k, state["pv"], args.detach_timeout)
    state["worker_replacement_prepared_at"] = utcnow()
    atomic_json(evidence_dir / "state.json", state)
    print(f"WORKER_REPLACEMENT_PREPARED old_node={state['replacement_old_node']}")
    print("NEXT: replace that worker through the authorized LayerSentry/OneKS lifecycle, then run after-worker-replacement --replacement-node <new-node>.")


def after_worker_replacement(args: argparse.Namespace) -> None:
    evidence_dir, state = load_state(args.evidence_dir)
    old_node = str(state.get("replacement_old_node") or "")
    if not old_node:
        raise QualificationError("prepare-worker-replacement has not been run")
    if not args.replacement_node or args.replacement_node == old_node:
        raise QualificationError("--replacement-node must name a different replacement Kubernetes node")
    k = Kubectl(args.kubeconfig, args.context)
    new_node = wait_for(
        lambda: k.json(["get", "node", args.replacement_node]),
        condition_ready,
        f"replacement node {args.replacement_node} Ready",
        args.timeout,
    )
    csinode = k.json(["get", "csinode", args.replacement_node])
    drivers = {entry.get("name") for entry in csinode.get("spec", {}).get("drivers", [])}
    if DRIVER not in drivers:
        raise QualificationError(f"replacement node {args.replacement_node} has not registered {DRIVER}")

    pod = create_probe_pod(k, state, args.replacement_node)
    if pod.get("spec", {}).get("nodeName") != args.replacement_node:
        raise QualificationError("probe pod is not running on the requested replacement node")
    verify_marker(k, state)
    state["current_node"] = args.replacement_node
    state["replacement_node_uid"] = new_node.get("metadata", {}).get("uid")
    atomic_json(evidence_dir / "state.json", state)
    details = {
        "old_node": old_node,
        "replacement_node": args.replacement_node,
        "replacement_node_uid": state["replacement_node_uid"],
        "marker_sha256": state["marker_sha256"],
    }
    evidence_record(evidence_dir, "worker_replacement", "PASS", state, details)
    evidence_record(evidence_dir, "same_data_after_replacement", "PASS", state, details)
    print("WORKER_REPLACEMENT_DATA_SURVIVAL_PASS")
    print("NEXT: run cleanup only after collecting any required fault-injection/tenant-isolation evidence.")


def cleanup(args: argparse.Namespace) -> None:
    evidence_dir, state = load_state(args.evidence_dir)
    k = Kubectl(args.kubeconfig, args.context)
    delete_probe_pod(k, state)
    wait_detached(k, state["pv"], args.detach_timeout)
    k.run(
        ["delete", "pvc", state["pvc"], "-n", state["namespace"], "--ignore-not-found=true"]
    )

    def pv_absent():
        result = k.run(["get", "pv", state["pv"]], check=False)
        return result.returncode != 0

    wait_for(pv_absent, bool, f"PV {state['pv']} deleted by reclaim policy", args.timeout)
    evidence_record(
        evidence_dir,
        "delete",
        "PASS",
        state,
        {"pv": state["pv"], "pvc": state["pvc"], "reclaim_policy": "Delete"},
    )
    k.run(["delete", "namespace", state["namespace"], "--ignore-not-found=true"], check=True)
    state["cleaned_up_at"] = utcnow()
    atomic_json(evidence_dir / "state.json", state)
    print("DELETE_AND_CLEANUP_PASS")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kubeconfig")
    p.add_argument("--context")
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--detach-timeout", type=int, default=600)
    sub = p.add_subparsers(dest="phase", required=True)

    start = sub.add_parser("baseline")
    start.add_argument("--driver-namespace", required=True)
    start.add_argument("--storage-class", required=True)
    start.add_argument("--workload-image", required=True)
    start.add_argument("--size", default="1Gi")

    sub.add_parser("after-node-restart")
    sub.add_parser("prepare-worker-replacement")
    replacement = sub.add_parser("after-worker-replacement")
    replacement.add_argument("--replacement-node", required=True)
    sub.add_parser("cleanup")
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        if args.phase == "baseline":
            baseline(args)
        elif args.phase == "after-node-restart":
            after_node_restart(args)
        elif args.phase == "prepare-worker-replacement":
            prepare_worker_replacement(args)
        elif args.phase == "after-worker-replacement":
            after_worker_replacement(args)
        elif args.phase == "cleanup":
            cleanup(args)
        else:
            raise QualificationError(f"unsupported phase {args.phase}")
    except QualificationError as exc:
        print(f"QUALIFICATION_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
