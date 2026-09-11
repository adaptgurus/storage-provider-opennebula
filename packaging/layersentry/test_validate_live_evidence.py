# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import unittest
from pathlib import Path

from validate_live_evidence import REQUIRED_TESTS, validate_live_evidence


class LiveEvidenceGateTests(unittest.TestCase):
    def lock(self):
        digest = "a" * 64
        return {
            "release": {
                "version": "0.5.15-layersentry.1",
                "gitTag": "v0.5.15-layersentry.1",
                "sourceCommit": "b" * 40,
            },
            "rke2": {
                "version": "v1.36.4+rke2r1",
                "commit": "7479a59cdd2c8ce0b8871699a24daa4b7c28cc64",
                "kubeletRootDir": "/var/lib/kubelet",
            },
            "images": {
                "driver": "ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.1@sha256:" + digest,
                "provisioner": "registry.k8s.io/sig-storage/csi-provisioner:v6.3.0@sha256:" + digest,
                "attacher": "registry.k8s.io/sig-storage/csi-attacher:v4.13.0@sha256:" + digest,
                "nodeDriverRegistrar": "registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.18.0@sha256:" + digest,
                "livenessProbe": "registry.k8s.io/sig-storage/livenessprobe:v2.20.0@sha256:" + digest,
            },
            "capabilities": {"expansion": False, "snapshots": False, "clones": False},
        }

    def materialize(self, root: Path, *, run_id="run-1", lock_sha="c" * 64):
        lock = self.lock()
        tests = []
        for test_id in REQUIRED_TESTS:
            ref = f"evidence/{test_id}.json"
            tests.append({"id": test_id, "status": "PASS", "evidence": [ref]})
            target = {
                "rke2": lock["rke2"]["version"],
                "driver": "csi.layersentry.io",
                "storage_class": "layersentry-prod",
            }
            if test_id.startswith("release_identity_"):
                target.update(
                    {
                        "release_version": lock["release"]["version"],
                        "release_tag": lock["release"]["gitTag"],
                        "source_commit": lock["release"]["sourceCommit"],
                        "driver_image": lock["images"]["driver"],
                        "release_lock_sha256": lock_sha,
                    }
                )
            path = root / ref
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "product": "LayerSentry CSI",
                        "test_id": test_id,
                        "status": "PASS",
                        "run_id": run_id,
                        "target": target,
                    }
                )
                + "\n"
            )
        return {"tests": tests}, lock

    def test_one_run_with_matching_release_identity_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix, lock = self.materialize(root)
            self.assertEqual(validate_live_evidence(matrix, lock, root, "c" * 64), [])

    def test_mixed_run_ids_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix, lock = self.materialize(root)
            path = root / "evidence/node_restart.json"
            data = json.loads(path.read_text())
            data["run_id"] = "other-run"
            path.write_text(json.dumps(data) + "\n")
            errors = validate_live_evidence(matrix, lock, root, "c" * 64)
            self.assertTrue(any("exactly one run_id" in error for error in errors))

    def test_final_identity_wrong_image_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix, lock = self.materialize(root)
            path = root / "evidence/release_identity_final.json"
            data = json.loads(path.read_text())
            data["target"]["driver_image"] = "wrong"
            path.write_text(json.dumps(data) + "\n")
            errors = validate_live_evidence(matrix, lock, root, "c" * 64)
            self.assertTrue(any("driver_image mismatch" in error for error in errors))

    def test_recorded_release_lock_hash_must_match_exact_lock_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix, lock = self.materialize(root, lock_sha="d" * 64)
            errors = validate_live_evidence(matrix, lock, root, "c" * 64)
            self.assertTrue(any("exact frozen release-lock bytes" in error for error in errors))

    def test_missing_required_evidence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix, lock = self.materialize(root)
            matrix["tests"] = [item for item in matrix["tests"] if item["id"] != "tenant_isolation"]
            errors = validate_live_evidence(matrix, lock, root, "c" * 64)
            self.assertTrue(any("tenant_isolation" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
