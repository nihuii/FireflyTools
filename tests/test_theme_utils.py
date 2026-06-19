import unittest

from tools.theme_utils import get_global_stylesheet


class ThemeUtilsTests(unittest.TestCase):
    def test_spin_box_uses_themed_input_style(self):
        stylesheet = get_global_stylesheet("missing-wallpaper.png")
        self.assertIn("QLineEdit, QSpinBox, QTextEdit, QListWidget", stylesheet)


if __name__ == "__main__":
    unittest.main()
