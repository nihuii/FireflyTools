import os
import tempfile
import unittest

from PIL import Image

from tools.theme_utils import get_global_stylesheet


class ThemeUtilsTests(unittest.TestCase):
    def test_spin_box_uses_themed_input_style(self):
        stylesheet = get_global_stylesheet("missing-wallpaper.png")
        self.assertIn("QLineEdit, QSpinBox, QTextEdit, QListWidget", stylesheet)

    def test_message_box_uses_readable_light_theme(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
            image_path = os.path.join(temp_dir, "light.png")
            Image.new("RGB", (2, 2), "white").save(image_path)
            stylesheet = get_global_stylesheet(image_path)

        self.assertIn(
            "QMessageBox {\n            background-color: #f5f5f5;",
            stylesheet,
        )
        self.assertIn(
            "QMessageBox QLabel {\n            color: #1c2833;",
            stylesheet,
        )

    def test_message_box_uses_readable_dark_theme(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
            image_path = os.path.join(temp_dir, "dark.png")
            Image.new("RGB", (2, 2), "black").save(image_path)
            stylesheet = get_global_stylesheet(image_path)

        self.assertIn(
            "QMessageBox {\n            background-color: #252525;",
            stylesheet,
        )
        self.assertIn(
            "QMessageBox QLabel {\n            color: #fdfefe;",
            stylesheet,
        )


if __name__ == "__main__":
    unittest.main()
