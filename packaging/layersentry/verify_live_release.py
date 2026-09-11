#!/usr/bin/env python3
"""Bind LayerSentry CSI live qualification to the exact frozen release images.

Run this after the baseline phase and again after worker-replacement/data-survival
verification. It verifies that every LayerSentry controller/node Pod is Ready and
uses exactly the digest-pinned images in the release lock, with no unreviewed
containers. The emitted evidence carries the live qualification run_id so the
production gate can prove that lifecycle evidence and release-identity evidence
belong to one qualification run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from build_chart import LAYERSENTRY, load_release_lock

CONTROLLER_IMAGES = {
    "opennebula-csi": "driver",
    "csi-provisioner": "provisioner",
    "csi-attacher": "attacher",
    "liveness-probe": "livenessProbe",
}
NODE_IMAGES = {
    "opennebula-csi": "driver",
    "csi-node-driver-registrar": "nodeDriverRegistrar",
    "liveness-probe": "livenessProbe",
}
FORBIDDEN_CONTAINERS = {"csi-resizer", "csi-snapshotter"}


class VerificationError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{path} must contain a JSON object")
    return value


def kubectl_json(prefix: list[str], args: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            prefix + args + ["-o", "json"],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError(f"kubectl failed to execute {args}: {exc}") from exc
    if result.returncode != 0:
        raise VerificationError(
            f"kubectl {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"kubectl returned invalid JSON for {args}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"kubectl JSON result for {args} is not an object")
    return value


def pod_ready(pod: dict[str, Any]) -> bool:
    statuses = pod.get("status", {}).get("containerStatuses") or []
    return (
        pod.get("status", {}).get("phase") == "Running"
        and bool(statuses)
        and all(bool(item.get("ready")) for item in statuses)
    )


def container_images(pod: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for container in pod.get("spec", {}).get("containers") or []:
        if not isinstance(container, dict):
            continue
        name = str(container.get("name") or "").strip()
        image = str(container.get("image") or "").strip()
        if not name or name in result:
            raise VerificationError("pod contains a missing or duplicate container name")
        result[name] = image
    return result


def image_ids(pod: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for status in pod.get("status", {}).get("containerStatuses") or []:
        if isinstance(status, dict):
            result[str(status.get("name") or "")] = str(status.get("imageID") or "")
    return result


def verify_pod_images(
    pod: dict[str, Any], expected_names: dict[str, str], lock: dict[str, Any]
) -> dict[str, Any]:
    name = str(pod.get("metadata", {}).get("name") or "<unknown>")
    if not pod_ready(pod):
        raise VerificationError(f"pod {name} is not Ready")
    if pod.get("spec", {}).get("initContainers"):
        raise VerificationError(f"pod {name} contains unexpected initContainers")

    actual = container_images(pod)
    actual_names = set(actual)
    required_names = set(expected_names)
    if actual_names != required_names:
        missing = sorted(required_names - actual_names)
        unexpected = sorted(actual_names - required_names)
        raise VerificationError(
            f"pod {name} container set mismatch: missing={missing} unexpected={unexpected}"
        )
    forbidden = sorted(actual_names & FORBIDDEN_CONTAINERS)
    if forbidden:
        raise VerificationError(f"pod {name} contains unqualified containers: {forbidden}")

    expected_images = lock.get("images") or {}
    for container_name, lock_key in expected_names.items():
        expected = str(expected_images.get(lock_key) or "")
        if actual[container_name] != expected:
            raise VerificationError(
                f"pod {name} container {container_name} image mismatch: "
                f"expected {expected!r}, got {actual[container_name]!r}"
            )

    return {
        "name": name,
        "uid": str(pod.get("metadata", {}).get("uid") or ""),
        "node": str(pod.get("spec", {}).get("nodeName") or ""),
        "images": actual,
        "runtime_image_ids": image_ids(pod),
    }


def verify_deployment(
    prefix: list[str], state: dict[str, Any], lock: dict[str, Any]
) -> dict[str, Any]:
    namespace = str(state.get("driver_namespace") or "").strip()
    if not namespace:
        raise VerificationError("qualification state is missing driver_namespace")

    csidriver = kubectl_json(prefix, ["get", "csidriver", LAYERSENTRY])
    if str(csidriver.get("metadata", {}).get("name") or "") != LAYERSENTRY:
        raise VerificationError(f"CSIDriver {LAYERSENTRY} is not present")

    selector = "app.kubernetes.io/name=layersentry-csi"
    pod_list = kubectl_json(prefix, ["get", "pods", "-n", namespace, "-l", selector])
    pods = [item for item in pod_list.get("items") or [] if isinstance(item, dict)]
    controllers = [
        pod for pod in pods
        if pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "controller"
    ]
    nodes = [
        pod for pod in pods
        if pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "node"
    ]
    if not controllers:
        raise VerificationError("no LayerSentry controller Pod found")
    if not nodes:
        raise VerificationError("no LayerSentry node Pod found")

    node_list = kubectl_json(prefix, ["get", "nodes"])
    ready_nodes = {
        str(item.get("metadata", {}).get("name") or "")
        for item in node_list.get("items") or []
        if isinstance(item, dict)
        and any(
            cond.get("type") == "Ready" and cond.get("status") == "True"
            for cond in item.get("status", {}).get("conditions", [])
        )
    }
    node_pod_nodes = {str(pod.get("spec", {}).get("nodeName") or "") for pod in nodes}
    missing_nodes = sorted(ready_nodes - node_pod_nodes)
    if missing_nodes:
        raise VerificationError(f"LayerSentry node Pod missing from Ready nodes: {missing_nodes}")

    return {
        "namespace": namespace,
        "controller_pods": [verify_pod_images(pod, CONTROLLER_IMAGES, lock) for pod in controllers],
        "node_pods": [verify_pod_images(pod, NODE_IMAGES, lock) for pod in nodes],
        "ready_nodes": sorted(ready_nodes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-lock", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("baseline", "final"), required=True)
    parser.add_argument("--kubeconfig")
    parser.add_argument("--context")
    args = parser.parse_args()

    evidence_dir = args.evidence_dir.resolve()
    try:
        state = load_object(evidence_dir / "state.json")
        lock_path = args.release_lock.resolve()
        lock = load_release_lock(lock_path)
        run_id = str(state.get("run_id") or "").strip()
        if not run_id:
            raise VerificationError("qualification state is missing run_id")

        prefix = ["kubectl"]
        if args.kubeconfig:
            prefix += ["--kubeconfig", args.kubeconfig]
        if args.context:
            prefix += ["--context", args.context]

        deployment = verify_deployment(prefix, state, lock)
        lock_sha = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        test_id = f"release_identity_{args.phase}"
        record = {
            "schema_version": 1,
            "product": "LayerSentry CSI",
            "test_id": test_id,
            "status": "PASS",
            "recorded_at": now(),
            "run_id": run_id,
            "target": {
                "release_version": lock["release"]["version"],
                "release_tag": lock["release"]["gitTag"],
                "source_commit": lock["release"]["sourceCommit"],
                "rke2": lock["rke2"]["version"],
                "kubernetes": "v1.36.4",
                "driver": LAYERSENTRY,
                "driver_image": lock["images"]["driver"],
                "release_lock_sha256": lock_sha,
                "storage_class": state.get("storage_class"),
            },
            "details": deployment,
        }
        output = evidence_dir / f"{test_id}.json"
        output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print(f"RELEASE_IDENTITY_PASS phase={args.phase} evidence={output}")
        return 0
    except (OSError, ValueError, VerificationError) as exc:
        print(f"RELEASE_IDENTITY_FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
