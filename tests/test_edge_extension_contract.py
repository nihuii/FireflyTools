import json
from pathlib import Path
import unittest


MANIFEST_PATH = (
    Path(__file__).resolve().parents[1]
    / "browser_extensions"
    / "edge_video_capture"
    / "manifest.json"
)
PACKAGE_PATH = MANIFEST_PATH.with_name("package.json")
EXPECTED_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsbFUu9s0WkJ5Y2jA03jaUT0l"
    "IR2II3dQ6w8Y52XB16224XEmVtzC7T28M8SbptzXNPSCVgeDGBo5FTukrB172AG/5Pya"
    "iVK0BLAykUA6xtgYfC+NYBl6IVeRQtWALTpYZbhsFmPlROCG9MzgAWSyAgyEdZTOV8N1"
    "fOK/iQYCoiBr7GBFzIejsoEs3IT4KU6DvhM6yTS8mtGYxtEl/KXdtJvtBreooVT8uFj6"
    "s+xXln9imEf8N3zZ9kl2IGklmleQozYgRbOOPsVbyv9UI5yqlYu5oVueQT+6l2pS+wn2"
    "r7uSaxKavAo2Z/gJ6fhyyuYUUF0JpFRtBrQLnIeKyKN7tQIDAQAB"
)


class EdgeExtensionManifestContractTests(unittest.TestCase):
    def test_manifest_stays_within_the_v1_permission_boundary(self):
        with MANIFEST_PATH.open(encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)

        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["incognito"], "not_allowed")
        self.assertEqual(
            manifest["permissions"],
            [
                "activeTab",
                "alarms",
                "clipboardWrite",
                "nativeMessaging",
                "storage",
                "webRequest",
            ],
        )
        self.assertIn("webRequest", manifest["permissions"])
        self.assertIn("alarms", manifest["permissions"])
        self.assertIn("clipboardWrite", manifest["permissions"])
        self.assertNotIn("cookies", manifest["permissions"])
        self.assertNotIn("webRequestBlocking", manifest["permissions"])
        self.assertNotIn("content_scripts", manifest)
        self.assertNotIn("host_permissions", manifest)
        self.assertNotIn("optional_permissions", manifest)
        self.assertEqual(
            manifest["optional_host_permissions"],
            ["http://*/*", "https://*/*"],
        )
        self.assertEqual(manifest["key"], EXPECTED_KEY)

    def test_package_uses_only_node_test_without_dependencies(self):
        with PACKAGE_PATH.open(encoding="utf-8") as package_file:
            package = json.load(package_file)

        self.assertEqual(package["name"], "fireflytools-edge-video-capture")
        self.assertEqual(package["version"], "0.1.0")
        self.assertIs(package["private"], True)
        self.assertEqual(
            package["scripts"],
            {
                "test": (
                    "node --test tests/candidate_detector.test.js "
                    "tests/capture_store.test.js "
                    "tests/capture_controller.test.js "
                    "tests/popup_model.test.js"
                )
            },
        )
        for dependency_field in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
            "bundledDependencies",
            "bundleDependencies",
        ):
            with self.subTest(field=dependency_field):
                self.assertNotIn(dependency_field, package)


if __name__ == "__main__":
    unittest.main()
