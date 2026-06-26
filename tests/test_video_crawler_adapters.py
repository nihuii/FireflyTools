import os
import tempfile
import unittest

from tools.video_crawler.adapters.base import VideoDownloadOrchestrator
from tools.video_crawler.adapters.direct_mp4 import DirectMp4Adapter
from tools.video_crawler.adapters.hls import HlsAdapter
from tools.video_crawler.models import MediaCandidate, MediaKind
from tools.video_downloader import UniversalVideoSpider


class RecordingAdapter:
    def __init__(self, name, priority, kind):
        self.name = name
        self.priority = priority
        self.kind = kind
        self.download_calls = []

    def can_handle(self, candidate):
        return candidate.kind == self.kind

    def download(self, candidate, output_filename):
        self.download_calls.append((candidate, output_filename))
        return f"{self.name}-{output_filename}.mp4"


class FakeOrchestrator:
    def __init__(self, result_path):
        self.result_path = result_path
        self.calls = []

    def download(self, url, output_filename):
        self.calls.append((url, output_filename))
        return self.result_path


class AdapterArchitectureTests(unittest.TestCase):
    def test_orchestrator_selects_highest_priority_matching_adapter(self):
        candidate = MediaCandidate(
            url="https://cdn.example.test/video.m3u8",
            kind=MediaKind.HLS,
            source="direct",
        )
        low = RecordingAdapter("low", priority=10, kind=MediaKind.HLS)
        high = RecordingAdapter("high", priority=50, kind=MediaKind.HLS)
        orchestrator = VideoDownloadOrchestrator(
            adapters=[low, high],
            candidate_resolver=lambda url: candidate,
        )

        result = orchestrator.download(candidate.url, "movie")

        self.assertEqual(result, "high-movie.mp4")
        self.assertEqual(high.download_calls, [(candidate, "movie")])
        self.assertEqual(low.download_calls, [])

    def test_direct_mp4_adapter_uses_expected_output_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            adapter = DirectMp4Adapter(
                output_dir=temp_dir,
                headers_getter=lambda: {"User-Agent": "UA"},
                log_callback=lambda message: None,
                download_url=lambda url, save_path: calls.append((url, save_path)) or save_path,
            )
            candidate = MediaCandidate(
                url="https://cdn.example.test/video.mp4",
                kind=MediaKind.DIRECT_MP4,
                source="direct",
            )

            result = adapter.download(candidate, "movie")

            self.assertTrue(adapter.can_handle(candidate))
            self.assertEqual(result, os.path.join(temp_dir, "movie.mp4"))
            self.assertEqual(calls, [(candidate.url, result)])

    def test_hls_adapter_delegates_to_m3u8_downloader(self):
        calls = []
        adapter = HlsAdapter(
            output_dir="downloads",
            temp_dir="temp",
            headers_getter=lambda: {},
            log_callback=lambda message: None,
            download_m3u8=lambda url, output_filename: calls.append((url, output_filename)) or "movie.mp4",
        )
        candidate = MediaCandidate(
            url="https://cdn.example.test/index.m3u8",
            kind=MediaKind.HLS,
            source="direct",
        )

        result = adapter.download(candidate, "movie")

        self.assertTrue(adapter.can_handle(candidate))
        self.assertEqual(result, "movie.mp4")
        self.assertEqual(calls, [(candidate.url, "movie")])

    def test_orchestrator_can_select_dash_adapter(self):
        candidate = MediaCandidate(
            url="https://cdn.example.test/manifest.mpd",
            kind=MediaKind.DASH,
            source="direct",
        )
        dash = RecordingAdapter("dash", priority=60, kind=MediaKind.DASH)
        hls = RecordingAdapter("hls", priority=80, kind=MediaKind.HLS)
        orchestrator = VideoDownloadOrchestrator(
            adapters=[hls, dash],
            candidate_resolver=lambda url: candidate,
        )

        result = orchestrator.download(candidate.url, "movie")

        self.assertEqual(result, "dash-movie.mp4")
        self.assertEqual(dash.download_calls, [(candidate, "movie")])
        self.assertEqual(hls.download_calls, [])

    def test_universal_spider_classifies_direct_mpd_as_dash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)

            candidate = spider._resolve_candidate("https://cdn.example.test/manifest.mpd")

            self.assertEqual(candidate.kind, MediaKind.DASH)

    def test_universal_spider_run_uses_orchestrator_and_verifies_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "movie.mp4")
            with open(output_path, "wb") as output_file:
                output_file.write(b"video")
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            orchestrator = FakeOrchestrator(output_path)
            spider.orchestrator = orchestrator

            result = spider.run("https://example.test/watch", "movie")

            self.assertEqual(result, output_path)
            self.assertEqual(orchestrator.calls, [("https://example.test/watch", "movie")])


class SpiderModuleTests(unittest.TestCase):
    def test_universal_video_spider_is_importable_from_core_package(self):
        from tools.video_crawler.spider import UniversalVideoSpider as CoreSpider
        from tools.video_downloader import UniversalVideoSpider as UiCompatSpider

        self.assertIs(CoreSpider, UiCompatSpider)


if __name__ == "__main__":
    unittest.main()
