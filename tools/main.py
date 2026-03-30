import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QVBoxLayout,
                             QHBoxLayout, QWidget, QLabel, QPushButton)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPainter, QPixmap, QPainterPath, QColor

# 导入工具模块
from tools.video_downloader import VideoDownloaderTool
from tools.video_extractor import VideoExtractorTool
from tools.keyword_organizer import KeywordOrganizerTool
from tools.image_resizer import SmartImageResizerTool
from tools.theme_utils import get_global_stylesheet


class BgWidget(QWidget):
    """负责将背景图铺满整个圆角窗口的底层画布"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bg_pixmap = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 裁剪出全局圆角 (确保四角也是圆的)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 15, 15)
        painter.setClipPath(path)

        if self.bg_pixmap and not self.bg_pixmap.isNull():
            # 采用等比例拉伸填充铺满全屏
            scaled = self.bg_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.fillPath(path, QColor("#212121"))  # 无图片时的底色


class CustomTitleBar(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(45)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_label = QLabel(" FireflyTools")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # 切换壁纸按钮
        self.btn_skin = QPushButton("切换壁纸")
        self.btn_skin.setObjectName("skinBtn")
        self.btn_skin.clicked.connect(self.parent.switch_wallpaper)
        layout.addWidget(self.btn_skin)

        # 最小化
        self.btn_min = QPushButton("—")
        self.btn_min.setObjectName("titleBtn")
        self.btn_min.setFixedSize(45, 45)
        self.btn_min.clicked.connect(self.parent.showMinimized)
        layout.addWidget(self.btn_min)

        # 最大化/还原
        self.btn_max = QPushButton("□")
        self.btn_max.setObjectName("titleBtn")
        self.btn_max.setFixedSize(45, 45)
        self.btn_max.clicked.connect(self.toggle_maximize)
        layout.addWidget(self.btn_max)

        # 关闭
        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("closeBtn")
        self.btn_close.setFixedSize(45, 45)
        self.btn_close.clicked.connect(self.parent.close)
        layout.addWidget(self.btn_close)

        self.start_pos = None

    def toggle_maximize(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
            self.btn_max.setText("□")
        else:
            self.parent.showMaximized()
            self.btn_max.setText("❐")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.parent.isMaximized():
            self.start_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self.start_pos is not None and not self.parent.isMaximized():
            delta = event.globalPosition().toPoint() - self.start_pos
            self.parent.move(self.parent.pos() + delta)
            self.start_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.start_pos = None


class MediaToolboxApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(850, 700)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.main_wrapper = BgWidget()
        self.main_wrapper.setObjectName("mainWrapper")
        self.setCentralWidget(self.main_wrapper)

        self.layout = QVBoxLayout(self.main_wrapper)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.title_bar = CustomTitleBar(self)
        self.layout.addWidget(self.title_bar)

        self.notebook = QTabWidget()
        self.layout.addWidget(self.notebook)

        self.notebook.addTab(VideoDownloaderTool(), "视频下载爬虫")
        self.notebook.addTab(VideoExtractorTool(), "视频子目录提取")
        self.notebook.addTab(KeywordOrganizerTool(), "关键字归档")
        self.notebook.addTab(SmartImageResizerTool(), "图片智能裁剪")

        # 初始化壁纸系统
        self.wallpapers = []
        self.current_wp_idx = 0
        self.load_wallpapers()
        self.apply_wallpaper()

    def load_wallpapers(self):
        # 自动定位 pic 文件夹
        curr_dir = os.path.dirname(os.path.abspath(__file__))
        pic_dir = os.path.join(curr_dir, "pic")
        if not os.path.exists(pic_dir):
            pic_dir = os.path.join(curr_dir, "..", "pic")  # 兼容层级

        if os.path.exists(pic_dir):
            for file in os.listdir(pic_dir):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    self.wallpapers.append(os.path.join(pic_dir, file))

    def switch_wallpaper(self):
        if self.wallpapers:
            self.current_wp_idx = (self.current_wp_idx + 1) % len(self.wallpapers)
            self.apply_wallpaper()

    def apply_wallpaper(self):
        if self.wallpapers:
            img_path = self.wallpapers[self.current_wp_idx]
            self.main_wrapper.bg_pixmap = QPixmap(img_path)
            # 全局下发带智能色彩提取的 QSS 样式表
            self.setStyleSheet(get_global_stylesheet(img_path))
            self.main_wrapper.update()  # 强制刷新背景绘制


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)
    window = MediaToolboxApp()
    window.show()
    sys.exit(app.exec())