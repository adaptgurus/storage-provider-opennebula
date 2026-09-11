# SPDX-License-Identifier: Apache-2.0
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from verify_oci_attestations import VerificationError, verify_archive


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


class OCIAttestationTests(unittest.TestCase):
    def build_archive(self, include_provenance=True, tamper_sbom=False):
        runnable_bytes = encoded({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json"})
        runnable_digest = digest(runnable_bytes)
        runnable_hex = runnable_digest.split(":", 1)[1]

        statements = [
            {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [{"name": "_", "digest": {"sha256": runnable_hex}}],
                "predicateType": "https://spdx.dev/Document",
                "predicate": {"spdxVersion": "SPDX-2.3"},
            }
        ]
        if include_provenance:
            statements.append(
                {
                    "_type": "https://in-toto.io/Statement/v1",
                    "subject": [{"name": "_", "digest": {"sha256": runnable_hex}}],
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "predicate": {"buildDefinition": {}},
                }
            )

        blobs = {runnable_digest: runnable_bytes}
        layers = []
        for statement in statements:
            data = encoded(statement)
            layer_digest = digest(data)
            stored = data + b"tampered" if tamper_sbom and statement["predicateType"] == "https://spdx.dev/Document" else data
            blobs[layer_digest] = stored
            layers.append(
                {
                    "mediaType": "application/vnd.in-toto+json",
                    "digest": layer_digest,
                    "size": len(data),
                    "annotations": {"in-toto.io/predicate-type": statement["predicateType"]},
                }
            )

        attestation_manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "artifactType": "application/vnd.docker.attestation.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.empty.v1+json",
                "digest": "sha256:" + hashlib.sha256(b"{}").hexdigest(),
                "size": 2,
                "data": "e30=",
            },
            "layers": layers,
            "subject": {"digest": runnable_digest},
        }
        attestation_bytes = encoded(attestation_manifest)
        attestation_digest = digest(attestation_bytes)
        blobs[attestation_digest] = attestation_bytes

        index = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": runnable_digest,
                    "size": len(runnable_bytes),
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": attestation_digest,
                    "size": len(attestation_bytes),
                    "annotations": {
                        "vnd.docker.reference.type": "attestation-manifest",
                        "vnd.docker.reference.digest": runnable_digest,
                    },
                    "platform": {"os": "unknown", "architecture": "unknown"},
                },
            ],
        }
        index_bytes = encoded(index)
        index_digest = digest(index_bytes)

        tmp = tempfile.TemporaryDirectory()
        archive_path = Path(tmp.name) / "image.oci.tar"
        with tarfile.open(archive_path, "w") as archive:
            files = {"index.json": index_bytes}
            for blob_digest, blob_data in blobs.items():
                files[f"blobs/sha256/{blob_digest.split(':', 1)[1]}"] = blob_data
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return tmp, archive_path, index_digest, runnable_digest

    def test_valid_sbom_and_provenance_pass(self):
        tmp, archive, expected_index, expected_runnable = self.build_archive()
        self.addCleanup(tmp.cleanup)
        index_digest, runnable, sboms, provenance = verify_archive(archive)
        self.assertEqual(index_digest, expected_index)
        self.assertEqual(runnable, expected_runnable)
        self.assertEqual(len(sboms), 1)
        self.assertEqual(len(provenance), 1)

    def test_missing_provenance_fails(self):
        tmp, archive, _, _ = self.build_archive(include_provenance=False)
        self.addCleanup(tmp.cleanup)
        with self.assertRaisesRegex(VerificationError, "SLSA provenance"):
            verify_archive(archive)

    def test_tampered_attestation_blob_fails(self):
        tmp, archive, _, _ = self.build_archive(tamper_sbom=True)
        self.addCleanup(tmp.cleanup)
        with self.assertRaisesRegex(VerificationError, "digest mismatch"):
            verify_archive(archive)


if __name__ == "__main__":
    unittest.main()
