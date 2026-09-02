import asyncio
from datetime import datetime, timezone
import json
import os
import requests
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from urllib.error import URLError
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QScrollArea
from PyQt6.QtCore import Qt

from tests.edge_companion_fixtures import valid_edge_message
from tools.edge_companion.protocol import parse_candidate_json, serialize_candidate
from tools.edge_companion.ui import EdgeCandidateDialog
from tools.video_downloader import (
    UniversalVideoSpider,
    VideoDownloadError,
    VideoDownloaderTool,
)
from tools.video_crawler.errors import VideoErrorCode
from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    DiagnosticReport,
    MediaCandidate,
    MediaKind,
)

TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)


class RecordingEdgeDialog:
    """Record confirmation input while returning a configured dialog result."""

    def __init__(self, accepted):
        self.accepted = accepted
        self.candidate = None
        self.shown = False

    def bind(self, candidate):
        """Remember the candidate supplied by the production dialog factory."""
        self.candidate = candidate
        return self

    def exec(self):
        """Record display and return the configured Qt dialog result."""
        self.shown = True
        if self.accepted:
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected


def fixed_edge_now():
    """Return a stable aware UTC time for Edge candidate tests."""
    return datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc)


def complete_task_fixture():
    """Return a complete queue task using the stable Edge fixture URL."""
    return {
        "url": valid_edge_message()["candidate"]["url"],
        "name": "edge-video",
        "save_dir": "downloads",
        "is_high_speed": False,
        "segment_concurrency": 5,
        "resume_enabled": True,
        "use_ytdlp_fallback": False,
        "live_record_seconds": 300,
        "sniffer_headless": True,
        "sniffer_use_persistent_profile": False,
        "sniffer_use_system_chrome": False,
        "sniffer_manual_wait_seconds": 25,
    }


