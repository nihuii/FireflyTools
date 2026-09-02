import os
import tempfile
import unittest
from unittest.mock import patch

from tools.video_crawler.adapters.base import VideoDownloadOrchestrator
from tools.video_crawler.adapters.direct_mp4 import DirectMp4Adapter
from tools.video_crawler.adapters.hls import HlsAdapter
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    MediaCandidate,
    MediaKind,
)
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


class RecordingHeadersAdapter(RecordingAdapter):
    def __init__(self, name, priority, kind, headers_getter):
        super().__init__(name, priority, kind)
        self.headers_getter = headers_getter
        self.received_headers = []

    def download(self, candidate, output_filename):
        self.received_headers.append(dict(self.headers_getter()))
        return super().download(candidate, output_filename)


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

    def test_direct_candidates_pass_safe_session_headers_to_all_adapters(self):
        snapshot = BrowserSessionSnapshot(
            user_agent="Edge UA",
            referer="https://example.test/watch",
            origin="https://example.test",
            cookies=(
                {"name": "sid", "value": "secret", "domain": "cdn.example.test"},
            ),
            headers={
                "Accept": "*/*",
                "Accept-Language": "zh-CN",
                "Range": "bytes=0-",
                "Authorization": "Bearer secret",
                "Cookie": "sid=secret",
            },
        )
        cases = (
            ("https://cdn.example.test/master.m3u8", MediaKind.HLS),
            ("https://cdn.example.test/video.mp4", MediaKind.DIRECT_MP4),
            ("https://cdn.example.test/manifest.mpd", MediaKind.DASH),
        )

        for url, kind in cases:
            with self.subTest(kind=kind):
                spider = UniversalVideoSpider(
                    output_dir=os.path.join("tests", ".tmp"),
                    temp_dir=os.path.join("tests", ".tmp"),
                    session_snapshot=snapshot,
                )
                adapter = RecordingHeadersAdapter(
                    "recording",
                    priority=100,
                    kind=kind,
                    headers_getter=lambda: spider.headers,
                )
                orchestrator = VideoDownloadOrchestrator(
                    adapters=[adapter],
                    candidate_resolver=spider._resolve_candidate,
                )

                orchestrator.download(url, "movie")

                self.assertEqual(len(adapter.received_headers), 1)
                headers = adapter.received_headers[0]
                self.assertEqual(headers["User-Agent"], "Edge UA")
                self.assertEqual(headers["Referer"], "https://example.test/watch")
                self.assertEqual(headers["Origin"], "https://example.test")
                self.assertEqual(headers["Accept"], "*/*")
                self.assertEqual(headers["Accept-Language"], "zh-CN")
                if kind == MediaKind.DIRECT_MP4:
                    self.assertNotIn("Range", headers)
                else:
                    self.assertEqual(headers["Range"], "bytes=0-")
                self.assertNotIn("Cookie", headers)
                self.assertNotIn("Authorization", headers)

    def test_edge_candidate_hint_kind_selects_adapter_without_sniffing(self):
        cases = (
            (MediaKind.HLS, "application/vnd.apple.mpegurl"),
            (MediaKind.DIRECT_MP4, "video/mp4"),
            (MediaKind.DASH, "application/dash+xml"),
        )

        for kind, content_type in cases:
            with self.subTest(kind=kind):
                url = "https://cdn.example.test/captured-media"
                spider = UniversalVideoSpider(
                    output_dir=os.path.join("tests", ".tmp"),
                    temp_dir=os.path.join("tests", ".tmp"),
                )
                spider.initial_candidate = MediaCandidate(
                    url=url,
                    kind=kind,
                    source="edge",
                    score=100,
                    content_type=content_type,
                )
                adapters = [
                    RecordingAdapter(item.value.lower(), 100, item)
                    for item in (
                        MediaKind.HLS,
                        MediaKind.DIRECT_MP4,
                        MediaKind.DASH,
                    )
                ]
                orchestrator = VideoDownloadOrchestrator(
                    adapters=adapters,
                    candidate_resolver=spider._resolve_candidate,
                )

                with patch.object(
                    spider,
                    "_sniff_real_url",
                    return_value="https://cdn.example.test/fallback.m3u8",
                ) as sniff:
                    result = orchestrator.download(url, "movie")

                selected = next(adapter for adapter in adapters if adapter.kind == kind)
                self.assertEqual(result, f"{kind.value.lower()}-movie.mp4")
                self.assertEqual(
                    selected.download_calls[0][0],
                    spider.initial_candidate,
                )
                sniff.assert_not_called()

    def test_edge_candidate_hint_overrides_conflicting_url_suffix(self):
        url = "https://cdn.example.test/captured.mpd"
        hint = MediaCandidate(
            url=url,
            kind=MediaKind.DIRECT_MP4,
            source="edge",
            score=100,
            content_type="video/mp4",
        )
        spider = UniversalVideoSpider(
            output_dir=os.path.join("tests", ".tmp"),
            temp_dir=os.path.join("tests", ".tmp"),
        )
        spider.initial_candidate = hint
        mp4 = RecordingAdapter("mp4", 100, MediaKind.DIRECT_MP4)
        dash = RecordingAdapter("dash", 100, MediaKind.DASH)
        orchestrator = VideoDownloadOrchestrator(
            adapters=[mp4, dash],
            candidate_resolver=spider._resolve_candidate,
        )

        result = orchestrator.download(url, "movie")

        self.assertEqual(result, "mp4-movie.mp4")
        self.assertEqual(mp4.download_calls, [(hint, "movie")])
        self.assertEqual(dash.download_calls, [])

    def test_edge_candidate_hint_rejects_mismatched_resolve_url(self):
        spider = UniversalVideoSpider(
            output_dir=os.path.join("tests", ".tmp"),
            temp_dir=os.path.join("tests", ".tmp"),
        )
        spider.initial_candidate = MediaCandidate(
            url="https://cdn.example.test/captured.mp4",
            kind=MediaKind.DIRECT_MP4,
            source="edge",
            score=100,
            content_type="video/mp4",
        )

        with self.assertRaises(VideoDownloadError) as raised:
            spider._resolve_candidate("https://cdn.example.test/tampered.mp4")

        self.assertEqual(
            raised.exception.code,
            VideoErrorCode.EDGE_CANDIDATE_INVALID,
        )
        self.assertFalse(raised.exception.retryable)

    def test_edge_direct_mp4_drops_nonzero_range_before_adapter(self):
        url = "https://cdn.example.test/video.mp4"
        snapshot = BrowserSessionSnapshot(
            user_agent="Edge UA",
            referer="https://example.test/watch",
            origin="https://example.test",
            headers={
                "Accept": "video/mp4",
                "Range": "bytes=100-199",
                "Authorization": "Bearer secret",
            },
        )
        spider = UniversalVideoSpider(
            output_dir=os.path.join("tests", ".tmp"),
            temp_dir=os.path.join("tests", ".tmp"),
            session_snapshot=snapshot,
        )
        spider.initial_candidate = MediaCandidate(
            url=url,
            kind=MediaKind.DIRECT_MP4,
            source="edge",
            score=100,
            content_type="video/mp4",
        )
        adapter = RecordingHeadersAdapter(
            "mp4",
            100,
            MediaKind.DIRECT_MP4,
            headers_getter=lambda: spider.headers,
        )
        orchestrator = VideoDownloadOrchestrator(
            adapters=[adapter],
            candidate_resolver=spider._resolve_candidate,
        )

        orchestrator.download(url, "movie")

        headers = adapter.received_headers[0]
        self.assertFalse(any(name.lower() == "range" for name in headers))
        self.assertEqual(headers["User-Agent"], "Edge UA")
        self.assertEqual(headers["Referer"], "https://example.test/watch")
        self.assertEqual(headers["Origin"], "https://example.test")
        self.assertEqual(headers["Accept"], "video/mp4")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Authorization", headers)

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
