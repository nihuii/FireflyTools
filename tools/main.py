"""构建 FireflyTools 主窗口、壁纸画布、自定义标题栏和工具标签页。"""

import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QVBoxLayout,
                             QHBoxLayout, QWidget, QLabel, QPushButton, QSizeGrip)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QPainter, QPixmap, QPainterPath, QColor

# 导入工具模块
from tools.edge_companion.receiver import EdgeCaptureReceiver
from tools.video_downloader import VideoDownloaderTool
from tools.video_extractor import VideoExtractorTool
from tools.keyword_organizer import KeywordOrganizerTool
from tools.image_resizer import SmartImageResizerTool
from tools.image_similarity_tool import ImageSimilarityTool
from tools.theme_utils import get_global_stylesheet


class BgWidget(QWidget):
    """负责将背景图铺满整个圆角窗口的底层画布"""

    def __init__(self, parent=None):
        """创建尚未绑定壁纸的透明背景画布。"""
        super().__init__(parent)
        self.bg_pixmap = None

    def paintEvent(self, event):
        """在控件重绘时裁剪圆角区域并铺满当前壁纸。"""
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
    """实现无边框主窗口的拖动、缩放状态切换和标题栏按钮。"""
    def __init__(self, parent):
        """构建自定义标题栏，并把窗口命令连接到主窗口。"""
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
        """在最大化和普通窗口状态之间切换并同步按钮图标。"""
        if self.parent.isMaximized():
            self.parent.showNormal()
            self.btn_max.setText("□")
        else:
            self.parent.showMaximized()
            self.btn_max.setText("❐")

    def mouseDoubleClickEvent(self, event):
        """双击标题栏时切换主窗口最大化状态。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize()

    def mousePressEvent(self, event):
        """记录无边框窗口开始拖动时的全局鼠标位置。"""
        if event.button() == Qt.MouseButton.LeftButton and not self.parent.isMaximized():
            self.start_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        """根据鼠标位移移动未最大化的主窗口。"""
        if self.start_pos is not None and not self.parent.isMaximized():
            delta = event.globalPosition().toPoint() - self.start_pos
            self.parent.move(self.parent.pos() + delta)
            self.start_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        """结束标题栏拖动并清除上一次鼠标位置。"""
        self.start_pos = None


class MediaToolboxApp(QMainWindow):
    """组装五个媒体工具标签页并管理全局壁纸与窗口尺寸。"""
    def __init__(self, edge_receiver_factory=EdgeCaptureReceiver):
        """组装无边框主窗口、五个工具页、缩放手柄和壁纸系统。"""
        super().__init__()
        self.resize(850, 700)
        self._close_pending = False
        self.edge_receiver = edge_receiver_factory()
        self.edge_receiver.start()

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

        self.size_grip = QSizeGrip(self.main_wrapper)
        self.size_grip.setObjectName("windowSizeGrip")
        self.size_grip.raise_()
        self._position_size_grip()

        self.video_downloader_tool = VideoDownloaderTool(
            edge_receiver=self.edge_receiver
        )
        self.notebook.addTab(self.video_downloader_tool, "视频下载爬虫")
        self.notebook.addTab(VideoExtractorTool(), "视频子目录提取")
        self.notebook.addTab(KeywordOrganizerTool(), "关键字归档")
        self.notebook.addTab(SmartImageResizerTool(), "图片智能裁剪")
        self.image_similarity_tool = ImageSimilarityTool()
        self.image_similarity_tool.workers_idle.connect(
            self._resume_pending_close
        )
        self.notebook.addTab(self.image_similarity_tool, "图片相似度检测")

        # 初始化壁纸系统
        self.wallpapers = []
        self.current_wp_idx = 0
        self.load_wallpapers()
        self.apply_wallpaper()

    def load_wallpapers(self):
        # 自动定位 pic 文件夹
        """从项目 pic 目录收集可用的 JPG 和 PNG 壁纸。"""
        curr_dir = os.path.dirname(os.path.abspath(__file__))
        pic_dir = os.path.join(curr_dir, "pic")
        if not os.path.exists(pic_dir):
            pic_dir = os.path.join(curr_dir, "..", "pic")  # 兼容层级

        if os.path.exists(pic_dir):
            for file in os.listdir(pic_dir):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    self.wallpapers.append(os.path.join(pic_dir, file))

    def switch_wallpaper(self):
        """循环切换到下一张壁纸并刷新主题。"""
        if self.wallpapers:
            self.current_wp_idx = (self.current_wp_idx + 1) % len(self.wallpapers)
            self.apply_wallpaper()

    def apply_wallpaper(self):
        """应用当前壁纸，同时重新生成全局样式表。"""
        if self.wallpapers:
            img_path = self.wallpapers[self.current_wp_idx]
            self.main_wrapper.bg_pixmap = QPixmap(img_path)
            # 全局下发带智能色彩提取的 QSS 样式表
            self.setStyleSheet(get_global_stylesheet(img_path))
            self.main_wrapper.update()  # 强制刷新背景绘制
        else:
            self.main_wrapper.bg_pixmap = None
            self.setStyleSheet(get_global_stylesheet(""))
            self.main_wrapper.update()

    def resizeEvent(self, event):
        """窗口尺寸变化后重新定位右下角缩放手柄。"""
        super().resizeEvent(event)
        self._position_size_grip()

    def _position_size_grip(self):
        """把缩放手柄固定到背景画布右下角。"""
        if hasattr(self, "size_grip"):
            grip_size = self.size_grip.sizeHint()
            self.size_grip.setGeometry(
                self.main_wrapper.width() - grip_size.width() - 4,
                self.main_wrapper.height() - grip_size.height() - 4,
                grip_size.width(),
                grip_size.height(),
            )

    def closeEvent(self, event):
        """等待图片扫描或回收任务安全结束后再真正销毁主窗口。"""
        if self.image_similarity_tool.has_active_workers():
            self._close_pending = True
            self.image_similarity_tool.request_shutdown()
            event.ignore()
            return
        self._close_pending = False
        self.edge_receiver.stop()
        super().closeEvent(event)

    def _resume_pending_close(self):
        """后台任务全部空闲后重新投递此前被忽略的主窗口关闭请求。"""
        if (
            self._close_pending
            and not self.image_similarity_tool.has_active_workers()
        ):
            QTimer.singleShot(0, self.close)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)
    window = MediaToolboxApp()
    window.show()
    sys.exit(app.exec())
