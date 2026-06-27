import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QSizeGrip

from tools.main import MediaToolboxApp


class MediaToolboxAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MediaToolboxApp()

    def tearDown(self):
        self.window.close()

    def test_frameless_window_exposes_resize_grip(self):
        self.assertIsInstance(self.window.size_grip, QSizeGrip)
        self.assertIs(self.window.size_grip.parentWidget(), self.window.main_wrapper)
        self.assertNotEqual(
            self.window.minimumSize(),
            self.window.maximumSize(),
        )


if __name__ == "__main__":
    unittest.main()
