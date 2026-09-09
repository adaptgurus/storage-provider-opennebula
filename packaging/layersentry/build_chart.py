#!/usr/bin/env python3
"""Generate an opt-in LayerSentry chart from the inspected upstream chart.

Standard library only. Does not contact a cluster, modify source, or migrate PVs.
Generated output is a candidate, not a production-qualified Helm release.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

OLD = "csi.opennebula.io"
NEW = "csi.layersentry.io"
BASE_CHART = "f4d46195a388c244e33c86a5a2c63784ee3b623d"
EXPECTED = {"csi-driver.yaml": 1, "csi-node-server.yaml": 2,
            "csi-storageclass.yaml": 1}


def transform_templates(templates: dict[str, str]) -> dict[str, str]:
    """Fail on known source drift; replace only CSI identity, not API groups."""
    for name, count in EXPECTED.items():
        if name not in templates or templates[name].count(OLD) != count:
            raise ValueError(f"Review source drift in {name}; expected {count} identity references")
    return {name: text.replace(OLD, NEW) for name, text in templates.items()}


def build_profile(repository: str, tag_digest: str) -> dict:
    if (not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*[a-z0-9]", repository)
            or ":" in repository.rsplit("/", 1)[-1] or "//" in repository):
        raise ValueError("Expected an image repository, without a tag, digest, or whitespace")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*@sha256:[0-9a-f]{64}", tag_digest):
        raise ValueError("Expected tag@sha256:<64 lowercase hexadecimal digits>")
    return {"fullnameOverride": "layersentry-csi", "nameOverride": "layersentry-csi",
            "image": {"repository": repository, "tag": tag_digest},
            "driver": {"extraArgs": [f"--drivername={NEW}"]},
            "controller": {"leaderElection": {"leaseName": "layersentry-csi-controller"}},
            "inventoryController": {"enabled": False},
            "snapshotter": {"enabled": False}, "storageClasses": []}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-repository", required=True)
    parser.add_argument("--image-tag-digest", required=True)
    args = parser.parse_args()
    root = args.repo.resolve()
    dest = args.output.resolve()
    source = root / "helm/opennebula-csi"
    if dest == root or root in dest.parents:
        raise ValueError("Output must be outside the source repository")
    if dest.exists():
        raise ValueError("Output already exists; refusing to overwrite it")
    if git(root, "rev-parse", "HEAD:helm/opennebula-csi") != BASE_CHART:
        raise ValueError("Upstream chart changed; review and update the packaging baseline first")
    if git(root, "status", "--porcelain", "--", "helm/opennebula-csi"):
        raise ValueError("Upstream chart has uncommitted changes; refusing to bypass baseline verification")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Unexpected chart symlink; review source before copying")
    templates = {p.name: p.read_text() for p in (source / "templates").iterdir() if p.is_file()}
    updated = transform_templates(templates)
    profile = build_profile(args.image_repository, args.image_tag_digest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="layersentry-chart-", dir=dest.parent) as tmp:
        staged = Path(tmp) / "layersentry-csi"
        shutil.copytree(source, staged)
        for name, text in updated.items():
            (staged / "templates" / name).write_text(text)
        (staged / "Chart.yaml").write_text(json.dumps({
            "apiVersion": "v2", "name": "layersentry-csi", "type": "application",
            "version": "0.1.0-dev", "appVersion": "0.1.0-dev",
            "description": "LayerSentry CSI candidate based on the OpenNebula CSI engine",
            "sources": ["https://github.com/adaptgurus/storage-provider-opennebula",
                        "https://github.com/OpenNebula/storage-provider-opennebula"]}, indent=2) + "\n")
        # Keep upstream values intact and require this second file during rendering.
        (staged / "layersentry-values.json").write_text(json.dumps(profile, indent=2) + "\n")
        (staged / "LAYERSENTRY-CANDIDATE.txt").write_text(
            "Use -f layersentry-values.json and a site values file. NOT production-qualified.\n"
            "Review endpoint, credentials, datastore scope, topology, sidecars and all rendered resources.\n"
            "Do not rename existing PVs or remove their old driver. Inventory API groups stay upstream.\n")
        staged.rename(dest)
    print(f"Candidate chart written to {dest}; no cluster operation performed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"ERROR: {exc}")
