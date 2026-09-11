#!/usr/bin/env python3
"""Generate a LayerSentry CSI chart from identity-safe source.

Standard library only. The source chart is copied without string replacement.
A release lock is mandatory and all enabled images must be immutable digest
references. Generated output is still a candidate until the live qualification
matrix passes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

LEGACY = "csi.opennebula.io"
LAYERSENTRY = "csi.layersentry.io"
TARGET_RKE2_VERSION = "v1.36.4+rke2r1"
TARGET_RKE2_COMMIT = "7479a59cdd2c8ce0b8871699a24daa4b7c28cc64"
DIGEST_REF = re.compile(r"^\S+:[^/@\s]+@sha256:[0-9a-f]{64}$")
REQUIRED_SIDE_CARS = (
    "provisioner",
    "attacher",
    "resizer",
    "nodeDriverRegistrar",
    "livenessProbe",
)
IDENTITY_CONTRACT = {
    "csi-driver.yaml": ("opennebula-csi.driverName",),
    "csi-storageclass.yaml": ("opennebula-csi.driverName",),
    "csi-controller-server.yaml": ("--drivername={{ include", "opennebula-csi.driverName"),
    "csi-node-server.yaml": (
        "--drivername={{ include",
        "opennebula-csi.driverName",
        "opennebula-csi.kubeletPluginDir",
        "opennebula-csi.kubeletRegistrationDir",
    ),
}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def validate_identity_source(templates: dict[str, str]) -> None:
    """Reject source that reintroduces split CSI identity ownership."""
    helper = templates.get("_identity.tpl", "")
    if "define \"opennebula-csi.driverName\"" not in helper:
        raise ValueError("identity helper is missing from _identity.tpl")
    if LEGACY not in helper:
        raise ValueError("legacy default identity must remain explicit for backward compatibility")
    if LAYERSENTRY in helper:
        raise ValueError("LayerSentry identity belongs in the release profile, not the chart default")
    for name, required_tokens in IDENTITY_CONTRACT.items():
        text = templates.get(name)
        if text is None:
            raise ValueError(f"required identity template missing: {name}")
        for token in required_tokens:
            if token not in text:
                raise ValueError(f"identity contract missing {token!r} in {name}")
        if LEGACY in text or LAYERSENTRY in text:
            raise ValueError(f"hard-coded CSI identity found in {name}")


def validate_digest_ref(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not DIGEST_REF.fullmatch(text):
        raise ValueError(f"{field} must be image:tag@sha256:<64 lowercase hex>")
    return text


def load_release_lock(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid release lock: {exc}") from exc
    rke2 = data.get("rke2") or {}
    if rke2.get("version") != TARGET_RKE2_VERSION:
        raise ValueError(f"release lock must target {TARGET_RKE2_VERSION}")
    if rke2.get("commit") != TARGET_RKE2_COMMIT:
        raise ValueError(f"release lock must pin RKE2 commit {TARGET_RKE2_COMMIT}")
    root_dir = str(rke2.get("kubeletRootDir") or "").strip()
    if not root_dir.startswith("/") or root_dir == "/":
        raise ValueError("rke2.kubeletRootDir must be an absolute non-root path")

    images = data.get("images") or {}
    validate_digest_ref(images.get("driver"), "images.driver")
    for name in REQUIRED_SIDE_CARS:
        validate_digest_ref(images.get(name), f"images.{name}")

    capabilities = data.get("capabilities") or {}
    if capabilities.get("snapshots") is not False:
        raise ValueError("snapshots must remain false until snapshot qualification passes")
    if capabilities.get("clones") is not False:
        raise ValueError("clones must remain false until clone qualification passes")
    if images.get("snapshotter"):
        raise ValueError("snapshotter image must be omitted while snapshots are not qualified")
    return data


def split_driver_ref(image_ref: str) -> tuple[str, str]:
    validate_digest_ref(image_ref, "images.driver")
    tagged, digest = image_ref.split("@sha256:", 1)
    repository, tag = tagged.rsplit(":", 1)
    if not repository or not tag:
        raise ValueError("images.driver must include both repository and tag")
    return repository, f"{tag}@sha256:{digest}"


def build_profile(lock: dict[str, Any]) -> dict[str, Any]:
    images = lock["images"]
    repository, tag_digest = split_driver_ref(images["driver"])
    root_dir = lock["rke2"]["kubeletRootDir"]
    return {
        "fullnameOverride": "layersentry-csi",
        "nameOverride": "layersentry-csi",
        "image": {"repository": repository, "tag": tag_digest},
        "driver": {"name": LAYERSENTRY},
        "kubelet": {"rootDir": root_dir},
        "sidecars": {
            name: {"image": images[name]}
            for name in REQUIRED_SIDE_CARS
        },
        "controller": {"leaderElection": {"leaseName": "layersentry-csi-controller"}},
        "inventoryController": {"enabled": False},
        "snapshotter": {"enabled": False},
        "featureGates": {"cephfsSnapshots": False, "cephfsClones": False},
        "storageClasses": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-lock", type=Path, required=True)
    args = parser.parse_args()

    root = args.repo.resolve()
    dest = args.output.resolve()
    source = root / "helm/opennebula-csi"
    if dest == root or root in dest.parents:
        raise ValueError("output must be outside the source repository")
    if dest.exists():
        raise ValueError("output already exists; refusing to overwrite it")
    if git(root, "status", "--porcelain", "--", "helm/opennebula-csi"):
        raise ValueError("source chart has uncommitted changes; refusing to package it")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("unexpected chart symlink; review source before copying")

    templates = {
        p.name: p.read_text()
        for p in (source / "templates").iterdir()
        if p.is_file()
    }
    validate_identity_source(templates)
    lock = load_release_lock(args.release_lock.resolve())
    profile = build_profile(lock)
    source_commit = git(root, "rev-parse", "HEAD")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="layersentry-chart-", dir=dest.parent) as tmp:
        staged = Path(tmp) / "layersentry-csi"
        shutil.copytree(source, staged)
        (staged / "Chart.yaml").write_text(json.dumps({
            "apiVersion": "v2",
            "name": "layersentry-csi",
            "type": "application",
            "version": "0.1.0-dev",
            "appVersion": "0.1.0-dev",
            "description": "LayerSentry-qualified candidate using the OpenNebula CSI engine",
            "sources": [
                "https://github.com/adaptgurus/storage-provider-opennebula",
                "https://github.com/OpenNebula/storage-provider-opennebula",
            ],
        }, indent=2) + "\n")
        (staged / "layersentry-values.json").write_text(json.dumps(profile, indent=2) + "\n")
        (staged / "layersentry-release-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
        (staged / "LAYERSENTRY-CANDIDATE.txt").write_text(
            f"Source commit: {source_commit}\n"
            f"CSI identity: {LAYERSENTRY}\n"
            f"RKE2 target: {TARGET_RKE2_VERSION} ({TARGET_RKE2_COMMIT})\n"
            "NOT production-qualified until the live qualification matrix passes.\n"
            "Use -f layersentry-values.json plus an approved site values file.\n"
            "Do not rewrite existing bound PV spec.csi.driver fields. Legacy volumes keep their legacy driver.\n"
        )
        staged.rename(dest)

    print(f"Candidate chart written to {dest}; no cluster operation performed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"ERROR: {exc}")
