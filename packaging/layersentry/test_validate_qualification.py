# SPDX-License-Identifier: Apache-2.0
import copy
import tempfile
import unittest
from pathlib import Path

from validate_qualification import (
    MANDATORY_LIVE_TESTS,
    referenced_evidence_paths,
    validate_materialized_evidence,
    validate_qualification,
)


class QualificationGateTests(unittest.TestCase):
    def release_lock(self):
        digest = "a" * 64
        return {
            "rke2": {
                "version": "v1.36.4+rke2r1",
                "commit": "7479a59cdd2c8ce0b8871699a24daa4b7c28cc64",
                "kubeletRootDir": "/var/lib/kubelet",
            },
            "images": {
                "driver": f"ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.1@sha256:{digest}"
            },
            "capabilities": {"expansion": False, "snapshots": False, "clones": False},
            "qualification": {
                "storageProfile": "ceph-rbd-prod-a",
                "status": "QUALIFIED",
                "evidence": ["evidence/qualification.json"],
            },
        }

    def matrix(self):
        lock = self.release_lock()
        tests = [
            {"id": test_id, "status": "PASS", "evidence": [f"evidence/{test_id}.json"]}
            for test_id in MANDATORY_LIVE_TESTS
        ]
        tests.extend(
            [
                {"id": "volume_expansion", "status": "NOT_OFFERED_END_TO_END", "evidence": []},
                {"id": "snapshot", "status": "NOT_ADVERTISED_BY_LAYERSENTRY_CONTROLLER", "evidence": []},
                {"id": "clone", "status": "NOT_ADVERTISED_BY_LAYERSENTRY_CONTROLLER", "evidence": []},
                {"id": "snapshot_restore", "status": "NOT_OFFERED", "evidence": []},
            ]
        )
        return {
            "schema_version": 1,
            "product": "LayerSentry CSI",
            "target": {
                "kubernetes": "1.36.4",
                "rke2": lock["rke2"]["version"],
                "rke2_commit": lock["rke2"]["commit"],
                "driver_identity": "csi.layersentry.io",
            },
            "selected_storage_profile": "ceph-rbd-prod-a",
            "overall_status": "QUALIFIED",
            "tests": tests,
            "release_artifacts": {
                "driver_image_digest": lock["images"]["driver"],
                "sbom": "evidence/sbom.spdx.json",
                "provenance": "evidence/provenance.json",
                "offline_image_manifest": "evidence/offline-images.txt",
            },
        }

    def test_complete_matrix_passes(self):
        self.assertEqual(validate_qualification(self.matrix(), self.release_lock()), [])

    def test_missing_backend_profile_fails(self):
        matrix = self.matrix()
        matrix["selected_storage_profile"] = None
        errors = validate_qualification(matrix, self.release_lock())
        self.assertTrue(any("selected_storage_profile" in error for error in errors))

    def test_live_test_without_evidence_fails(self):
        matrix = self.matrix()
        next(record for record in matrix["tests"] if record["id"] == "worker_replacement")["evidence"] = []
        errors = validate_qualification(matrix, self.release_lock())
        self.assertTrue(any("worker_replacement" in error and "evidence" in error for error in errors))

    def test_advertised_snapshot_requires_snapshot_and_restore_pass(self):
        lock = self.release_lock()
        lock["capabilities"]["snapshots"] = True
        errors = validate_qualification(self.matrix(), lock)
        self.assertTrue(any("snapshot must be PASS" in error for error in errors))
        self.assertTrue(any("snapshot_restore must be PASS" in error for error in errors))

    def test_driver_artifact_must_match_release_lock(self):
        matrix = self.matrix()
        matrix["release_artifacts"]["driver_image_digest"] = (
            "ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.1@sha256:" + "b" * 64
        )
        errors = validate_qualification(matrix, self.release_lock())
        self.assertTrue(any("driver_image_digest" in error for error in errors))

    def test_release_lock_cannot_claim_qualified_without_evidence(self):
        lock = self.release_lock()
        lock["qualification"]["evidence"] = []
        errors = validate_qualification(self.matrix(), lock)
        self.assertTrue(any("qualification.evidence" in error for error in errors))

    def test_wrong_rke2_pin_fails(self):
        lock = copy.deepcopy(self.release_lock())
        lock["rke2"]["version"] = "v1.36.3+rke2r1"
        errors = validate_qualification(self.matrix(), lock)
        self.assertTrue(any("RKE2" in error for error in errors))

    def test_materialized_evidence_passes_when_all_files_exist(self):
        matrix = self.matrix()
        lock = self.release_lock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for reference in set(referenced_evidence_paths(matrix, lock)):
                path = root / reference
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("evidence\n")
            self.assertEqual(validate_materialized_evidence(matrix, lock, root), [])

    def test_materialized_evidence_rejects_missing_file(self):
        matrix = self.matrix()
        lock = self.release_lock()
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_materialized_evidence(matrix, lock, Path(tmp))
        self.assertTrue(any("does not exist" in error for error in errors))

    def test_materialized_evidence_rejects_path_escape(self):
        matrix = self.matrix()
        lock = self.release_lock()
        lock["qualification"]["evidence"] = ["../outside.json"]
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_materialized_evidence(matrix, lock, Path(tmp))
        self.assertTrue(any("escapes evidence root" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
