# SPDX-License-Identifier: Apache-2.0
import unittest

from verify_live_release import (
    CONTROLLER_IMAGES,
    NODE_IMAGES,
    VerificationError,
    verify_pod_images,
)


class LiveReleaseVerificationTests(unittest.TestCase):
    def lock(self):
        digest = "a" * 64
        return {
            "images": {
                "driver": "ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.1@sha256:" + digest,
                "provisioner": "registry.k8s.io/sig-storage/csi-provisioner:v6.3.0@sha256:" + digest,
                "attacher": "registry.k8s.io/sig-storage/csi-attacher:v4.13.0@sha256:" + digest,
                "nodeDriverRegistrar": "registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.18.0@sha256:" + digest,
                "livenessProbe": "registry.k8s.io/sig-storage/livenessprobe:v2.20.0@sha256:" + digest,
            }
        }

    def pod(self, expected, lock):
        containers = [
            {"name": name, "image": lock["images"][key]}
            for name, key in expected.items()
        ]
        statuses = [
            {
                "name": item["name"],
                "ready": True,
                "imageID": "containerd://sha256:" + "b" * 64,
            }
            for item in containers
        ]
        return {
            "metadata": {"name": "test-pod", "uid": "uid-1"},
            "spec": {"nodeName": "node-1", "containers": containers},
            "status": {"phase": "Running", "containerStatuses": statuses},
        }

    def test_controller_exact_image_set_passes(self):
        lock = self.lock()
        result = verify_pod_images(self.pod(CONTROLLER_IMAGES, lock), CONTROLLER_IMAGES, lock)
        self.assertEqual(result["images"]["opennebula-csi"], lock["images"]["driver"])

    def test_node_exact_image_set_passes(self):
        lock = self.lock()
        result = verify_pod_images(self.pod(NODE_IMAGES, lock), NODE_IMAGES, lock)
        self.assertIn("csi-node-driver-registrar", result["images"])

    def test_wrong_driver_digest_rejected(self):
        lock = self.lock()
        pod = self.pod(CONTROLLER_IMAGES, lock)
        pod["spec"]["containers"][0]["image"] = (
            "ghcr.io/adaptgurus/layersentry-csi:v0.5.15-layersentry.1@sha256:" + "c" * 64
        )
        with self.assertRaises(VerificationError):
            verify_pod_images(pod, CONTROLLER_IMAGES, lock)

    def test_unexpected_resizer_rejected(self):
        lock = self.lock()
        pod = self.pod(CONTROLLER_IMAGES, lock)
        pod["spec"]["containers"].append(
            {
                "name": "csi-resizer",
                "image": "registry.k8s.io/sig-storage/csi-resizer:v2.2.1@sha256:" + "a" * 64,
            }
        )
        pod["status"]["containerStatuses"].append(
            {"name": "csi-resizer", "ready": True, "imageID": "containerd://sha256:" + "d" * 64}
        )
        with self.assertRaises(VerificationError):
            verify_pod_images(pod, CONTROLLER_IMAGES, lock)

    def test_unexpected_init_container_rejected(self):
        lock = self.lock()
        pod = self.pod(NODE_IMAGES, lock)
        pod["spec"]["initContainers"] = [{"name": "injected", "image": "example.invalid/a:1"}]
        with self.assertRaises(VerificationError):
            verify_pod_images(pod, NODE_IMAGES, lock)

    def test_not_ready_pod_rejected(self):
        lock = self.lock()
        pod = self.pod(NODE_IMAGES, lock)
        pod["status"]["containerStatuses"][0]["ready"] = False
        with self.assertRaises(VerificationError):
            verify_pod_images(pod, NODE_IMAGES, lock)


if __name__ == "__main__":
    unittest.main()
