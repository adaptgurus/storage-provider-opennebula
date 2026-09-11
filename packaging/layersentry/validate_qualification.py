#!/usr/bin/env python3
"""Validate whether a LayerSentry CSI release is eligible for production promotion.

This is a promotion gate, not a test runner. It deliberately fails until a named
storage backend has passed the required live qualification matrix and immutable
release artifacts have been recorded. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

DRIVER_IDENTITY = "csi.layersentry.io"
TARGET_KUBERNETES = "1.36.4"
TARGET_RKE2 = "v1.36.4+rke2r1"
TARGET_RKE2_COMMIT = "7479a59cdd2c8ce0b8871699a24daa4b7c28cc64"
EXPECTED_IMAGE_TAGS = {
    "driver": "ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.1",
    "provisioner": "registry.k8s.io/sig-storage/csi-provisioner:v6.3.0",
    "attacher": "registry.k8s.io/sig-storage/csi-attacher:v4.13.0",
    "nodeDriverRegistrar": "registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.18.0",
    "livenessProbe": "registry.k8s.io/sig-storage/livenessprobe:v2.20.0",
}

MANDATORY_LIVE_TESTS = (
    "install",
    "discovery",
    "storageclass",
    "pvc_create",
    "pv_bind",
    "pod_mount",
    "write_recognizable_data",
    "pod_restart",
    "node_restart",
    "controller_restart",
    "detach",
    "attach",
    "worker_replacement",
    "same_data_after_replacement",
    "delete",
    "idempotent_retry",
    "duplicate_operations",
    "unknown_reconciliation",
    "tenant_isolation",
    "foreign_csi_isolation",
)

OPTIONAL_CAPABILITY_TESTS = {
    "expansion": ("volume_expansion",),
    "snapshots": ("snapshot", "snapshot_restore"),
    "clones": ("clone",),
}

NOT_OFFERED_PREFIXES = ("NOT_OFFERED", "NOT_ADVERTISED")
DIGEST_IMAGE = re.compile(r"^\S+:[^/@\s]+@sha256:[0-9a-f]{64}$")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def evidence_values(record: dict[str, Any]) -> list[str]:
    evidence = record.get("evidence")
    if not isinstance(evidence, list):
        return []
    return [str(item).strip() for item in evidence if str(item).strip()]


def has_evidence(record: dict[str, Any]) -> bool:
    return bool(evidence_values(record))


def test_map(matrix: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    tests = matrix.get("tests")
    if not isinstance(tests, list):
        errors.append("qualification matrix tests must be a list")
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for record in tests:
        if not isinstance(record, dict):
            errors.append("every qualification test record must be an object")
            continue
        test_id = str(record.get("id") or "").strip()
        if not test_id:
            errors.append("qualification test record is missing id")
            continue
        if test_id in mapped:
            errors.append(f"duplicate qualification test id: {test_id}")
            continue
        mapped[test_id] = record
    return mapped


def expected_release_images(release_lock: dict[str, Any]) -> list[str]:
    images = release_lock.get("images") or {}
    return [str(images.get(name) or "").strip() for name in EXPECTED_IMAGE_TAGS]


def validate_images(release_lock: dict[str, Any], errors: list[str]) -> None:
    images = release_lock.get("images")
    if not isinstance(images, dict):
        errors.append("release lock images must be an object")
        return
    expected_keys = set(EXPECTED_IMAGE_TAGS)
    actual_keys = set(images)
    missing = sorted(expected_keys - actual_keys)
    unexpected = sorted(actual_keys - expected_keys)
    if missing:
        errors.append(f"release lock is missing required images: {missing}")
    if unexpected:
        errors.append(
            f"release lock contains images for disabled/unreviewed components: {unexpected}"
        )
    for name, expected_tag in EXPECTED_IMAGE_TAGS.items():
        value = str(images.get(name) or "").strip()
        if not DIGEST_IMAGE.fullmatch(value):
            errors.append(f"release lock images.{name} must be image:tag@sha256:<digest>")
            continue
        if not value.startswith(expected_tag + "@sha256:"):
            errors.append(f"release lock images.{name} must use reviewed tag {expected_tag}")


def validate_qualification(matrix: dict[str, Any], release_lock: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    target = matrix.get("target") or {}
    rke2 = release_lock.get("rke2") or {}
    if target.get("kubernetes") != TARGET_KUBERNETES:
        errors.append(f"qualification target kubernetes must be {TARGET_KUBERNETES}")
    if target.get("driver_identity") != DRIVER_IDENTITY:
        errors.append(f"qualification target driver_identity must be {DRIVER_IDENTITY}")
    if target.get("rke2") != TARGET_RKE2 or rke2.get("version") != TARGET_RKE2:
        errors.append(f"qualification and release lock must target RKE2 {TARGET_RKE2}")
    if target.get("rke2_commit") != TARGET_RKE2_COMMIT or rke2.get("commit") != TARGET_RKE2_COMMIT:
        errors.append(f"qualification and release lock must pin RKE2 commit {TARGET_RKE2_COMMIT}")

    root_dir = str(rke2.get("kubeletRootDir") or "").strip()
    if not root_dir.startswith("/") or root_dir == "/":
        errors.append("release lock rke2.kubeletRootDir must be an absolute non-root path")

    validate_images(release_lock, errors)

    profile = str(matrix.get("selected_storage_profile") or "").strip()
    if not profile:
        errors.append("selected_storage_profile must name the exact qualified backend profile")
    if matrix.get("overall_status") != "QUALIFIED":
        errors.append("overall_status must be QUALIFIED")

    lock_qualification = release_lock.get("qualification") or {}
    if lock_qualification.get("status") != "QUALIFIED":
        errors.append("release lock qualification.status must be QUALIFIED")
    if str(lock_qualification.get("storageProfile") or "").strip() != profile:
        errors.append("release lock qualification.storageProfile must match selected_storage_profile")
    lock_evidence = lock_qualification.get("evidence")
    if not isinstance(lock_evidence, list) or not any(str(item).strip() for item in lock_evidence):
        errors.append("release lock qualification.evidence must reference immutable qualification evidence")

    tests = test_map(matrix, errors)
    for test_id in MANDATORY_LIVE_TESTS:
        record = tests.get(test_id)
        if record is None:
            errors.append(f"missing mandatory live qualification test: {test_id}")
            continue
        if record.get("status") != "PASS":
            errors.append(f"mandatory live qualification test {test_id} must be PASS")
        if not has_evidence(record):
            errors.append(f"mandatory live qualification test {test_id} must contain evidence")

    capabilities = release_lock.get("capabilities") or {}
    for capability, test_ids in OPTIONAL_CAPABILITY_TESTS.items():
        enabled = capabilities.get(capability)
        if not isinstance(enabled, bool):
            errors.append(f"release lock capabilities.{capability} must be boolean")
            continue
        for test_id in test_ids:
            record = tests.get(test_id)
            if record is None:
                errors.append(f"missing capability qualification record: {test_id}")
                continue
            status = str(record.get("status") or "")
            if enabled:
                if status != "PASS":
                    errors.append(f"advertised capability test {test_id} must be PASS")
                if not has_evidence(record):
                    errors.append(f"advertised capability test {test_id} must contain evidence")
            elif not status.startswith(NOT_OFFERED_PREFIXES):
                errors.append(
                    f"unqualified capability test {test_id} must remain explicitly NOT_OFFERED/NOT_ADVERTISED"
                )

    images = release_lock.get("images") or {}
    driver_image = str(images.get("driver") or "").strip()
    artifacts = matrix.get("release_artifacts") or {}
    if artifacts.get("driver_image_digest") != driver_image:
        errors.append("release_artifacts.driver_image_digest must equal the release lock driver image")
    for field in ("sbom", "provenance", "offline_image_manifest"):
        if not str(artifacts.get(field) or "").strip():
            errors.append(f"release_artifacts.{field} must reference generated immutable evidence")
    return errors


def referenced_evidence_paths(matrix: dict[str, Any], release_lock: dict[str, Any]) -> Iterable[str]:
    qualification = release_lock.get("qualification") or {}
    for value in qualification.get("evidence") or []:
        text = str(value).strip()
        if text:
            yield text
    for record in matrix.get("tests") or []:
        if isinstance(record, dict) and record.get("status") == "PASS":
            yield from evidence_values(record)
    artifacts = matrix.get("release_artifacts") or {}
    for field in ("sbom", "provenance", "offline_image_manifest"):
        text = str(artifacts.get(field) or "").strip()
        if text:
            yield text


def resolve_evidence_path(root: Path, reference: str) -> Path | None:
    path = Path(reference)
    if path.is_absolute():
        return None
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def validate_materialized_evidence(
    matrix: dict[str, Any], release_lock: dict[str, Any], evidence_root: Path
) -> list[str]:
    errors: list[str] = []
    root = evidence_root.resolve()
    seen: set[str] = set()
    for reference in referenced_evidence_paths(matrix, release_lock):
        if reference in seen:
            continue
        seen.add(reference)
        resolved = resolve_evidence_path(root, reference)
        if resolved is None:
            errors.append(f"evidence reference must be repository-relative and contained: {reference}")
            continue
        if not resolved.is_file():
            errors.append(f"evidence file does not exist: {reference}")
            continue
        try:
            if resolved.stat().st_size <= 0:
                errors.append(f"evidence file is empty: {reference}")
        except OSError as exc:
            errors.append(f"cannot stat evidence file {reference}: {exc}")

    offline_ref = str((matrix.get("release_artifacts") or {}).get("offline_image_manifest") or "").strip()
    if offline_ref:
        offline_path = resolve_evidence_path(root, offline_ref)
        if offline_path is not None and offline_path.is_file():
            lines = [line.strip() for line in offline_path.read_text().splitlines() if line.strip()]
            expected = expected_release_images(release_lock)
            if len(lines) != len(set(lines)):
                errors.append("offline image manifest must not contain duplicate image references")
            if lines != expected:
                errors.append(
                    "offline image manifest must exactly match the ordered enabled image set in release-lock.json"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--release-lock", type=Path, required=True)
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path.cwd(),
        help="repository/evidence root used to resolve all recorded evidence paths",
    )
    args = parser.parse_args()

    try:
        matrix = load_json(args.matrix)
        release_lock = load_json(args.release_lock)
        errors = validate_qualification(matrix, release_lock)
        if not errors:
            errors.extend(validate_materialized_evidence(matrix, release_lock, args.evidence_root))
    except (OSError, ValueError) as exc:
        print(f"NOT_QUALIFIED: {exc}")
        return 2

    if errors:
        print("NOT_QUALIFIED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("QUALIFIED: production promotion evidence is internally consistent and materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
