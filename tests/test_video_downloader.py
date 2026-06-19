import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from tools.video_downloader import (
    UniversalVideoSpider,
    VideoDownloadError,
    VideoDownloaderTool,
)

TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)


class TrackingSpider(UniversalVideoSpider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_downloads = 0
        self.max_active_downloads = 0

    async def _download_ts(self, session, ts_url, save_path, cipher):
        self.active_downloads += 1
        self.max_active_downloads = max(
            self.max_active_downloads, self.active_downloads
        )
        await asyncio.sleep(0.01)
        self.active_downloads -= 1
        return True


class FailingSegmentSpider(UniversalVideoSpider):
    async def _download_ts(self, session, ts_url, save_path, cipher):
        return ts_url != "bad"


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

    def test_missing_sniffed_stream_is_failure(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            with patch.object(spider, "_sniff_real_url", return_value=None):
                with self.assertRaisesRegex(VideoDownloadError, "未能找到视频流"):
                    spider.run("https://example.invalid/watch", "video")

    def test_empty_output_is_failure(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            output_path = os.path.join(temp_dir, "empty.mp4")
            open(output_path, "wb").close()
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)

            with self.assertRaisesRegex(VideoDownloadError, "输出文件"):
                spider._verify_output(output_path)


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


if __name__ == "__main__":
    unittest.main()
