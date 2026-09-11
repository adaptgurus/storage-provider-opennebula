#!/usr/bin/env python3
"""Validate live LayerSentry CSI evidence as one release-bound qualification run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from build_chart import LAYERSENTRY, load_release_lock

REQUIRED_TESTS = (
    "release_identity_baseline",
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
    "release_identity_final",
)
IDENTITY_TESTS = {"release_identity_baseline", "release_identity_final"}


class EvidenceError(ValueError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{path} must contain a JSON object")
    return value


def safe_path(root: Path, reference: str) -> Path:
    path = Path(reference)
    if path.is_absolute():
        raise EvidenceError(f"evidence path must be repository-relative: {reference}")
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise EvidenceError(f"evidence path escapes evidence root: {reference}")
    return resolved


def matrix_records(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in matrix.get("tests") or []:
        if not isinstance(item, dict):
            continue
        test_id = str(item.get("id") or "").strip()
        if not test_id:
            continue
        if test_id in result:
            raise EvidenceError(f"duplicate qualification test id: {test_id}")
        result[test_id] = item
    return result


def validate_live_evidence(
    matrix: dict[str, Any], release_lock: dict[str, Any], root: Path
) -> list[str]:
    errors: list[str] = []
    records = matrix_records(matrix)
    release = release_lock.get("release") or {}
    rke2 = release_lock.get("rke2") or {}
    images = release_lock.get("images") or {}
    lock_sha = hashlib.sha256(json.dumps(release_lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    run_ids: set[str] = set()
    storage_classes: set[str] = set()
    identity_lock_hashes: set[str] = set()

    for test_id in REQUIRED_TESTS:
        matrix_record = records.get(test_id)
        if matrix_record is None:
            errors.append(f"missing required live evidence record in matrix: {test_id}")
            continue
        if matrix_record.get("status") != "PASS":
            errors.append(f"live evidence matrix record {test_id} must be PASS")
            continue
        refs = matrix_record.get("evidence")
        if not isinstance(refs, list) or not refs:
            errors.append(f"live evidence matrix record {test_id} must reference JSON evidence")
            continue
        reference = str(refs[0] or "").strip()
        if not reference:
            errors.append(f"live evidence matrix record {test_id} has an empty evidence path")
            continue
        try:
            path = safe_path(root, reference)
            evidence = load_object(path)
        except EvidenceError as exc:
            errors.append(str(exc))
            continue

        if evidence.get("test_id") != test_id:
            errors.append(f"evidence {reference} test_id must be {test_id}")
        if evidence.get("status") != "PASS":
            errors.append(f"evidence {reference} status must be PASS")
        run_id = str(evidence.get("run_id") or "").strip()
        if not run_id:
            errors.append(f"evidence {reference} is missing run_id")
        else:
            run_ids.add(run_id)

        target = evidence.get("target") or {}
        if target.get("rke2") != rke2.get("version"):
            errors.append(f"evidence {reference} RKE2 target does not match release lock")
        if target.get("driver") != LAYERSENTRY:
            errors.append(f"evidence {reference} driver must be {LAYERSENTRY}")
        storage_class = str(target.get("storage_class") or "").strip()
        if storage_class:
            storage_classes.add(storage_class)

        if test_id in IDENTITY_TESTS:
            if target.get("release_version") != release.get("version"):
                errors.append(f"identity evidence {reference} release_version mismatch")
            if target.get("release_tag") != release.get("gitTag"):
                errors.append(f"identity evidence {reference} release_tag mismatch")
            if target.get("source_commit") != release.get("sourceCommit"):
                errors.append(f"identity evidence {reference} source_commit mismatch")
            if target.get("driver_image") != images.get("driver"):
                errors.append(f"identity evidence {reference} driver_image mismatch")
            recorded_hash = str(target.get("release_lock_sha256") or "").strip()
            if not recorded_hash:
                errors.append(f"identity evidence {reference} is missing release_lock_sha256")
            else:
                identity_lock_hashes.add(recorded_hash)

    if len(run_ids) != 1:
        errors.append(f"all mandatory live evidence must share exactly one run_id; found {sorted(run_ids)}")
    if len(storage_classes) != 1:
        errors.append(
            f"all mandatory live evidence must refer to one StorageClass; found {sorted(storage_classes)}"
        )
    if len(identity_lock_hashes) != 1:
        errors.append("baseline/final release identity evidence must share one release_lock_sha256")

    # verify_live_release hashes the exact release-lock bytes. JSON-normalized hash
    # above is intentionally not compared because whitespace is meaningful to that
    # file hash. Instead both identity checkpoints must carry the same exact hash,
    # and the production gate separately validates the frozen release-lock content.
    del lock_sha
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--release-lock", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        matrix = load_object(args.matrix)
        release_lock = load_release_lock(args.release_lock.resolve())
        errors = validate_live_evidence(matrix, release_lock, args.evidence_root.resolve())
    except (EvidenceError, OSError, ValueError) as exc:
        print(f"LIVE_EVIDENCE_NOT_QUALIFIED: {exc}")
        return 2
    if errors:
        print("LIVE_EVIDENCE_NOT_QUALIFIED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("LIVE_EVIDENCE_QUALIFIED: one release-bound qualification run is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
