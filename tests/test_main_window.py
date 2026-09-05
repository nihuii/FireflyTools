import os
from pathlib import Path
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QImage
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QSizeGrip

from tools.image_similarity_tool import ImageSimilarityTool
from tools.main import MediaToolboxApp


class FakeEdgeReceiver(QObject):
    """Record receiver lifecycle calls without binding a real port."""

    candidate_received = pyqtSignal(object)
    status_changed = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.started = False
        self.accepting = False
        self.stopped = False

    def start(self):
        self.started = True

    def set_accepting(self, value):
        self.accepting = bool(value)

    def stop(self):
        self.stopped = True


class MediaToolboxAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MediaToolboxApp(edge_receiver_factory=FakeEdgeReceiver)

    def tearDown(self):
        self.window.close()

    def test_frameless_window_exposes_resize_grip(self):
        self.assertIsInstance(self.window.size_grip, QSizeGrip)
        self.assertIs(self.window.size_grip.parentWidget(), self.window.main_wrapper)
        self.assertNotEqual(
            self.window.minimumSize(),
            self.window.maximumSize(),
        )

    def test_main_window_contains_image_similarity_tab(self):
        """主窗口把图片相似度检测注册为第 5 个工具页。"""
        self.assertEqual(5, self.window.notebook.count())
        self.assertEqual("图片相似度检测", self.window.notebook.tabText(4))
        self.assertIsInstance(self.window.notebook.widget(4), ImageSimilarityTool)

    def test_main_window_starts_and_injects_one_receiver(self):
        self.assertTrue(self.window.edge_receiver.started)
        self.assertIs(
            self.window.video_downloader_tool._edge_receiver,
            self.window.edge_receiver,
        )

    def test_accepted_close_stops_receiver(self):
        event = QCloseEvent()

        self.window.closeEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertTrue(self.window.edge_receiver.stopped)

    def test_close_waits_for_active_scan_and_requests_cancellation(self):
        """扫描仍在运行时主窗口必须忽略关闭并发出协作取消请求。"""
        tool = self.window.notebook.widget(4)
        fake_worker = mock.Mock()
        tool.scan_worker = fake_worker
        tool.scan_thread = object()
        event = QCloseEvent()

        try:
            self.window.closeEvent(event)

            self.assertFalse(event.isAccepted())
            self.assertTrue(self.window._close_pending)
            self.assertFalse(self.window.edge_receiver.stopped)
            fake_worker.cancel.assert_called_once_with()
        finally:
            tool.scan_worker = None
            tool.scan_thread = None

    def test_pending_close_resumes_only_after_recycle_worker_is_idle(self):
        """不可强制终止的回收任务结束后，主窗口才继续原关闭请求。"""
        tool = self.window.notebook.widget(4)
        tool.recycle_thread = object()
        event = QCloseEvent()

        self.window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window._close_pending)

        tool.recycle_thread = None
        tool.recycle_worker = None
        tool._notify_workers_idle()
        self.app.processEvents()

        self.assertFalse(self.window._close_pending)

    def test_close_waits_for_running_thumbnail_decode(self):
        """主窗口关闭时应撤销缩略图队列并等待正在解码的任务退出。"""
        tool = self.window.image_similarity_tool
        release = threading.Event()
        started = threading.Event()

        def blocking_decoder(_path, _size):
            """模拟无法在函数中途强制终止的图片解码调用。"""
            started.set()
            release.wait(3)
            image = QImage(8, 8, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.red)
            return image

        tool.thumbnail_cache.decoder = blocking_decoder
        idle = QSignalSpy(tool.workers_idle)
        tool.thumbnail_cache.get(Path("thumbnail-close-test.png"))
        self.assertTrue(started.wait(1))
        event = QCloseEvent()

        self.window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window._close_pending)
        release.set()
        self.assertTrue(idle.wait(3000))
        self.app.processEvents()
        self.assertFalse(self.window._close_pending)


if __name__ == "__main__":
    unittest.main()
