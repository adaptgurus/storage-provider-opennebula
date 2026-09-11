#!/usr/bin/env python3
"""Verify LayerSentry OCI build attestations and extract auditable evidence.

The verifier uses only the Python standard library. It validates OCI blob hashes,
attestation-to-image linkage, in-toto predicate metadata, and requires both an
SPDX SBOM and SLSA provenance statement for the runnable image manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
ATTESTATION_TYPE = "attestation-manifest"
REFERENCE_TYPE_ANNOTATION = "vnd.docker.reference.type"
REFERENCE_DIGEST_ANNOTATION = "vnd.docker.reference.digest"
PREDICATE_ANNOTATION = "in-toto.io/predicate-type"
IN_TOTO_MEDIA_TYPE = "application/vnd.in-toto+json"
SPDX_PREDICATE = "https://spdx.dev/Document"
SLSA_PREFIX = "https://slsa.dev/provenance/"


class VerificationError(ValueError):
    pass


def _json(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{label} must contain a JSON object")
    return value


def _digest_hex(digest: str, label: str) -> str:
    match = DIGEST.fullmatch(str(digest or ""))
    if not match:
        raise VerificationError(f"{label} must be sha256:<64 lowercase hex>")
    return match.group(1)


def _read_member(archive: tarfile.TarFile, name: str) -> bytes:
    try:
        member = archive.getmember(name)
    except KeyError as exc:
        raise VerificationError(f"OCI archive is missing {name}") from exc
    if not member.isfile():
        raise VerificationError(f"OCI archive member is not a file: {name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise VerificationError(f"cannot read OCI archive member: {name}")
    return stream.read()


def _read_blob(archive: tarfile.TarFile, digest: str, label: str) -> bytes:
    hex_value = _digest_hex(digest, label)
    data = _read_member(archive, f"blobs/sha256/{hex_value}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != hex_value:
        raise VerificationError(
            f"OCI blob digest mismatch for {label}: expected sha256:{hex_value}, got sha256:{actual}"
        )
    return data


def _subject_matches(statement: dict[str, Any], reference_digest: str) -> bool:
    expected = _digest_hex(reference_digest, "attestation reference digest")
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        return False
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        digests = subject.get("digest")
        if isinstance(digests, dict) and digests.get("sha256") == expected:
            return True
    return False


def verify_archive(
    path: Path,
) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        archive = tarfile.open(path, mode="r:*")
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError(f"cannot open OCI archive {path}: {exc}") from exc

    with archive:
        index_bytes = _read_member(archive, "index.json")
        index_digest = "sha256:" + hashlib.sha256(index_bytes).hexdigest()
        index = _json(index_bytes, "index.json")
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or not manifests:
            raise VerificationError("OCI index has no manifests")

        runnable: list[dict[str, Any]] = []
        attestations: list[dict[str, Any]] = []
        for descriptor in manifests:
            if not isinstance(descriptor, dict):
                raise VerificationError("OCI index manifest descriptor must be an object")
            annotations = descriptor.get("annotations") or {}
            if isinstance(annotations, dict) and annotations.get(REFERENCE_TYPE_ANNOTATION) == ATTESTATION_TYPE:
                attestations.append(descriptor)
            else:
                platform = descriptor.get("platform") or {}
                if not (
                    isinstance(platform, dict)
                    and platform.get("os") == "unknown"
                    and platform.get("architecture") == "unknown"
                ):
                    runnable.append(descriptor)

        if len(runnable) != 1:
            raise VerificationError(
                f"LayerSentry release expects exactly one runnable linux/amd64 manifest; found {len(runnable)}"
            )
        runnable_digest = str(runnable[0].get("digest") or "")
        _read_blob(archive, runnable_digest, "runnable image manifest")

        sboms: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []

        for descriptor in attestations:
            annotations = descriptor.get("annotations") or {}
            if not isinstance(annotations, dict):
                continue
            reference_digest = str(annotations.get(REFERENCE_DIGEST_ANNOTATION) or "")
            if reference_digest != runnable_digest:
                continue

            manifest_digest = str(descriptor.get("digest") or "")
            manifest = _json(
                _read_blob(archive, manifest_digest, "attestation manifest"),
                f"attestation manifest {manifest_digest}",
            )
            layers = manifest.get("layers")
            if not isinstance(layers, list):
                raise VerificationError(f"attestation manifest {manifest_digest} has no layers")

            for layer in layers:
                if not isinstance(layer, dict) or layer.get("mediaType") != IN_TOTO_MEDIA_TYPE:
                    continue
                layer_digest = str(layer.get("digest") or "")
                statement = _json(
                    _read_blob(archive, layer_digest, "in-toto attestation"),
                    f"in-toto attestation {layer_digest}",
                )
                predicate = str(statement.get("predicateType") or "")
                layer_annotations = layer.get("annotations") or {}
                if isinstance(layer_annotations, dict):
                    annotated = str(layer_annotations.get(PREDICATE_ANNOTATION) or "")
                    if annotated and annotated != predicate:
                        raise VerificationError(
                            f"attestation predicate annotation {annotated!r} does not match statement {predicate!r}"
                        )
                if not _subject_matches(statement, reference_digest):
                    raise VerificationError(
                        f"attestation {layer_digest} does not bind to runnable manifest {reference_digest}"
                    )
                if predicate == SPDX_PREDICATE:
                    sboms.append(statement)
                elif predicate.startswith(SLSA_PREFIX):
                    provenance.append(statement)

        if not sboms:
            raise VerificationError("OCI image is missing an SPDX SBOM attestation")
        if not provenance:
            raise VerificationError("OCI image is missing a SLSA provenance attestation")

        return index_digest, runnable_digest, sboms, provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oci-archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        index_digest, runnable_digest, sboms, provenance = verify_archive(args.oci_archive)
    except VerificationError as exc:
        print(f"ATTESTATION_VERIFICATION_FAILED: {exc}")
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "sbom-attestations.json").write_text(json.dumps(sboms, indent=2) + "\n")
    (args.output_dir / "provenance-attestations.json").write_text(json.dumps(provenance, indent=2) + "\n")
    (args.output_dir / "oci-index-digest.txt").write_text(index_digest + "\n")
    (args.output_dir / "image-manifest-digest.txt").write_text(runnable_digest + "\n")
    print(
        "ATTESTATIONS_VERIFIED "
        f"index={index_digest} image={runnable_digest} sbom={len(sboms)} provenance={len(provenance)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
