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
from typing import Any

DRIVER_IDENTITY = "csi.layersentry.io"
TARGET_KUBERNETES = "1.36"

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

NOT_OFFERED_PREFIXES = (
    "NOT_OFFERED",
    "NOT_ADVERTISED",
)

DIGEST_IMAGE = re.compile(r"^\S+:[^/@\s]+@sha256:[0-9a-f]{64}$")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def has_evidence(record: dict[str, Any]) -> bool:
    evidence = record.get("evidence")
    return isinstance(evidence, list) and any(str(item).strip() for item in evidence)


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


def validate_qualification(matrix: dict[str, Any], release_lock: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    target = matrix.get("target") or {}
    rke2 = release_lock.get("rke2") or {}
    if target.get("kubernetes") != TARGET_KUBERNETES:
        errors.append(f"qualification target kubernetes must be {TARGET_KUBERNETES}")
    if target.get("driver_identity") != DRIVER_IDENTITY:
        errors.append(f"qualification target driver_identity must be {DRIVER_IDENTITY}")
    if target.get("rke2") != rke2.get("version"):
        errors.append("qualification target RKE2 version does not match release lock")
    if target.get("rke2_commit") != rke2.get("commit"):
        errors.append("qualification target RKE2 commit does not match release lock")

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
    if not DIGEST_IMAGE.fullmatch(driver_image):
        errors.append("release lock images.driver must be immutable image:tag@sha256:<digest>")

    artifacts = matrix.get("release_artifacts") or {}
    if artifacts.get("driver_image_digest") != driver_image:
        errors.append("release_artifacts.driver_image_digest must equal the release lock driver image")
    for field in ("sbom", "provenance", "offline_image_manifest"):
        if not str(artifacts.get(field) or "").strip():
            errors.append(f"release_artifacts.{field} must reference generated immutable evidence")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--release-lock", type=Path, required=True)
    args = parser.parse_args()

    try:
        matrix = load_json(args.matrix)
        release_lock = load_json(args.release_lock)
        errors = validate_qualification(matrix, release_lock)
    except ValueError as exc:
        print(f"NOT_QUALIFIED: {exc}")
        return 2

    if errors:
        print("NOT_QUALIFIED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("QUALIFIED: production promotion evidence is internally consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
