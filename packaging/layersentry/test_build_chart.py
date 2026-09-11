# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import unittest
from pathlib import Path

from build_chart import (
    LAYERSENTRY,
    LEGACY,
    TARGET_DRIVER_IMAGE,
    TARGET_RKE2_COMMIT,
    TARGET_RKE2_VERSION,
    TARGET_SIDECAR_IMAGES,
    build_profile,
    load_release_lock,
    validate_identity_source,
)


class PackagingTests(unittest.TestCase):
    def identity_fixture(self):
        driver_binding = (
            '{{- $driverName := include "opennebula-csi.driverName" . -}}\n'
            '- "--drivername={{ $driverName }}"\n'
        )
        return {
            "_identity.tpl": (
                '{{- define "opennebula-csi.driverName" -}}\n'
                f'{{{{- default "{LEGACY}" .Values.driver.name -}}}}\n'
                "{{- end -}}\n"
            ),
            "csi-driver.yaml": 'name: {{ include "opennebula-csi.driverName" . }}\n',
            "csi-storageclass.yaml": 'provisioner: {{ include "opennebula-csi.driverName" $root }}\n',
            "csi-snapshotclass.yaml": 'driver: {{ include "opennebula-csi.driverName" $root }}\n',
            "csi-controller-server.yaml": driver_binding,
            "csi-node-server.yaml": (
                driver_binding
                + 'path: {{ include "opennebula-csi.kubeletPluginDir" . }}\n'
                + 'path: {{ include "opennebula-csi.kubeletRegistrationDir" . }}\n'
            ),
        }

    def release_lock(self):
        digest = "a" * 64
        images = {"driver": f"{TARGET_DRIVER_IMAGE}@sha256:{digest}"}
        images.update({name: f"{image}@sha256:{digest}" for name, image in TARGET_SIDECAR_IMAGES.items()})
        return {
            "rke2": {
                "version": TARGET_RKE2_VERSION,
                "commit": TARGET_RKE2_COMMIT,
                "kubeletRootDir": "/var/lib/kubelet",
            },
            "images": images,
            "capabilities": {"expansion": False, "snapshots": False, "clones": False},
        }

    def test_identity_source_is_consistent(self):
        validate_identity_source(self.identity_fixture())

    def test_hard_coded_layersentry_identity_rejected(self):
        data = self.identity_fixture()
        data["csi-driver.yaml"] += LAYERSENTRY
        with self.assertRaises(ValueError):
            validate_identity_source(data)

    def test_hard_coded_legacy_identity_rejected_outside_helper(self):
        data = self.identity_fixture()
        data["csi-storageclass.yaml"] += LEGACY
        with self.assertRaises(ValueError):
            validate_identity_source(data)

    def test_missing_identity_surface_rejected(self):
        data = self.identity_fixture()
        del data["csi-node-server.yaml"]
        with self.assertRaises(ValueError):
            validate_identity_source(data)

    def test_pinned_profile_uses_single_identity(self):
        profile = build_profile(self.release_lock())
        self.assertEqual(profile["driver"], {"name": LAYERSENTRY})
        self.assertEqual(profile["kubelet"]["rootDir"], "/var/lib/kubelet")
        self.assertFalse(profile["snapshotter"]["enabled"])
        self.assertEqual(profile["snapshotClasses"], [])
        self.assertEqual(profile["storageClasses"], [])
        self.assertNotIn("extraArgs", profile["driver"])

    def test_sidecars_remain_reviewed_and_digest_pinned_in_profile(self):
        lock = self.release_lock()
        profile = build_profile(lock)
        for name, config in profile["sidecars"].items():
            self.assertEqual(config["image"], lock["images"][name])
            self.assertTrue(config["image"].startswith(TARGET_SIDECAR_IMAGES[name] + "@sha256:"))

    def test_release_lock_rejects_floating_image(self):
        data = self.release_lock()
        data["images"]["attacher"] = TARGET_SIDECAR_IMAGES["attacher"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_release_lock(path)

    def test_release_lock_rejects_unreviewed_sidecar_tag(self):
        data = self.release_lock()
        data["images"]["provisioner"] = "registry.k8s.io/sig-storage/csi-provisioner:v5.3.0@sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_release_lock(path)

    def test_release_lock_rejects_wrong_driver_tag(self):
        data = self.release_lock()
        data["images"]["driver"] = "ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.0@sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_release_lock(path)

    def test_release_lock_rejects_wrong_rke2_version(self):
        data = self.release_lock()
        data["rke2"]["version"] = "v1.35.0+rke2r1"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_release_lock(path)

    def test_release_lock_rejects_unqualified_expansion_advertising(self):
        data = self.release_lock()
        data["capabilities"]["expansion"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_release_lock(path)

    def test_release_lock_rejects_unqualified_snapshot_advertising(self):
        data = self.release_lock()
        data["capabilities"]["snapshots"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_release_lock(path)

    def test_release_lock_rejects_snapshotter_when_snapshots_disabled(self):
        data = self.release_lock()
        data["images"]["snapshotter"] = "registry.k8s.io/sig-storage/csi-snapshotter:v8.6.0@sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_release_lock(path)


if __name__ == "__main__":
    unittest.main()
