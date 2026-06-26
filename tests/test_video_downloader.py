import asyncio
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

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

    def __init__(self, **kwargs):
        type(self).init_kwargs = kwargs

    def run(self, url, name):
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

                result = spider._sniff_real_url("https://example.invalid/watch")

            self.assertEqual(result, "https://cdn.example.invalid/video.mp4")

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
                    headers={"Authorization": "Bearer secret"},
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
                with patch.object(spider, "_download_mp4", side_effect=fake_download_mp4):
                    spider.run("https://example.test/watch", "video")

            self.assertEqual(captured_headers["User-Agent"], "Browser UA")
            self.assertEqual(captured_headers["Referer"], "https://example.test/watch")
            self.assertEqual(captured_headers["Origin"], "https://example.test")
            self.assertEqual(captured_headers["Authorization"], "Bearer secret")
            self.assertEqual(captured_headers["Cookie"], "sid=abc")

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

    def test_diagnose_button_is_available(self):
        self.assertEqual(self.tool.diagnose_btn.text(), "诊断链接")

    def test_diagnose_task_logs_static_report(self):
        self.tool._diagnose_task("https://cdn.example.invalid/video.mp4")

        self.assertIn("DIRECT_MP4", self.tool.log_text.toPlainText())

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
        }

        result = tool._execute_task(task)

        self.assertTrue(result["success"])
        self.assertEqual(RecordingSpider.init_kwargs["segment_concurrency"], 17)
        self.assertFalse(RecordingSpider.init_kwargs["resume_enabled"])
        self.assertEqual(RecordingSpider.init_kwargs["live_record_seconds"], 123)
        tool.close()

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