class TrackingSpider(UniversalVideoSpider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_downloads = 0
        self.max_active_downloads = 0

    async def _download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
        self.active_downloads += 1
        self.max_active_downloads = max(
            self.max_active_downloads, self.active_downloads
        )
        await asyncio.sleep(0.01)
        self.active_downloads -= 1
        return True


class FailingSegmentSpider(UniversalVideoSpider):
    async def _download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
        return ts_url != "bad"


class RatioFailingSegmentSpider(UniversalVideoSpider):
    async def _download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
        return not ts_url.startswith("bad")


class RecordingSegmentSpider(UniversalVideoSpider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.download_calls = []

    async def _download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
        self.download_calls.append(
            {
                "url": ts_url,
                "save_path": save_path,
                "cipher": cipher,
                "extra_headers": extra_headers or {},
            }
        )
        return True


class ResumeRecordingSpider(UniversalVideoSpider):
    def __init__(self, *args, fail_urls=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_urls = set(fail_urls or [])
        self.download_calls = []
        self.download_headers = []

    async def _download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
        self.download_calls.append(ts_url)
        self.download_headers.append(extra_headers or {})
        if ts_url in self.fail_urls:
            return False
        with open(save_path, "wb") as segment_file:
            segment_file.write(b"segment")
        return True

    def _merge_with_ffmpeg(self, ts_files, output_mp4, init_file=None):
        with open(output_mp4, "wb") as output_file:
            output_file.write(b"video")
        return output_mp4


def segment_items(failed_count, total_count):
    return [
        (
            f"bad-{index}" if index < failed_count else f"good-{index}",
            f"{index}.ts",
        )
        for index in range(total_count)
    ]


class RecordingSpider:
    init_kwargs = None
    run_args = None

    def __init__(self, **kwargs):
        type(self).init_kwargs = kwargs

    def run(self, url, name):
        type(self).run_args = (url, name)
        return os.path.join("downloads", f"{name}.mp4")


class StructuredFailureSpider:
    def __init__(self, **kwargs):
        pass

    def run(self, url, name):
        raise VideoDownloadError(
            VideoErrorCode.HTTP_FORBIDDEN,
            "服务器拒绝访问",
            retryable=False,
        )


class GenericFailureSpider:
    def __init__(self, **kwargs):
        pass

    def run(self, url, name):
        raise RuntimeError("unexpected boom")


class NoMediaFailureSpider:
    def __init__(self, **kwargs):
        pass

    def run(self, url, name):
        raise VideoDownloadError(
            VideoErrorCode.NO_MEDIA_FOUND,
            "未能找到视频流",
            retryable=False,
        )


class UnsupportedDashFailureSpider:
    def __init__(self, **kwargs):
        pass

    def run(self, url, name):
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "暂不支持该 DASH 清单",
            retryable=False,
        )


def fake_playlist(segment_count, byteranges=None):
    byteranges = byteranges or [None] * segment_count
    return SimpleNamespace(
        is_variant=False,
        segment_map=[],
        segments=[
            SimpleNamespace(
                absolute_uri=f"https://cdn.example.test/{index:05d}.ts",
                key=None,
                byterange=byteranges[index],
                init_section=None,
                discontinuity=False,
            )
            for index in range(segment_count)
        ],
        media_sequence=0,
        keys=[],
    )


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeMetadataResponse:
    def __init__(self, headers):
        self.headers = headers

    def raise_for_status(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class UniversalVideoSpiderTests(unittest.TestCase):
    def test_segment_downloads_obey_selected_concurrency(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = TrackingSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                segment_concurrency=2,
            )
            items = [
                (str(index), os.path.join(temp_dir, str(index)))
                for index in range(5)
            ]

            asyncio.run(spider._download_segments(None, items, None))

            self.assertEqual(spider.max_active_downloads, 2)

    def test_failed_segment_makes_download_fail(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = FailingSegmentSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                segment_concurrency=3,
            )

            with self.assertRaisesRegex(VideoDownloadError, "1 个切片"):
                asyncio.run(
                    spider._download_segments(
                        None,
                        [("good", "good.ts"), ("bad", "bad.ts")],
                        None,
                    )
                )

    def test_three_percent_failed_segments_are_tolerated(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = RatioFailingSegmentSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
            )

            asyncio.run(
                spider._download_segments(None, segment_items(3, 100), None)
            )

    def test_download_segments_passes_per_item_cipher_and_range_header(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = RecordingSegmentSpider(output_dir=temp_dir, temp_dir=temp_dir)
            first_cipher = object()
            second_cipher = object()
            items = [
                {
                    "url": "https://cdn.example.test/segment.ts",
                    "save_path": os.path.join(temp_dir, "00000.ts"),
                    "cipher": first_cipher,
                    "range_header": "bytes=0-99",
                },
                {
                    "url": "https://cdn.example.test/segment.ts",
                    "save_path": os.path.join(temp_dir, "00001.ts"),
                    "cipher": second_cipher,
                    "range_header": "bytes=100-199",
                },
            ]

            asyncio.run(spider._download_segments(None, items, None))

            self.assertIs(spider.download_calls[0]["cipher"], first_cipher)
            self.assertEqual(
                spider.download_calls[0]["extra_headers"],
                {"Range": "bytes=0-99"},
            )
            self.assertIs(spider.download_calls[1]["cipher"], second_cipher)
            self.assertEqual(
                spider.download_calls[1]["extra_headers"],
                {"Range": "bytes=100-199"},
            )

    def test_more_than_three_percent_failed_segments_fail(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = RatioFailingSegmentSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
            )

            with self.assertRaisesRegex(VideoDownloadError, "超过允许的 3%"):
                asyncio.run(
                    spider._download_segments(None, segment_items(4, 100), None)
                )

    def test_merge_accepts_three_percent_missing_segments(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            segment_paths = []
            for index in range(100):
                path = os.path.join(temp_dir, f"{index:05d}.ts")
                segment_paths.append(path)
                if index >= 3:
                    with open(path, "wb") as segment_file:
                        segment_file.write(b"segment")
            output_path = os.path.join(temp_dir, "output.mp4")

            def fake_ffmpeg(command, **kwargs):
                with open(output_path, "wb") as output_file:
                    output_file.write(b"video")

            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            with patch(
                "tools.video_crawler.adapters.hls.subprocess.run",
                side_effect=fake_ffmpeg,
            ):
                result = spider._merge_with_ffmpeg(segment_paths, output_path)

            self.assertEqual(result, output_path)

    def test_missing_sniffed_stream_is_failure(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            with patch.object(spider, "_sniff_real_url", return_value=None):
                with self.assertRaisesRegex(VideoDownloadError, "未能找到视频流") as raised:
                    spider.run("https://example.invalid/watch", "video")
        self.assertEqual(raised.exception.code, VideoErrorCode.NO_MEDIA_FOUND)

    def test_webpage_forbidden_report_uses_http_forbidden_code(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            with patch.object(
                spider,
                "_sniff_real_url",
                side_effect=VideoDownloadError(
                    VideoErrorCode.HTTP_FORBIDDEN,
                    "页面访问受限",
                    retryable=False,
                ),
            ):
                with self.assertRaises(VideoDownloadError) as raised:
                    spider.run("https://example.test/watch", "video")

        self.assertEqual(raised.exception.code, VideoErrorCode.HTTP_FORBIDDEN)

    def test_sniff_real_url_uses_page_sniffer_report_mp4_candidate(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            report = DiagnosticReport(
                source_url="https://example.invalid/watch",
                candidates=[
                    MediaCandidate(
                        url="https://cdn.example.invalid/video.mp4",
                        kind=MediaKind.DIRECT_MP4,
                        source="network",
                        score=75,
                    )
                ],
            )

            with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
                sniffer_class.return_value.sniff.return_value = report
                with patch.object(
                    spider,
                    "_probe_mp4_size",
                    return_value=50_000,
                ):
                    result = spider._sniff_real_url("https://example.invalid/watch")

            self.assertEqual(result, "https://cdn.example.invalid/video.mp4")

    def test_incomplete_navigation_without_candidate_is_retryable_timeout(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
        report = DiagnosticReport(
            source_url="https://site.example/watch",
            navigation_incomplete=True,
            warnings=["页面加载异常或超时: domcontentloaded timeout"],
        )

        with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
            sniffer_class.return_value.sniff.return_value = report
            with self.assertRaises(VideoDownloadError) as raised:
                spider._sniff_real_url("https://site.example/watch")

        self.assertEqual(raised.exception.code, VideoErrorCode.NETWORK_TIMEOUT)
        self.assertTrue(raised.exception.retryable)
        self.assertIn("导航未完整结束", str(raised.exception))

    def test_incomplete_navigation_does_not_select_response_body_only_mp4(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
        report = DiagnosticReport(
            source_url="https://site.example/watch",
            candidates=[
                MediaCandidate(
                    url="https://cdn.example.test/preview.mp4",
                    kind=MediaKind.DIRECT_MP4,
                    source="response-body",
                    score=70,
                )
            ],
            navigation_incomplete=True,
            warnings=["页面加载异常或超时"],
        )

        with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
            sniffer_class.return_value.sniff.return_value = report
            with patch.object(spider, "_probe_mp4_size") as probe:
                with self.assertRaises(VideoDownloadError) as raised:
                    spider._sniff_real_url("https://site.example/watch")

        self.assertEqual(raised.exception.code, VideoErrorCode.NETWORK_TIMEOUT)
        probe.assert_not_called()

    def test_incomplete_navigation_can_use_verified_hls(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
        hls_url = "https://cdn.example.test/main.m3u8"
        report = DiagnosticReport(
            source_url="https://site.example/watch",
            candidates=[
                MediaCandidate(
                    url=hls_url,
                    kind=MediaKind.HLS,
                    source="network",
                    score=80,
                )
            ],
            navigation_incomplete=True,
            warnings=["页面加载异常或超时"],
        )

        with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
            sniffer_class.return_value.sniff.return_value = report
            with patch.object(
                spider,
                "_select_best_m3u8",
                return_value=(hls_url, 553),
            ):
                selected = spider._sniff_real_url("https://site.example/watch")

        self.assertEqual(selected, hls_url)

    def test_select_best_mp4_prefers_larger_validated_network_candidate(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
        candidates = [
            MediaCandidate(
                url="https://cdn.example.test/short.mp4",
                kind=MediaKind.DIRECT_MP4,
                source="network",
                score=75,
            ),
            MediaCandidate(
                url="https://cdn.example.test/main.mp4",
                kind=MediaKind.DIRECT_MP4,
                source="network",
                score=75,
            ),
        ]

        with patch.object(
            spider,
            "_probe_mp4_size",
            side_effect=[1_000, 50_000],
            create=True,
        ):
            selected = spider._select_best_mp4(candidates)

        self.assertEqual(selected.url, "https://cdn.example.test/main.mp4")

    def test_select_best_mp4_rejects_unverified_response_body_only_candidate(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
        candidate = MediaCandidate(
            url="https://cdn.example.test/short.mp4",
            kind=MediaKind.DIRECT_MP4,
            source="response-body",
            score=70,
        )

        with patch.object(
            spider,
            "_probe_mp4_size",
            return_value=None,
            create=True,
        ):
            selected = spider._select_best_mp4([candidate])

        self.assertIsNone(selected)

    def test_probe_mp4_size_uses_head_content_length_without_get(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")

        with patch(
            "tools.video_crawler.spider.requests.head",
            return_value=FakeMetadataResponse({"Content-Length": "50000"}),
        ):
            with patch("tools.video_crawler.spider.requests.get") as get:
                size = spider._probe_mp4_size(
                    "https://cdn.example.test/main.mp4"
                )

        self.assertEqual(size, 50_000)
        get.assert_not_called()

    def test_probe_mp4_size_falls_back_to_range_get(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")

        with patch(
            "tools.video_crawler.spider.requests.head",
            return_value=FakeMetadataResponse({}),
        ):
            with patch(
                "tools.video_crawler.spider.requests.get",
                return_value=FakeMetadataResponse(
                    {"Content-Range": "bytes 0-0/50000"}
                ),
            ) as get:
                size = spider._probe_mp4_size(
                    "https://cdn.example.test/main.mp4"
                )

        self.assertEqual(size, 50_000)
        self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=0-0")
        self.assertTrue(get.call_args.kwargs["stream"])

    def test_probe_mp4_size_inherits_browser_session_headers(self):
        spider = UniversalVideoSpider(
            output_dir="downloads",
            temp_dir="temp",
            session_snapshot=BrowserSessionSnapshot(
                user_agent="Browser UA",
                referer="https://site.example/watch",
                origin="https://site.example",
                cookies=(
                    {
                        "name": "sid",
                        "value": "abc",
                        "domain": "cdn.example.test",
                    },
                ),
                headers={
                    "Authorization": "Bearer secret",
                    "Accept-Language": "zh-CN",
                },
            ),
        )

        with patch(
            "tools.video_crawler.spider.requests.head",
            return_value=FakeMetadataResponse({"Content-Length": "50000"}),
        ) as head:
            size = spider._probe_mp4_size("https://cdn.example.test/main.mp4")

        self.assertEqual(size, 50_000)
        request_headers = head.call_args.kwargs["headers"]
        self.assertEqual(request_headers["User-Agent"], "Browser UA")
        self.assertEqual(request_headers["Referer"], "https://site.example/watch")
        self.assertEqual(request_headers["Origin"], "https://site.example")
        self.assertEqual(request_headers["Accept-Language"], "zh-CN")
        self.assertNotIn("Authorization", request_headers)
        self.assertNotIn("Cookie", request_headers)

    def test_sniff_selection_is_independent_of_dash_and_mp4_discovery_order(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
        report = DiagnosticReport(
            source_url="https://site.example/watch",
            candidates=[
                MediaCandidate(
                    url="https://cdn.example.test/stream.mpd",
                    kind=MediaKind.DASH,
                    source="network",
                    score=65,
                ),
                MediaCandidate(
                    url="https://cdn.example.test/short.mp4",
                    kind=MediaKind.DIRECT_MP4,
                    source="response-body",
                    score=70,
                ),
                MediaCandidate(
                    url="https://cdn.example.test/main.mp4",
                    kind=MediaKind.DIRECT_MP4,
                    source="network",
                    score=75,
                ),
            ],
        )
        sizes = {
            "https://cdn.example.test/short.mp4": 1_000,
            "https://cdn.example.test/main.mp4": 50_000,
        }

        with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
            sniffer_class.return_value.sniff.return_value = report
            with patch.object(
                spider,
                "_probe_mp4_size",
                side_effect=lambda url: sizes.get(url),
            ):
                selected = spider._sniff_real_url("https://site.example/watch")

        self.assertEqual(selected, "https://cdn.example.test/main.mp4")

    def test_webpage_run_inherits_sniffed_session_headers(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            report = DiagnosticReport(
                source_url="https://example.test/watch",
                candidates=[
                    MediaCandidate(
                        url="https://cdn.example.test/video.mp4",
                        kind=MediaKind.DIRECT_MP4,
                        source="network",
                        score=75,
                    )
                ],
                session=BrowserSessionSnapshot(
                    user_agent="Browser UA",
                    referer="https://example.test/watch",
                    origin="https://example.test",
                    cookies=(
                        {"name": "sid", "value": "abc", "domain": "example.test"},
                    ),
                    headers={
                        "Authorization": "Bearer secret",
                        "Accept-Language": "zh-CN",
                    },
                ),
            )
            captured_headers = {}

            def fake_download_mp4(video_url, save_path):
                captured_headers.update(spider.headers)
                output_path = os.path.join(temp_dir, "video.mp4")
                with open(output_path, "wb") as output_file:
                    output_file.write(b"video")
                return output_path

            with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
                sniffer_class.return_value.sniff.return_value = report
                with patch.object(spider, "_probe_mp4_size", return_value=50_000):
                    with patch.object(
                        spider,
                        "_download_mp4",
                        side_effect=fake_download_mp4,
                    ):
                        spider.run("https://example.test/watch", "video")

            self.assertEqual(captured_headers["User-Agent"], "Browser UA")
            self.assertEqual(captured_headers["Referer"], "https://example.test/watch")
            self.assertEqual(captured_headers["Origin"], "https://example.test")
            self.assertEqual(captured_headers["Accept-Language"], "zh-CN")
            self.assertNotIn("Authorization", captured_headers)
            self.assertNotIn("Cookie", captured_headers)

    def test_sniff_real_url_rejects_unverified_hls_candidate(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
        report = DiagnosticReport(
            source_url="https://example.test/watch",
            candidates=[
                MediaCandidate(
                    url="https://cdn.example.test/index.m3u8",
                    kind=MediaKind.HLS,
                    source="response-body",
                    score=75,
                )
            ],
        )

        with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
            sniffer_class.return_value.sniff.return_value = report
            with patch(
                "tools.video_crawler.spider.requests.get",
                side_effect=requests.exceptions.Timeout("timed out"),
            ):
                with self.assertRaises(VideoDownloadError) as raised:
                    spider._sniff_real_url("https://example.test/watch")

        self.assertEqual(raised.exception.code, VideoErrorCode.NETWORK_TIMEOUT)
        self.assertTrue(raised.exception.retryable)
        self.assertIn("候选 M3U8", str(raised.exception))

    def test_select_best_m3u8_prefers_higher_bandwidth_when_segments_tie(self):
        messages = []
        spider = UniversalVideoSpider(
            output_dir="downloads",
            temp_dir="temp",
            log_callback=messages.append,
        )
        low_master = "https://cdn.example.test/low/index.m3u8"
        high_master = "https://cdn.example.test/high/index.m3u8"
        responses = {
            low_master: FakeResponse(
                "#EXTM3U\n"
                "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720\n"
                "https://cdn.example.test/low/720.m3u8\n"
            ),
            "https://cdn.example.test/low/720.m3u8": FakeResponse(
                "#EXTM3U\n#EXT-X-TARGETDURATION:8\n"
                "#EXTINF:8,\n000.ts\n#EXTINF:8,\n001.ts\n"
                "#EXTINF:8,\n002.ts\n#EXT-X-ENDLIST\n"
            ),
            high_master: FakeResponse(
                "#EXTM3U\n"
                "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080\n"
                "https://cdn.example.test/high/1080.m3u8\n"
            ),
            "https://cdn.example.test/high/1080.m3u8": FakeResponse(
                "#EXTM3U\n#EXT-X-TARGETDURATION:8\n"
                "#EXTINF:8,\n000.ts\n#EXTINF:8,\n001.ts\n"
                "#EXTINF:8,\n002.ts\n#EXT-X-ENDLIST\n"
            ),
        }

        with patch(
            "tools.video_crawler.spider.requests.get",
            side_effect=lambda url, **kwargs: responses[url],
        ):
            best_url, segment_count = spider._select_best_m3u8(
                [low_master, high_master]
            )

        self.assertEqual(best_url, high_master)
        self.assertEqual(segment_count, 3)
        log_text = "\n".join(messages)
        self.assertIn("最高码率 3000k", log_text)
        self.assertIn("分辨率 1920x1080", log_text)

    def test_select_best_m3u8_allows_slow_playlist_probe(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
        playlist_url = "https://cdn.example.test/main.m3u8"
        response = FakeResponse(
            "#EXTM3U\n#EXT-X-TARGETDURATION:8\n"
            "#EXTINF:8,\n000.ts\n#EXT-X-ENDLIST\n"
        )

        with patch(
            "tools.video_crawler.spider.requests.get",
            return_value=response,
        ) as get:
            best_url, segment_count = spider._select_best_m3u8([playlist_url])

        self.assertEqual(best_url, playlist_url)
        self.assertEqual(segment_count, 1)
        self.assertEqual(get.call_args.kwargs["timeout"], 15)

    def test_select_best_m3u8_inherits_browser_session_headers(self):
        spider = UniversalVideoSpider(
            output_dir="downloads",
            temp_dir="temp",
            session_snapshot=BrowserSessionSnapshot(
                user_agent="Browser UA",
                referer="https://site.example/watch",
                origin="https://site.example",
                cookies=(
                    {
                        "name": "sid",
                        "value": "abc",
                        "domain": "cdn.example.test",
                    },
                ),
                headers={
                    "Authorization": "Bearer secret",
                    "Accept-Language": "zh-CN",
                },
            ),
        )
        playlist_url = "https://cdn.example.test/main.m3u8"
        response = FakeResponse(
            "#EXTM3U\n#EXT-X-TARGETDURATION:8\n"
            "#EXTINF:8,\n000.ts\n#EXT-X-ENDLIST\n"
        )

        with patch(
            "tools.video_crawler.spider.requests.get",
            return_value=response,
        ) as get:
            spider._select_best_m3u8([playlist_url])

        request_headers = get.call_args.kwargs["headers"]
        self.assertEqual(request_headers["User-Agent"], "Browser UA")
        self.assertEqual(request_headers["Referer"], "https://site.example/watch")
        self.assertEqual(request_headers["Origin"], "https://site.example")
        self.assertEqual(request_headers["Accept-Language"], "zh-CN")
        self.assertNotIn("Authorization", request_headers)
        self.assertNotIn("Cookie", request_headers)

    def test_m3u8_load_timeout_uses_network_timeout_code(self):
        spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")

        with patch(
            "tools.video_crawler.adapters.hls.m3u8.load",
            side_effect=URLError(TimeoutError("timed out")),
        ):
            with self.assertRaises(VideoDownloadError) as raised:
                asyncio.run(
                    spider._download_m3u8(
                        "https://cdn.example.test/index.m3u8",
                        "video",
                    )
                )

        self.assertEqual(raised.exception.code, VideoErrorCode.NETWORK_TIMEOUT)
        self.assertTrue(raised.exception.retryable)

    def test_empty_output_is_failure(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            output_path = os.path.join(temp_dir, "empty.mp4")
            open(output_path, "wb").close()
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)

            with self.assertRaisesRegex(VideoDownloadError, "输出文件"):
                spider._verify_output(output_path)

    def test_cleanup_error_does_not_hide_ffmpeg_failure(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            segment_path = os.path.join(temp_dir, "00000.ts")
            with open(segment_path, "wb") as segment_file:
                segment_file.write(b"segment")
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            ffmpeg_error = subprocess.CalledProcessError(
                1,
                ["ffmpeg"],
                stderr=b"merge failed",
            )

            with patch(
                "tools.video_crawler.adapters.hls.subprocess.run",
                side_effect=ffmpeg_error,
            ), patch(
                "tools.video_crawler.adapters.hls.os.remove",
                side_effect=PermissionError("cleanup denied"),
            ):
                with self.assertRaisesRegex(
                    VideoDownloadError,
                    "FFmpeg 合并失败",
                ) as raised:
                    spider._merge_with_ffmpeg(
                        [segment_path],
                        os.path.join(temp_dir, "output.mp4"),
                    )
                self.assertEqual(raised.exception.code, VideoErrorCode.FFMPEG_FAILED)

    def test_m3u8_resume_skips_manifest_completed_segment(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = ResumeRecordingSpider(output_dir=temp_dir, temp_dir=temp_dir)
            video_temp_dir = os.path.join(temp_dir, "resume")
            os.makedirs(video_temp_dir)
            completed_path = os.path.join(video_temp_dir, "00000.ts")
            with open(completed_path, "wb") as segment_file:
                segment_file.write(b"cached")

            from tools.video_crawler.resume import SegmentManifest

            manifest = SegmentManifest(
                os.path.join(video_temp_dir, ".firefly-segments.json")
            )
            manifest.mark_downloaded(
                "00000.ts",
                url="https://cdn.example.test/00000.ts",
                size=os.path.getsize(completed_path),
            )
            manifest.save()

            with patch(
                "tools.video_crawler.adapters.hls.m3u8.load",
                return_value=fake_playlist(2),
            ):
                output_path = asyncio.run(
                    spider._download_m3u8("https://cdn.example.test/index.m3u8", "resume")
                )

            self.assertEqual(
                spider.download_calls,
                ["https://cdn.example.test/00001.ts"],
            )
            self.assertTrue(os.path.exists(output_path))
            self.assertFalse(os.path.exists(video_temp_dir))

    def test_m3u8_resume_failure_rate_uses_full_playlist_count(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            failed_urls = {
                "https://cdn.example.test/00096.ts",
                "https://cdn.example.test/00097.ts",
            }
            spider = ResumeRecordingSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                fail_urls=failed_urls,
            )
            video_temp_dir = os.path.join(temp_dir, "resume-rate")
            os.makedirs(video_temp_dir)

            from tools.video_crawler.resume import SegmentManifest

            manifest = SegmentManifest(
                os.path.join(video_temp_dir, ".firefly-segments.json")
            )
            for index in range(96):
                filename = f"{index:05d}.ts"
                path = os.path.join(video_temp_dir, filename)
                with open(path, "wb") as segment_file:
                    segment_file.write(b"cached")
                manifest.mark_downloaded(
                    filename,
                    url=f"https://cdn.example.test/{filename}",
                    size=os.path.getsize(path),
                )
            manifest.save()

            with patch(
                "tools.video_crawler.adapters.hls.m3u8.load",
                return_value=fake_playlist(100),
            ):
                output_path = asyncio.run(
                    spider._download_m3u8(
                        "https://cdn.example.test/index.m3u8",
                        "resume-rate",
                    )
                )

            self.assertEqual(len(spider.download_calls), 4)
            self.assertTrue(os.path.exists(output_path))

    def test_m3u8_failure_preserves_manifest_and_downloaded_segments(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = ResumeRecordingSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                fail_urls={"https://cdn.example.test/00001.ts"},
            )

            with patch(
                "tools.video_crawler.adapters.hls.m3u8.load",
                return_value=fake_playlist(2),
            ):
                with self.assertRaises(VideoDownloadError):
                    asyncio.run(
                        spider._download_m3u8(
                            "https://cdn.example.test/index.m3u8",
                            "resume-fail",
                        )
                    )

            video_temp_dir = os.path.join(temp_dir, "resume-fail")
            self.assertTrue(
                os.path.exists(os.path.join(video_temp_dir, ".firefly-segments.json"))
            )
            self.assertTrue(os.path.exists(os.path.join(video_temp_dir, "00000.ts")))

    def test_m3u8_resume_keeps_byterange_offset_after_skipped_segment(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = ResumeRecordingSpider(output_dir=temp_dir, temp_dir=temp_dir)
            video_temp_dir = os.path.join(temp_dir, "resume-range")
            os.makedirs(video_temp_dir)
            completed_path = os.path.join(video_temp_dir, "00000.ts")
            with open(completed_path, "wb") as segment_file:
                segment_file.write(b"cached")

            from tools.video_crawler.resume import SegmentManifest

            manifest = SegmentManifest(
                os.path.join(video_temp_dir, ".firefly-segments.json")
            )
            manifest.mark_downloaded(
                "00000.ts",
                url="https://cdn.example.test/00000.ts",
                size=os.path.getsize(completed_path),
            )
            manifest.save()

            with patch(
                "tools.video_crawler.adapters.hls.m3u8.load",
                return_value=fake_playlist(2, byteranges=["100@0", "100"]),
            ):
                asyncio.run(
                    spider._download_m3u8(
                        "https://cdn.example.test/index.m3u8",
                        "resume-range",
                    )
                )

            self.assertEqual(spider.download_headers[0]["Range"], "bytes=100-199")

    def test_hls_adapter_receives_live_record_seconds(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = UniversalVideoSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                live_record_seconds=42,
            )

            adapter = spider._hls_adapter()

        self.assertEqual(adapter.live_record_seconds, 42)


class VideoDownloaderToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tool = VideoDownloaderTool(start_worker=False)

    def tearDown(self):
        self.tool.close()

    def test_mode_switch_restores_default_concurrency(self):
        self.assertEqual(self.tool.concurrency_spin.value(), 5)

        self.tool.toggle_mode()
        self.assertEqual(self.tool.concurrency_spin.value(), 30)

        self.tool.concurrency_spin.setValue(12)
        self.tool.toggle_mode()
        self.assertEqual(self.tool.concurrency_spin.value(), 5)

    def test_default_sniff_wait_is_25_seconds(self):
        self.assertEqual(self.tool.sniff_wait_spin.value(), 25)

    def test_system_chrome_mode_defaults_off(self):
        self.assertFalse(self.tool.system_chrome_chk.isChecked())
        self.assertIn("实验", self.tool.system_chrome_chk.text())

    def test_diagnose_button_is_available(self):
        self.assertEqual(self.tool.diagnose_btn.text(), "诊断链接")

    def test_edge_controls_have_safe_disconnected_defaults(self):
        self.assertEqual(self.tool.edge_status_label.text(), "未连接")
        self.assertEqual(self.tool.edge_wait_btn.text(), "等待 Edge 捕获")
        self.assertEqual(self.tool.edge_paste_btn.text(), "粘贴 Edge 候选")
        self.assertIsNone(self.tool._pending_edge_candidate)

    def test_paste_reads_clipboard_on_click_and_requires_confirmation(self):
        reads = []
        dialog = RecordingEdgeDialog(accepted=True)
        tool = VideoDownloaderTool(
            start_worker=False,
            clipboard_getter=lambda: reads.append(True)
            or json.dumps(valid_edge_message()),
            edge_dialog_factory=lambda candidate, parent: dialog.bind(candidate),
            now=fixed_edge_now,
        )
        try:
            self.assertEqual(reads, [])

            tool.edge_paste_btn.click()

            self.assertEqual(reads, [True])
            self.assertTrue(dialog.shown)
            self.assertEqual(
                tool.url_entry.text(),
                valid_edge_message()["candidate"]["url"],
            )
            self.assertIs(tool._pending_edge_candidate, dialog.candidate)
            self.assertFalse(tool.visible_sniff_chk.isEnabled())
            self.assertFalse(tool.persistent_profile_chk.isEnabled())
            self.assertFalse(tool.system_chrome_chk.isEnabled())
            self.assertFalse(tool.sniff_wait_spin.isEnabled())
            self.assertEqual(tool.edge_status_label.text(), "已收到候选")
        finally:
            tool.close()

    def test_rejected_paste_preserves_existing_url_and_pending_candidate(self):
        message = valid_edge_message()
        dialog = RecordingEdgeDialog(accepted=True)
        tool = VideoDownloaderTool(
            start_worker=False,
            clipboard_getter=lambda: json.dumps(message),
            edge_dialog_factory=lambda candidate, parent: dialog.bind(candidate),
            now=fixed_edge_now,
        )
        try:
            tool.paste_edge_candidate()
            original_url = tool.url_entry.text()
            original_candidate = tool._pending_edge_candidate

            message["candidate"]["url"] = "https://other.example.test/video.mp4"
            message["candidate"]["kind"] = "direct_mp4"
            dialog.accepted = False
            tool.paste_edge_candidate()

            self.assertEqual(tool.url_entry.text(), original_url)
            self.assertIs(tool._pending_edge_candidate, original_candidate)
            self.assertEqual(tool.edge_status_label.text(), "已收到候选")
        finally:
            tool.close()

    def test_expired_paste_warns_without_changing_current_input(self):
        dialog = RecordingEdgeDialog(accepted=True)
        tool = VideoDownloaderTool(
            start_worker=False,
            clipboard_getter=lambda: json.dumps(valid_edge_message()),
            edge_dialog_factory=lambda candidate, parent: dialog.bind(candidate),
            now=lambda: datetime(
                2026,
                8,
                30,
                12,
                6,
                tzinfo=timezone.utc,
            ),
        )
        tool.url_entry.setText("https://existing.example.test/watch")
        try:
            with patch("tools.video_downloader.QMessageBox.warning") as warning:
                tool.paste_edge_candidate()

            self.assertFalse(dialog.shown)
            self.assertEqual(
                tool.url_entry.text(),
                "https://existing.example.test/watch",
            )
            self.assertIsNone(tool._pending_edge_candidate)
            self.assertIn("过期", warning.call_args.args[2])
        finally:
            tool.close()

    def test_invalid_json_or_protocol_warns_without_changing_current_input(self):
        clipboard = ["not json"]
        dialog_calls = []
        tool = VideoDownloaderTool(
            start_worker=False,
            clipboard_getter=lambda: clipboard[0],
            edge_dialog_factory=lambda candidate, parent: dialog_calls.append(
                candidate
            ),
            now=fixed_edge_now,
        )
        tool.url_entry.setText("https://existing.example.test/watch")
        try:
            invalid_version = valid_edge_message()
            invalid_version["protocol_version"] = 2
            with patch("tools.video_downloader.QMessageBox.warning") as warning:
                for raw in ("not json", json.dumps(invalid_version)):
                    with self.subTest(raw=raw[:20]):
                        clipboard[0] = raw
                        tool.paste_edge_candidate()
                        self.assertEqual(
                            tool.url_entry.text(),
                            "https://existing.example.test/watch",
                        )
                        self.assertIsNone(tool._pending_edge_candidate)

            self.assertEqual(dialog_calls, [])
            self.assertEqual(warning.call_count, 2)
            self.assertTrue(
                all("无效" in call.args[2] for call in warning.call_args_list)
            )
        finally:
            tool.close()

    def test_clearing_confirmed_candidate_restores_playwright_controls(self):
        dialog = RecordingEdgeDialog(accepted=True)
        tool = VideoDownloaderTool(
            start_worker=False,
            clipboard_getter=lambda: json.dumps(valid_edge_message()),
            edge_dialog_factory=lambda candidate, parent: dialog.bind(candidate),
            now=fixed_edge_now,
        )
        try:
            tool.paste_edge_candidate()

            tool.clear_edge_candidate()

            self.assertIsNone(tool._pending_edge_candidate)
            self.assertEqual(tool.edge_status_label.text(), "未连接")
            self.assertTrue(tool.visible_sniff_chk.isEnabled())
            self.assertTrue(tool.persistent_profile_chk.isEnabled())
            self.assertTrue(tool.system_chrome_chk.isEnabled())
            self.assertTrue(tool.sniff_wait_spin.isEnabled())
        finally:
            tool.close()

    def test_confirmed_candidate_blocks_waiting_transition(self):
        dialog = RecordingEdgeDialog(accepted=True)
        tool = VideoDownloaderTool(
            start_worker=False,
            clipboard_getter=lambda: json.dumps(valid_edge_message()),
            edge_dialog_factory=lambda candidate, parent: dialog.bind(candidate),
            now=fixed_edge_now,
        )
        try:
            tool.paste_edge_candidate()
            candidate = tool._pending_edge_candidate

            tool.edge_wait_btn.click()

            self.assertIs(tool._pending_edge_candidate, candidate)
            self.assertFalse(tool._edge_waiting)
            self.assertEqual(tool.edge_status_label.text(), "已收到候选")

            tool.toggle_edge_waiting()

            self.assertIs(tool._pending_edge_candidate, candidate)
            self.assertFalse(tool._edge_waiting)
            self.assertEqual(tool.edge_status_label.text(), "已收到候选")
            self.assertFalse(tool.edge_wait_btn.isEnabled())
            self.assertFalse(tool.visible_sniff_chk.isEnabled())
            self.assertFalse(tool.persistent_profile_chk.isEnabled())
            self.assertFalse(tool.system_chrome_chk.isEnabled())
            self.assertFalse(tool.sniff_wait_spin.isEnabled())
        finally:
            tool.close()

    def test_wait_button_only_toggles_visible_waiting_state(self):
        self.tool.edge_wait_btn.click()

        self.assertEqual(self.tool.edge_status_label.text(), "等待捕获")
        self.assertEqual(self.tool.edge_wait_btn.text(), "停止等待")

        self.tool.edge_wait_btn.click()

        self.assertEqual(self.tool.edge_status_label.text(), "未连接")
        self.assertEqual(self.tool.edge_wait_btn.text(), "等待 Edge 捕获")

    def test_edge_confirmation_dialog_shows_only_safe_candidate_metadata(self):
        self.assertIsNotNone(EdgeCandidateDialog)
        for headers_present in (True, False):
            with self.subTest(headers_present=headers_present):
                message = valid_edge_message()
                if not headers_present:
                    message["candidate"]["headers"] = {}
                candidate = parse_candidate_json(json.dumps(message))
                dialog = EdgeCandidateDialog(candidate)
                try:
                    text = "\n".join(
                        label.text() for label in dialog.findChildren(QLabel)
                    )
                finally:
                    dialog.close()

                self.assertIn("页面来源: example.test", text)
                self.assertIn("媒体主机: cdn.example.test", text)
                self.assertIn("媒体类型: HLS", text)
                self.assertIn("捕获时间 (UTC): 2026-08-30 12:00:00", text)
                expected = "是" if headers_present else "否"
                self.assertIn(f"包含临时请求头: {expected}", text)
                self.assertNotIn("token=opaque", text)
                self.assertNotIn("Edge UA", text)
                self.assertNotIn("https://example.test/", text)
                self.assertNotIn("Referer", text)
                self.assertNotIn("Origin", text)

    def test_settings_area_is_scrollable_and_split_into_rows(self):
        self.assertIsInstance(self.tool.scroll_area, QScrollArea)
        self.assertTrue(self.tool.scroll_area.widgetResizable())
        self.assertEqual(
            self.tool.scroll_area.verticalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.assertNotEqual(
            self.tool.container.minimumWidth(),
            self.tool.container.maximumWidth(),
        )

        self.assertGreaterEqual(
            self.tool.download_options_layout.indexOf(self.tool.concurrency_spin),
            0,
        )
        self.assertEqual(
            self.tool.download_options_layout.indexOf(self.tool.visible_sniff_chk),
            -1,
        )
        self.assertGreaterEqual(
            self.tool.sniff_options_layout.indexOf(self.tool.visible_sniff_chk),
            0,
        )
        self.assertGreaterEqual(
            self.tool.sniff_options_layout.indexOf(self.tool.sniff_wait_spin),
            0,
        )

    def test_scroll_area_background_remains_transparent(self):
        self.assertTrue(
            self.tool.scroll_area.testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground
            )
        )
        self.assertTrue(
            self.tool.scroll_area.viewport().testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground
            )
        )
        self.assertTrue(
            self.tool.scroll_content.testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground
            )
        )
        self.assertIn("background: transparent", self.tool.scroll_area.styleSheet())

    def test_diagnose_task_logs_static_report(self):
        self.tool._diagnose_task("https://cdn.example.invalid/video.mp4")

        self.assertIn("DIRECT_MP4", self.tool.log_text.toPlainText())

    def test_diagnose_task_uses_ui_sniffer_options(self):
        self.tool.visible_sniff_chk.setChecked(True)
        self.tool.persistent_profile_chk.setChecked(True)
        self.tool.system_chrome_chk.setChecked(True)
        self.tool.sniff_wait_spin.setValue(22)

        with patch("tools.video_downloader.PageSniffer") as sniffer_class:
            sniffer_class.return_value.sniff.return_value = DiagnosticReport(
                source_url="https://example.test/watch"
            )
            self.tool._diagnose_task("https://example.test/watch")

        options = sniffer_class.call_args.kwargs["options"]
        self.assertFalse(options.headless)
        self.assertTrue(options.use_persistent_profile)
        self.assertTrue(options.use_system_chrome)
        self.assertEqual(options.manual_wait_seconds, 22)

    def test_append_log_redacts_sensitive_text(self):
        self.tool.append_log(
            "https://cdn.example.test/video.mp4?token=secret "
            "Authorization: Bearer hidden"
        )

        text = self.tool.log_text.toPlainText()

        self.assertIn("token=<redacted>", text)
        self.assertIn("Authorization: <redacted>", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("Bearer hidden", text)

    def test_added_task_snapshots_custom_concurrency(self):
        self.tool.url_entry.setText("https://example.invalid/video.m3u8")
        self.tool.name_entry.setText("example")
        self.tool.path_entry.setText("downloads")
        self.tool.concurrency_spin.setValue(12)

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertEqual(task["segment_concurrency"], 12)
        self.assertFalse(task["is_high_speed"])
        self.assertIn("12并发", self.tool.queue_listbox.item(0).text())
        self.tool.task_queue.task_done()

    def test_added_task_snapshots_resume_setting(self):
        self.tool.url_entry.setText("https://example.invalid/video.m3u8")
        self.tool.name_entry.setText("example")
        self.tool.path_entry.setText("downloads")
        self.tool.resume_chk.setChecked(False)

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertFalse(task["resume_enabled"])
        self.tool.task_queue.task_done()

    def test_added_task_snapshots_ytdlp_setting(self):
        self.tool.url_entry.setText("https://example.invalid/watch")
        self.tool.name_entry.setText("example")
        self.tool.path_entry.setText("downloads")
        self.tool.ytdlp_chk.setChecked(True)

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertTrue(task["use_ytdlp_fallback"])
        self.tool.task_queue.task_done()

    def test_added_task_snapshots_live_record_seconds(self):
        self.tool.url_entry.setText("https://example.invalid/live.m3u8")
        self.tool.name_entry.setText("live")
        self.tool.path_entry.setText("downloads")
        self.tool.live_seconds_spin.setValue(45)

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertEqual(task["live_record_seconds"], 45)
        self.tool.task_queue.task_done()

    def test_added_task_snapshots_sniffer_options(self):
        self.tool.url_entry.setText("https://example.invalid/watch")
        self.tool.name_entry.setText("example")
        self.tool.path_entry.setText("downloads")
        self.tool.visible_sniff_chk.setChecked(True)
        self.tool.persistent_profile_chk.setChecked(True)
        self.tool.system_chrome_chk.setChecked(True)
        self.tool.sniff_wait_spin.setValue(25)

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertFalse(task["sniffer_headless"])
        self.assertTrue(task["sniffer_use_persistent_profile"])
        self.assertTrue(task["sniffer_use_system_chrome"])
        self.assertEqual(task["sniffer_manual_wait_seconds"], 25)
        self.tool.task_queue.task_done()

    def test_added_ordinary_task_marks_url_input_without_edge_payload(self):
        self.tool.url_entry.setText("https://example.invalid/video.mp4")
        self.tool.name_entry.setText("ordinary-video")
        self.tool.path_entry.setText("downloads")

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertEqual(task["input_source"], "url")
        self.assertIsNone(task["edge_candidate"])
        self.tool.task_queue.task_done()

    def test_confirmed_edge_candidate_is_frozen_before_ui_state_clears(self):
        candidate = parse_candidate_json(json.dumps(valid_edge_message()))
        self.tool._activate_edge_candidate(candidate)
        self.tool.name_entry.setText("edge-video")
        self.tool.path_entry.setText("downloads")

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertEqual(task["input_source"], "edge")
        self.assertEqual(task["url"], candidate.media_url)
        self.assertEqual(task["edge_candidate"], serialize_candidate(candidate))
        self.assertTrue(task["sniffer_headless"])
        self.assertFalse(task["sniffer_use_persistent_profile"])
        self.assertFalse(task["sniffer_use_system_chrome"])
        self.assertIsNone(self.tool._pending_edge_candidate)
        self.assertEqual(
            task["edge_candidate"],
            serialize_candidate(candidate),
        )
        self.tool.task_queue.task_done()

    def test_successful_queue_add_clears_confirmed_edge_candidate(self):
        dialog = RecordingEdgeDialog(accepted=True)
        tool = VideoDownloaderTool(
            start_worker=False,
            clipboard_getter=lambda: json.dumps(valid_edge_message()),
            edge_dialog_factory=lambda candidate, parent: dialog.bind(candidate),
            now=fixed_edge_now,
        )
        try:
            tool.paste_edge_candidate()
            tool.name_entry.setText("edge-video")

            tool.add_to_queue()

            self.assertEqual(tool.task_queue.qsize(), 1)
            self.assertIsNone(tool._pending_edge_candidate)
            self.assertEqual(tool.edge_status_label.text(), "未连接")
            self.assertTrue(tool.edge_wait_btn.isEnabled())
            self.assertTrue(tool.visible_sniff_chk.isEnabled())
            self.assertTrue(tool.persistent_profile_chk.isEnabled())
            self.assertTrue(tool.system_chrome_chk.isEnabled())
            self.assertTrue(tool.sniff_wait_spin.isEnabled())
        finally:
            tool.close()

    def test_failed_queue_validation_preserves_confirmed_edge_candidate(self):
        dialog = RecordingEdgeDialog(accepted=True)
        tool = VideoDownloaderTool(
            start_worker=False,
            clipboard_getter=lambda: json.dumps(valid_edge_message()),
            edge_dialog_factory=lambda candidate, parent: dialog.bind(candidate),
            now=fixed_edge_now,
        )
        try:
            tool.paste_edge_candidate()
            candidate = tool._pending_edge_candidate
            tool.name_entry.clear()

            with patch("tools.video_downloader.QMessageBox.warning"):
                tool.add_to_queue()

            self.assertEqual(tool.task_queue.qsize(), 0)
            self.assertIs(tool._pending_edge_candidate, candidate)
            self.assertEqual(tool.edge_status_label.text(), "已收到候选")
            self.assertFalse(tool.edge_wait_btn.isEnabled())
            self.assertFalse(tool.visible_sniff_chk.isEnabled())
            self.assertFalse(tool.persistent_profile_chk.isEnabled())
            self.assertFalse(tool.system_chrome_chk.isEnabled())
            self.assertFalse(tool.sniff_wait_spin.isEnabled())
        finally:
            tool.close()

    def test_worker_passes_task_concurrency_to_spider(self):
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=RecordingSpider,
        )
        task = {
            "url": "https://example.invalid/video.m3u8",
            "name": "example",
            "save_dir": "downloads",
            "is_high_speed": True,
            "segment_concurrency": 17,
            "resume_enabled": False,
            "live_record_seconds": 123,
            "sniffer_headless": False,
            "sniffer_use_persistent_profile": True,
            "sniffer_use_system_chrome": True,
            "sniffer_manual_wait_seconds": 33,
        }

        result = tool._execute_task(task)

        self.assertTrue(result["success"])
        self.assertEqual(RecordingSpider.init_kwargs["segment_concurrency"], 17)
        self.assertFalse(RecordingSpider.init_kwargs["resume_enabled"])
        self.assertEqual(RecordingSpider.init_kwargs["live_record_seconds"], 123)
        options = RecordingSpider.init_kwargs["sniffer_options"]
        self.assertFalse(options.headless)
        self.assertTrue(options.use_persistent_profile)
        self.assertTrue(options.use_system_chrome)
        self.assertEqual(options.manual_wait_seconds, 33)
        tool.close()

    def test_worker_revalidates_edge_task_and_passes_safe_session_snapshot(self):
        candidate = parse_candidate_json(json.dumps(valid_edge_message()))
        task = complete_task_fixture()
        task.update(
            input_source="edge",
            edge_candidate=serialize_candidate(candidate),
        )
        RecordingSpider.init_kwargs = None
        RecordingSpider.run_args = None
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=RecordingSpider,
            now=fixed_edge_now,
        )

        try:
            result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertTrue(result["success"])
        self.assertEqual(RecordingSpider.run_args, (candidate.media_url, "edge-video"))
        snapshot = RecordingSpider.init_kwargs["session_snapshot"]
        self.assertEqual(snapshot, candidate.to_session_snapshot())
        self.assertEqual(snapshot.user_agent, "Edge UA")
        self.assertEqual(snapshot.cookies, ())
        self.assertNotIn("Authorization", snapshot.headers)
        self.assertNotIn("Cookie", snapshot.headers)

    def test_worker_rejects_expired_edge_task_before_spider_construction(self):
        candidate = parse_candidate_json(json.dumps(valid_edge_message()))
        task = complete_task_fixture()
        task.update(
            input_source="edge",
            edge_candidate=serialize_candidate(candidate),
        )
        RecordingSpider.init_kwargs = None
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=RecordingSpider,
            now=lambda: datetime(2026, 8, 30, 12, 6, tzinfo=timezone.utc),
        )

        try:
            result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertFalse(result["success"])
        self.assertEqual(
            result["error_code"],
            VideoErrorCode.EDGE_CANDIDATE_EXPIRED.value,
        )
        self.assertFalse(result["retryable"])
        self.assertIsNone(RecordingSpider.init_kwargs)

    def test_worker_rejects_invalid_or_mismatched_edge_task_before_spider(self):
        candidate = parse_candidate_json(json.dumps(valid_edge_message()))
        invalid_tasks = []

        invalid_payload_task = complete_task_fixture()
        invalid_payload_task.update(
            input_source="edge",
            edge_candidate={"candidate": "tampered"},
        )
        invalid_tasks.append(invalid_payload_task)

        mismatched_url_task = complete_task_fixture()
        mismatched_url_task.update(
            input_source="edge",
            edge_candidate=serialize_candidate(candidate),
            url="https://cdn.example.test/tampered.m3u8",
        )
        invalid_tasks.append(mismatched_url_task)

        for task in invalid_tasks:
            with self.subTest(task=task):
                RecordingSpider.init_kwargs = None
                tool = VideoDownloaderTool(
                    start_worker=False,
                    spider_factory=RecordingSpider,
                    now=fixed_edge_now,
                )
                try:
                    result = tool._execute_task(task)
                finally:
                    tool.close()

                self.assertFalse(result["success"])
                self.assertEqual(
                    result["error_code"],
                    VideoErrorCode.EDGE_CANDIDATE_INVALID.value,
                )
                self.assertFalse(result["retryable"])
                self.assertIsNone(RecordingSpider.init_kwargs)

    def test_worker_uses_25_second_wait_for_legacy_task_snapshot(self):
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=RecordingSpider,
        )
        task = {
            "url": "https://example.test/watch",
            "name": "video",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
            "resume_enabled": True,
            "live_record_seconds": 300,
        }

        try:
            result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertTrue(result["success"])
        self.assertEqual(
            RecordingSpider.init_kwargs[
                "sniffer_options"
            ].manual_wait_seconds,
            25,
        )
        self.assertFalse(
            RecordingSpider.init_kwargs["sniffer_options"].use_system_chrome
        )

    def test_execute_task_returns_structured_video_error(self):
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=StructuredFailureSpider,
        )
        task = {
            "url": "https://example.invalid/blocked.m3u8",
            "name": "blocked",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
        }

        try:
            result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "服务器拒绝访问")
        self.assertEqual(result["error_code"], VideoErrorCode.HTTP_FORBIDDEN.value)
        self.assertFalse(result["retryable"])

    def test_execute_task_marks_generic_error_unknown_and_not_retryable(self):
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=GenericFailureSpider,
        )
        task = {
            "url": "https://example.invalid/error.m3u8",
            "name": "error",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
        }

        try:
            result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "unexpected boom")
        self.assertEqual(result["error_code"], VideoErrorCode.UNKNOWN.value)
        self.assertFalse(result["retryable"])

    def test_batch_summary_suggests_network_checks_for_timeout(self):
        summary, details = VideoDownloaderTool.format_batch_results(
            [
                {
                    "task": {"name": "video", "url": "https://example.test/watch"},
                    "success": False,
                    "error": "读取 M3U8 超时",
                    "error_code": VideoErrorCode.NETWORK_TIMEOUT.value,
                    "retryable": True,
                }
            ]
        )

        self.assertIn("失败 1 个", summary)
        self.assertIn("NETWORK_TIMEOUT", details)
        self.assertIn("网络超时", details)
        self.assertIn("可视化嗅探", details)

    def test_ytdlp_fallback_runs_for_no_media_when_enabled(self):
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=NoMediaFailureSpider,
        )
        task = {
            "url": "https://example.invalid/watch?token=secret",
            "name": "public-page",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
            "use_ytdlp_fallback": True,
        }

        try:
            with patch("tools.video_downloader.YtDlpAdapter") as adapter_class:
                adapter = adapter_class.return_value
                adapter.download.return_value = os.path.join(
                    "downloads",
                    "public-page.mp4",
                )

                result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertTrue(result["success"])
        self.assertEqual(result["engine"], "yt-dlp")
        self.assertEqual(result["output_path"], os.path.join("downloads", "public-page.mp4"))
        self.assertTrue(adapter_class.call_args.kwargs["enabled"])
        self.assertEqual(adapter_class.call_args.kwargs["output_dir"], "downloads")
        adapter.download.assert_called_once_with(task["url"], task["name"])

    def test_ytdlp_fallback_runs_for_unsupported_dash_when_enabled(self):
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=UnsupportedDashFailureSpider,
        )
        task = {
            "url": "https://example.invalid/manifest.mpd",
            "name": "dash-page",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
            "use_ytdlp_fallback": True,
        }

        try:
            with patch("tools.video_downloader.YtDlpAdapter") as adapter_class:
                adapter_class.return_value.download.return_value = os.path.join(
                    "downloads",
                    "dash-page.mp4",
                )

                result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertTrue(result["success"])
        self.assertEqual(result["engine"], "yt-dlp")
        adapter_class.return_value.download.assert_called_once_with(
            task["url"],
            task["name"],
        )

    def test_ytdlp_fallback_not_used_when_builtin_download_succeeds(self):
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=RecordingSpider,
        )
        task = {
            "url": "https://example.invalid/video.mp4",
            "name": "direct",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
            "use_ytdlp_fallback": True,
        }

        try:
            with patch("tools.video_downloader.YtDlpAdapter") as adapter_class:
                result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertTrue(result["success"])
        self.assertNotEqual(result.get("engine"), "yt-dlp")
        adapter_class.assert_not_called()

    def test_ytdlp_fallback_not_used_when_disabled(self):
        tool = VideoDownloaderTool(
            start_worker=False,
            spider_factory=NoMediaFailureSpider,
        )
        task = {
            "url": "https://example.invalid/watch",
            "name": "page",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
            "use_ytdlp_fallback": False,
        }

        try:
            with patch("tools.video_downloader.YtDlpAdapter") as adapter_class:
                result = tool._execute_task(task)
        finally:
            tool.close()

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], VideoErrorCode.NO_MEDIA_FOUND.value)
        adapter_class.assert_not_called()

    def test_batch_emits_once_after_all_tasks_finish(self):
        self.tool.batch_finished_signal.disconnect(self.tool.show_batch_results)
        batches = []
        self.tool.batch_finished_signal.connect(batches.append)
        first = {"name": "one"}
        second = {"name": "two"}
        self.tool.task_queue.put(first)
        self.tool.task_queue.put(second)

        self.tool.task_queue.get_nowait()
        self.tool._finish_task({
            "task": first,
            "success": True,
            "output_path": "one.mp4",
            "error": "",
        })
        self.assertEqual(batches, [])

        self.tool.task_queue.get_nowait()
        self.tool._finish_task({
            "task": second,
            "success": False,
            "output_path": "",
            "error": "failed",
        })
        self.assertEqual(len(batches), 1)
        self.assertEqual(
            [item["task"]["name"] for item in batches[0]],
            ["one", "two"],
        )

    def test_retry_requeues_only_failed_tasks_with_original_configuration(self):
        failed_task = {
            "url": "https://example.invalid/fail.m3u8",
            "name": "failed",
            "save_dir": "downloads",
            "is_high_speed": True,
            "segment_concurrency": 17,
        }
        results = [
            {
                "task": {"name": "ok"},
                "success": True,
                "output_path": "ok.mp4",
                "error": "",
            },
            {
                "task": failed_task,
                "success": False,
                "output_path": "",
                "error": "network",
            },
        ]

        self.tool.retry_failed_tasks(results)
        retried = self.tool.task_queue.get_nowait()

        self.assertEqual(retried, failed_task)
        self.assertTrue(self.tool.task_queue.empty())
        self.tool.task_queue.task_done()

    def test_retry_requeues_only_retryable_failures(self):
        retryable_task = {
            "url": "https://example.invalid/retry.m3u8",
            "name": "retry",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
        }
        blocked_task = {
            "url": "https://example.invalid/drm.mpd",
            "name": "blocked",
            "save_dir": "downloads",
            "is_high_speed": False,
            "segment_concurrency": 5,
        }

        self.tool.retry_failed_tasks([
            {"task": retryable_task, "success": False, "retryable": True},
            {"task": blocked_task, "success": False, "retryable": False},
        ])

        self.assertEqual(self.tool.task_queue.get_nowait(), retryable_task)
        self.assertTrue(self.tool.task_queue.empty())
        self.tool.task_queue.task_done()

    def test_retry_button_is_only_available_for_retryable_failures(self):
        retryable_results = [
            {"task": {"name": "retry"}, "success": False, "retryable": True}
        ]
        blocked_results = [
            {"task": {"name": "blocked"}, "success": False, "retryable": False}
        ]

        self.assertTrue(self.tool.has_retryable_failures(retryable_results))
        self.assertFalse(self.tool.has_retryable_failures(blocked_results))

    def test_batch_summary_groups_errors_by_code(self):
        summary, details = self.tool.format_batch_results([
            {
                "task": {"name": "blocked"},
                "success": False,
                "output_path": "",
                "error": "服务器拒绝访问",
                "error_code": VideoErrorCode.HTTP_FORBIDDEN.value,
                "retryable": False,
            }
        ])

        self.assertIn("失败 1 个", summary)
        self.assertIn("HTTP_FORBIDDEN", details)
        self.assertIn("服务器拒绝访问", details)
        self.assertIn("不建议直接重试", details)

    def test_batch_summary_suggests_visible_sniffing_for_http_forbidden(self):
        summary, details = self.tool.format_batch_results([
            {
                "task": {"name": "blocked"},
                "success": False,
                "output_path": "",
                "error": "页面访问受限",
                "error_code": VideoErrorCode.HTTP_FORBIDDEN.value,
                "retryable": False,
            }
        ])

        self.assertIn("失败 1 个", summary)
        self.assertIn("HTTP_FORBIDDEN", details)
        self.assertIn("可视化嗅探", details)
        self.assertIn("复用浏览器会话", details)

    def test_batch_summary_contains_success_and_failure_details(self):
        summary, details = self.tool.format_batch_results([
            {
                "task": {"name": "ok"},
                "success": True,
                "output_path": "ok.mp4",
                "error": "",
            },
            {
                "task": {"name": "bad"},
                "success": False,
                "output_path": "",
                "error": "merge failed",
            },
        ])

        self.assertIn("成功 1 个，失败 1 个", summary)
        self.assertIn("ok", details)
        self.assertIn("bad", details)
        self.assertIn("merge failed", details)

    def test_batch_summary_labels_ytdlp_engine(self):
        summary, details = self.tool.format_batch_results([
            {
                "task": {"name": "external"},
                "success": True,
                "output_path": "external.mp4",
                "error": "",
                "engine": "yt-dlp",
            }
        ])

        self.assertIn("成功 1 个", summary)
        self.assertIn("external", details)
        self.assertIn("yt-dlp", details)


if __name__ == "__main__":
    unittest.main()
