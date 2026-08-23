from pathlib import Path
import unittest

from PIL import Image

from tools.theme_utils import get_global_stylesheet


class ThemeUtilsTests(unittest.TestCase):
    def _temporary_wallpaper(self, name, color):
        """在已忽略目录中创建一个明确命名的主题测试壁纸。"""
        temp_root = Path(__file__).resolve().parent / ".tmp"
        temp_root.mkdir(exist_ok=True)
        image_path = temp_root / name
        Image.new("RGB", (2, 2), color).save(image_path)
        self.addCleanup(image_path.unlink, missing_ok=True)
        return str(image_path)

    def test_spin_box_uses_themed_input_style(self):
        stylesheet = get_global_stylesheet("missing-wallpaper.png")
        self.assertIn("QLineEdit, QSpinBox, QTextEdit, QListWidget", stylesheet)

    def test_message_box_uses_readable_light_theme(self):
        image_path = self._temporary_wallpaper("theme-light.png", "white")
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
        image_path = self._temporary_wallpaper("theme-dark.png", "black")
        stylesheet = get_global_stylesheet(image_path)

        self.assertIn(
            "QMessageBox {\n            background-color: #252525;",
            stylesheet,
        )
        self.assertIn(
            "QMessageBox QLabel {\n            color: #fdfefe;",
            stylesheet,
        )

    def test_trash_confirmation_dialog_uses_readable_dynamic_theme(self):
        """自定义回收确认框必须拥有与文字颜色匹配的动态背景。"""
        stylesheet = get_global_stylesheet("missing-wallpaper.png")

        self.assertIn(
            "QDialog#trashConfirmationDialog {\n            background-color: #252525;",
            stylesheet,
        )

    def test_similarity_model_views_and_progress_use_dynamic_theme(self):
        """新增结果视图、下拉框和进度条复用动态主题变量。"""
        stylesheet = get_global_stylesheet("missing-wallpaper.png")

        self.assertIn("QTreeView, QListView, QTableView", stylesheet)
        self.assertIn("QHeaderView::section", stylesheet)
        self.assertIn("QComboBox, QPlainTextEdit", stylesheet)
        self.assertIn("QProgressBar", stylesheet)
        self.assertIn("QProgressBar::chunk", stylesheet)


if __name__ == "__main__":
    unittest.main()
