# SPDX-License-Identifier: Apache-2.0
import unittest

from build_chart import TARGET_REQUIRED_SIDECAR_IMAGES
from emit_offline_manifest import release_images


class OfflineManifestTests(unittest.TestCase):
    def lock(self):
        digest = "a" * 64
        images = {
            "driver": "ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.1@sha256:" + digest
        }
        for name, tag in TARGET_REQUIRED_SIDECAR_IMAGES.items():
            images[name] = tag + "@sha256:" + digest
        return {"images": images}

    def test_manifest_contains_only_enabled_images_in_stable_order(self):
        lock = self.lock()
        images = release_images(lock)
        expected = [lock["images"]["driver"]]
        expected.extend(lock["images"][name] for name in TARGET_REQUIRED_SIDECAR_IMAGES)
        self.assertEqual(images, expected)
        self.assertFalse(any("csi-resizer" in image for image in images))
        self.assertFalse(any("csi-snapshotter" in image for image in images))

    def test_manifest_deduplicates_exact_references(self):
        lock = self.lock()
        lock["images"]["livenessProbe"] = lock["images"]["attacher"]
        images = release_images(lock)
        self.assertEqual(len(images), len(set(images)))


if __name__ == "__main__":
    unittest.main()
