#!/usr/bin/env python3
"""Validate the exact backend profile attached to a LayerSentry CSI qualification."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

TARGET_RKE2 = "v1.36.4+rke2r1"
TARGET_KUBERNETES = "v1.36.4"


class ProfileError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfileError(f"{path} must contain a JSON object")
    return value


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_profile(
    profile: dict[str, Any],
    expected_id: str,
    release_lock: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if profile.get("schema_version") != 1:
        errors.append("storage profile schema_version must be 1")
    if str(profile.get("id") or "").strip() != expected_id:
        errors.append("storage profile id must match selected_storage_profile")
    if profile.get("status") != "QUALIFIED":
        errors.append("storage profile status must be QUALIFIED")

    scope = profile.get("scope") or {}
    one = scope.get("opennebula") or {}
    datastore_id = one.get("datastore_id")
    if not isinstance(datastore_id, int) or datastore_id < 0:
        errors.append("scope.opennebula.datastore_id must be a non-negative integer")
    for field in ("datastore_name", "datastore_type", "ds_mad", "tm_mad"):
        if not nonempty(one.get(field)):
            errors.append(f"scope.opennebula.{field} must be explicit")

    backend = scope.get("backend") or {}
    for field in ("category", "vendor", "model", "firmware"):
        if not nonempty(backend.get(field)):
            errors.append(f"scope.backend.{field} must be explicit (use an explicit not-applicable value if needed)")

    transport = scope.get("transport") or {}
    if not nonempty(transport.get("protocol")):
        errors.append("scope.transport.protocol must be explicit")
    if not isinstance(transport.get("multipath"), bool):
        errors.append("scope.transport.multipath must be boolean")

    k8s = scope.get("kubernetes") or {}
    if k8s.get("rke2") != TARGET_RKE2:
        errors.append(f"scope.kubernetes.rke2 must be {TARGET_RKE2}")
    if k8s.get("kubernetes") != TARGET_KUBERNETES:
        errors.append(f"scope.kubernetes.kubernetes must be {TARGET_KUBERNETES}")
    for field in ("node_os", "node_kernel"):
        if not nonempty(k8s.get(field)):
            errors.append(f"scope.kubernetes.{field} must be explicit")

    sc = scope.get("storage_class") or {}
    if not nonempty(sc.get("name")):
        errors.append("scope.storage_class.name must be explicit")
    access_modes = sc.get("access_modes")
    if not isinstance(access_modes, list) or not access_modes or not all(nonempty(v) for v in access_modes):
        errors.append("scope.storage_class.access_modes must be a non-empty string list")
    if sc.get("volume_mode") not in {"Filesystem", "Block"}:
        errors.append("scope.storage_class.volume_mode must be Filesystem or Block")
    if not isinstance(sc.get("parameters"), dict):
        errors.append("scope.storage_class.parameters must be an object")

    caps = profile.get("qualified_capabilities") or {}
    for field in (
        "dynamic_provisioning",
        "attach_detach",
        "worker_replacement_data_survival",
        "expansion",
        "snapshots",
        "clones",
    ):
        if not isinstance(caps.get(field), bool):
            errors.append(f"qualified_capabilities.{field} must be boolean")
    for required in ("dynamic_provisioning", "attach_detach", "worker_replacement_data_survival"):
        if caps.get(required) is not True:
            errors.append(f"qualified_capabilities.{required} must be true for production qualification")

    release_caps = release_lock.get("capabilities") or {}
    for profile_key, release_key in (
        ("expansion", "expansion"),
        ("snapshots", "snapshots"),
        ("clones", "clones"),
    ):
        if caps.get(profile_key) != release_caps.get(release_key):
            errors.append(
                f"qualified_capabilities.{profile_key} must match release lock capabilities.{release_key}"
            )

    evidence = profile.get("evidence")
    if not isinstance(evidence, list) or not any(str(item).strip() for item in evidence):
        errors.append("storage profile evidence must contain at least one evidence path")
    limitations = profile.get("limitations")
    if not isinstance(limitations, list):
        errors.append("storage profile limitations must be a list")
    return errors


def safe_path(root: Path, reference: str) -> Path:
    path = Path(reference)
    if path.is_absolute():
        raise ProfileError(f"profile/evidence path must be repository-relative: {reference}")
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ProfileError(f"profile/evidence path escapes evidence root: {reference}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--release-lock", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    try:
        matrix = load_json(args.matrix)
        release_lock = load_json(args.release_lock)
        selected = str(matrix.get("selected_storage_profile") or "").strip()
        profile_ref = str(matrix.get("selected_storage_profile_file") or "").strip()
        if not selected:
            raise ProfileError("selected_storage_profile is empty")
        if not profile_ref:
            raise ProfileError("selected_storage_profile_file is empty")
        profile_path = safe_path(args.evidence_root, profile_ref)
        profile = load_json(profile_path)
        errors = validate_profile(profile, selected, release_lock)
        for reference in profile.get("evidence") or []:
            ref = str(reference).strip()
            if not ref:
                continue
            evidence_path = safe_path(args.evidence_root, ref)
            if not evidence_path.is_file():
                errors.append(f"storage profile evidence does not exist: {ref}")
            elif evidence_path.stat().st_size <= 0:
                errors.append(f"storage profile evidence is empty: {ref}")
    except ProfileError as exc:
        print(f"STORAGE_PROFILE_NOT_QUALIFIED: {exc}")
        return 1

    if errors:
        print("STORAGE_PROFILE_NOT_QUALIFIED")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"STORAGE_PROFILE_QUALIFIED: {selected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
