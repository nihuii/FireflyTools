import os
import tempfile
import unittest

from tools.video_crawler.adapters.dash import DashAdapter, DashTrackPlan
from tools.video_crawler.models import MediaCandidate, MediaKind
from tools.video_crawler.resume import SegmentManifest


TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)


STATIC_MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT4S">
  <BaseURL>https://cdn.example.test/</BaseURL>
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <SegmentTemplate initialization="video/$RepresentationID$/init.mp4"
                       media="video/$RepresentationID$/$Number$.m4s"
                       startNumber="1" duration="4" timescale="1"/>
      <Representation id="v1" bandwidth="1000"/>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4">
      <SegmentTemplate initialization="audio/$RepresentationID$/init.mp4"
                       media="audio/$RepresentationID$/$Number$.m4s"
                       startNumber="1" duration="4" timescale="1"/>
      <Representation id="a1" bandwidth="128"/>
    </AdaptationSet>
  </Period>
</MPD>"""


class DashResumeTests(unittest.TestCase):
    def test_write_track_skips_completed_segments_from_manifest(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            work_dir = os.path.join(temp_dir, "dash")
            os.makedirs(work_dir)
            manifest_path = os.path.join(work_dir, "video.firefly-segments.json")
            first_path = os.path.join(work_dir, "video-00000.m4s")
            with open(first_path, "wb") as output:
                output.write(b"init")
            manifest = SegmentManifest(manifest_path)
            manifest.mark_downloaded(
                "video-00000.m4s",
                url="https://cdn/video/init.m4s",
                size=4,
            )
            manifest.save()

            fetched = []

            def fake_fetch(url):
                fetched.append(url)
                return b"new"

            adapter = DashAdapter(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
                fetch_url=fake_fetch,
            )
            track = DashTrackPlan(
                kind="video",
                representation_id="v1",
                bandwidth=1000,
                urls=[
                    "https://cdn/video/init.m4s",
                    "https://cdn/video/1.m4s",
                ],
            )

            output_path = adapter._write_track(track, work_dir)

            self.assertEqual(fetched, ["https://cdn/video/1.m4s"])
            self.assertTrue(os.path.getsize(output_path) > 0)

    def test_download_failure_preserves_work_dir_for_resume(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            output_dir = os.path.join(temp_dir, "downloads")
            os.makedirs(output_dir)
            failed_url = "https://cdn.example.test/video/v1/1.m4s"

            def fake_fetch(url):
                if url.endswith(".mpd"):
                    return STATIC_MPD.encode("utf-8")
                if url == failed_url:
                    raise RuntimeError("network down")
                return b"segment"

            adapter = DashAdapter(
                output_dir=output_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
                fetch_url=fake_fetch,
            )
            candidate = MediaCandidate(
                url="https://cdn.example.test/movie.mpd",
                kind=MediaKind.DASH,
                source="direct",
            )

            with self.assertRaisesRegex(RuntimeError, "network down"):
                adapter.download(candidate, "resume-fail")

            work_dir = os.path.join(temp_dir, "resume-fail", "dash")
            self.assertTrue(os.path.isdir(work_dir))
            self.assertTrue(
                os.path.exists(os.path.join(work_dir, "video.firefly-segments.json"))
            )
            self.assertTrue(os.path.exists(os.path.join(work_dir, "video-00000.m4s")))


if __name__ == "__main__":
    unittest.main()
