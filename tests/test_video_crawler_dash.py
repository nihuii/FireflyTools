import os
import tempfile
import unittest
from unittest.mock import patch

from tools.video_crawler.adapters.dash import (
    DashAdapter,
    build_static_segment_template_plan,
    parse_mpd_capabilities,
)
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import MediaCandidate, MediaKind


DRM_MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
    </AdaptationSet>
  </Period>
</MPD>"""


STATIC_MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT12S">
  <BaseURL>https://cdn.example.test/root/</BaseURL>
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <SegmentTemplate initialization="video/$RepresentationID$/init.mp4"
                       media="video/$RepresentationID$/$Number$.m4s"
                       startNumber="1" duration="4" timescale="1"/>
      <Representation id="v-low" bandwidth="1000"/>
      <Representation id="v-high" bandwidth="3000"/>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4">
      <SegmentTemplate initialization="audio/$RepresentationID$/init.mp4"
                       media="audio/$RepresentationID$/$Number$.m4s"
                       startNumber="1" duration="4" timescale="1"/>
      <Representation id="a-main" bandwidth="128"/>
    </AdaptationSet>
  </Period>
</MPD>"""


class DashAdapterTests(unittest.TestCase):
    def test_detects_content_protection_as_drm(self):
        info = parse_mpd_capabilities(DRM_MPD)

        self.assertTrue(info.has_drm)

    def test_detects_video_and_audio_adaptation_sets(self):
        info = parse_mpd_capabilities(STATIC_MPD)

        self.assertTrue(info.has_video)
        self.assertTrue(info.has_audio)

    def test_build_static_segment_template_plan_picks_highest_bandwidth(self):
        plan = build_static_segment_template_plan(STATIC_MPD, "https://cdn.example.test/manifest.mpd")

        self.assertEqual(plan.video.representation_id, "v-high")
        self.assertEqual(plan.audio.representation_id, "a-main")
        self.assertEqual(
            plan.video.urls,
            [
                "https://cdn.example.test/root/video/v-high/init.mp4",
                "https://cdn.example.test/root/video/v-high/1.m4s",
                "https://cdn.example.test/root/video/v-high/2.m4s",
                "https://cdn.example.test/root/video/v-high/3.m4s",
            ],
        )

    def test_dynamic_mpd_is_rejected(self):
        dynamic_mpd = STATIC_MPD.replace('type="static"', 'type="dynamic"')

        with self.assertRaisesRegex(VideoDownloadError, "动态"):
            build_static_segment_template_plan(dynamic_mpd, "https://cdn.example.test/live.mpd")

    def test_adapter_rejects_drm_mpd(self):
        adapter = DashAdapter(
            output_dir="downloads",
            temp_dir="temp",
            headers_getter=lambda: {},
            log_callback=lambda message: None,
            fetch_url=lambda url: DRM_MPD.encode("utf-8"),
        )
        candidate = MediaCandidate(
            url="https://cdn.example.test/drm.mpd",
            kind=MediaKind.DASH,
            source="direct",
        )

        with self.assertRaises(VideoDownloadError) as caught:
            adapter.download(candidate, "drm")

        self.assertEqual(caught.exception.code, VideoErrorCode.UNSUPPORTED_DRM)
        self.assertFalse(caught.exception.retryable)

    def test_adapter_downloads_tracks_and_muxes_output(self):
        downloaded_urls = []

        def fake_fetch(url):
            downloaded_urls.append(url)
            if url.endswith(".mpd"):
                return STATIC_MPD.encode("utf-8")
            return f"bytes:{url}".encode("utf-8")

        with tempfile.TemporaryDirectory() as temp_root:
            output_dir = os.path.join(temp_root, "out")
            temp_dir = os.path.join(temp_root, "tmp")
            os.makedirs(output_dir)
            adapter = DashAdapter(
                output_dir=output_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
                log_callback=lambda message: None,
                fetch_url=fake_fetch,
            )
            candidate = MediaCandidate(
                url="https://cdn.example.test/manifest.mpd",
                kind=MediaKind.DASH,
                source="direct",
            )

            def fake_ffmpeg(command, **kwargs):
                self.assertEqual(command[:2], ["ffmpeg", "-y"])
                self.assertIn("-c", command)
                with open(command[-1], "wb") as output_file:
                    output_file.write(b"muxed")

            with patch(
                "tools.video_crawler.adapters.dash.subprocess.run",
                side_effect=fake_ffmpeg,
            ) as ffmpeg:
                result = adapter.download(candidate, "movie")

            self.assertTrue(os.path.exists(result))
            self.assertEqual(os.path.basename(result), "movie.mp4")
            self.assertEqual(ffmpeg.call_count, 1)
            self.assertIn("https://cdn.example.test/root/video/v-high/3.m4s", downloaded_urls)
            self.assertIn("https://cdn.example.test/root/audio/a-main/3.m4s", downloaded_urls)


if __name__ == "__main__":
    unittest.main()
