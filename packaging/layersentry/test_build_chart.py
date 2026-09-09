# SPDX-License-Identifier: Apache-2.0
import unittest
from build_chart import OLD, NEW, transform_templates, build_profile

class PackagingTests(unittest.TestCase):
    def fixture(self):
        return {"csi-driver.yaml": f"name: {OLD}\n",
                "csi-node-server.yaml": f"path: /plugins/{OLD}\npath: /plugins/{OLD}/csi.sock\n",
                "csi-storageclass.yaml": f"provisioner: {OLD}\n",
                "_helpers.tpl": "storageprovider.opennebula.io\n"}

    def test_identity_consistency(self):
        result = transform_templates(self.fixture())
        self.assertEqual(sum(v.count(NEW) for v in result.values()), 4)
        self.assertNotIn(OLD, "".join(result.values()))

    def test_preserves_provider_api_group(self):
        self.assertEqual(transform_templates(self.fixture())["_helpers.tpl"], "storageprovider.opennebula.io\n")

    def test_missing_template_rejected(self):
        data = self.fixture(); del data["csi-driver.yaml"]
        with self.assertRaises(ValueError): transform_templates(data)

    def test_source_drift_rejected(self):
        data = self.fixture(); data["csi-node-server.yaml"] += OLD
        with self.assertRaises(ValueError): transform_templates(data)

    def test_source_not_mutated(self):
        data = self.fixture(); transform_templates(data)
        self.assertIn(OLD, data["csi-driver.yaml"])

    def test_pinned_profile(self):
        p = build_profile("ghcr.io/adaptgurus/layersentry-csi", "dev@sha256:" + "a" * 64)
        self.assertEqual(p["driver"]["extraArgs"], ["--drivername=" + NEW])
        self.assertFalse(p["snapshotter"]["enabled"])
        self.assertEqual(p["storageClasses"], [])

    def test_floating_image_rejected(self):
        with self.assertRaises(ValueError): build_profile("ghcr.io/adaptgurus/layersentry-csi", "latest")

    def test_tag_in_repository_rejected(self):
        with self.assertRaises(ValueError): build_profile("registry.test/driver:old", "dev@sha256:" + "a" * 64)

    def test_registry_port_allowed(self):
        self.assertEqual(build_profile("registry.test:5000/team/driver", "dev@sha256:" + "a" * 64)["image"]["repository"], "registry.test:5000/team/driver")

    def test_invalid_repository_rejected(self):
        with self.assertRaises(ValueError): build_profile("repo bad", "dev@sha256:" + "a" * 64)

if __name__ == "__main__":
    unittest.main()
