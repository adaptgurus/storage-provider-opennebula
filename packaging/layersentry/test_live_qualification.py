# SPDX-License-Identifier: Apache-2.0
import unittest

from live_qualification import (
    DRIVER,
    RKE2_VERSION,
    node_ready,
    pod_ready,
    probe_pod,
)


class LiveQualificationTests(unittest.TestCase):
    def test_probe_pod_is_non_root_and_uses_fs_group(self):
        image = "registry.example/qual:1@sha256:" + "a" * 64
        pod = probe_pod("qual-ns", image)
        spec = pod["spec"]
        self.assertEqual(spec["securityContext"]["fsGroup"], 65534)
        self.assertEqual(spec["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
        container = spec["containers"][0]
        self.assertTrue(container["securityContext"]["runAsNonRoot"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertFalse(spec["automountServiceAccountToken"])

    def test_probe_pod_can_pin_replacement_node(self):
        image = "registry.example/qual:1@sha256:" + "b" * 64
        pod = probe_pod("qual-ns", image, "worker-new")
        self.assertEqual(pod["spec"]["nodeName"], "worker-new")

    def test_node_ready_requires_true_ready_condition(self):
        self.assertTrue(node_ready({"status": {"conditions": [{"type": "Ready", "status": "True"}]}}))
        self.assertFalse(node_ready({"status": {"conditions": [{"type": "Ready", "status": "False"}]}}))

    def test_pod_ready_requires_running_and_all_containers_ready(self):
        self.assertTrue(
            pod_ready(
                {
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{"ready": True}, {"ready": True}],
                    }
                }
            )
        )
        self.assertFalse(
            pod_ready(
                {
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{"ready": True}, {"ready": False}],
                    }
                }
            )
        )

    def test_release_constants_are_exact(self):
        self.assertEqual(DRIVER, "csi.layersentry.io")
        self.assertEqual(RKE2_VERSION, "v1.36.4+rke2r1")


if __name__ == "__main__":
    unittest.main()
