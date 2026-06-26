import os
import tempfile
import unittest

from tools.video_crawler.resume import SegmentManifest


class SegmentManifestTests(unittest.TestCase):
    def test_manifest_round_trips_segment_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "manifest.json")
            manifest = SegmentManifest(path)
            manifest.mark_downloaded("00001.ts", url="https://cdn/1.ts", size=12)
            manifest.save()

            loaded = SegmentManifest(path)
            loaded.load()

            self.assertTrue(loaded.is_downloaded("00001.ts", expected_size=12))
            self.assertFalse(loaded.is_downloaded("00002.ts", expected_size=12))

    def test_manifest_rejects_size_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "manifest.json")
            manifest = SegmentManifest(path)
            manifest.mark_downloaded("00001.ts", url="https://cdn/1.ts", size=12)

            self.assertFalse(manifest.is_downloaded("00001.ts", expected_size=13))


if __name__ == "__main__":
    unittest.main()
