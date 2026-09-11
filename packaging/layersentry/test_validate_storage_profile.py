# SPDX-License-Identifier: Apache-2.0
import unittest

from validate_storage_profile import validate_profile


class StorageProfileTests(unittest.TestCase):
    def release_lock(self):
        return {"capabilities": {"expansion": False, "snapshots": False, "clones": False}}

    def profile(self):
        return {
            "schema_version": 1,
            "id": "ceph-rbd-prod-a",
            "status": "QUALIFIED",
            "scope": {
                "opennebula": {
                    "datastore_id": 101,
                    "datastore_name": "prod-rbd",
                    "datastore_type": "IMAGE_DS",
                    "ds_mad": "ceph",
                    "tm_mad": "ceph",
                },
                "backend": {
                    "category": "ceph-rbd",
                    "vendor": "Ceph",
                    "model": "software-defined",
                    "firmware": "not-applicable-software-backend",
                },
                "transport": {"protocol": "rbd", "multipath": False},
                "kubernetes": {
                    "rke2": "v1.36.4+rke2r1",
                    "kubernetes": "v1.36.4",
                    "node_os": "Rocky Linux 9.x",
                    "node_kernel": "5.14.x",
                },
                "storage_class": {
                    "name": "layersentry-rbd",
                    "access_modes": ["ReadWriteOnce"],
                    "volume_mode": "Filesystem",
                    "parameters": {"datastores": "101"},
                },
            },
            "qualified_capabilities": {
                "dynamic_provisioning": True,
                "attach_detach": True,
                "worker_replacement_data_survival": True,
                "expansion": False,
                "snapshots": False,
                "clones": False,
            },
            "limitations": ["qualification applies only to this exact profile"],
            "evidence": ["evidence/t4/ceph-rbd-prod-a/qualification.json"],
        }

    def test_complete_profile_passes(self):
        self.assertEqual(validate_profile(self.profile(), "ceph-rbd-prod-a", self.release_lock()), [])

    def test_profile_id_must_match(self):
        errors = validate_profile(self.profile(), "different-profile", self.release_lock())
        self.assertTrue(any("id must match" in error for error in errors))

    def test_backend_identity_must_be_explicit(self):
        profile = self.profile()
        profile["scope"]["backend"]["firmware"] = ""
        errors = validate_profile(profile, "ceph-rbd-prod-a", self.release_lock())
        self.assertTrue(any("firmware" in error for error in errors))

    def test_optional_capabilities_must_match_release(self):
        profile = self.profile()
        profile["qualified_capabilities"]["snapshots"] = True
        errors = validate_profile(profile, "ceph-rbd-prod-a", self.release_lock())
        self.assertTrue(any("snapshots" in error and "release lock" in error for error in errors))

    def test_worker_replacement_is_mandatory(self):
        profile = self.profile()
        profile["qualified_capabilities"]["worker_replacement_data_survival"] = False
        errors = validate_profile(profile, "ceph-rbd-prod-a", self.release_lock())
        self.assertTrue(any("worker_replacement_data_survival" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
