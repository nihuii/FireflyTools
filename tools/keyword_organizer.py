import os
import shutil
import threading
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTextEdit, QFileDialog, QMessageBox, QFrame)
from PyQt6.QtCore import pyqtSignal, Qt
from tools.theme_utils import apply_shadow


class KeywordOrganizerTool(QWidget):
    # 定义用于跨线程更新 UI 的信号
    log_signal = pyqtSignal(str)
    btn_state_signal = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()

        # --- UI 界面搭建 ---
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setFixedWidth(600)
        apply_shadow(self.container)  # 应用统一的悬浮阴影

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        main_layout.addWidget(self.container)

        # 1. 主文件夹选择
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("主文件夹:"))
        self.path_entry = QLineEdit()
        row1.addWidget(self.path_entry)
        btn_browse = QPushButton("📂 选择...")
        btn_browse.clicked.connect(self.select_folder)
        row1.addWidget(btn_browse)
        layout.addLayout(row1)

        # 2. 关键字输入
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("目标关键字:"))
        self.keyword_entry = QLineEdit()
        self.keyword_entry.setPlaceholderText("包含此词的图片或视频将被移动")
        row2.addWidget(self.keyword_entry)
        layout.addLayout(row2)

        # 3. 开始按钮
        self.start_btn = QPushButton("开始归类整理")
        self.start_btn.setMinimumHeight(45)
        self.start_btn.clicked.connect(self.start_processing)
        layout.addWidget(self.start_btn)

        # 4. 日志区域
        layout.addWidget(QLabel("执行日志:"))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(150)
        layout.addWidget(self.log_area)

        # 绑定信号和槽
        self.log_signal.connect(self.append_log)
        self.btn_state_signal.connect(self.update_btn)

    def append_log(self, msg):
        self.log_area.append(msg)
        # 自动滚动到底部
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_btn(self, enabled, text):
        self.start_btn.setEnabled(enabled)
        self.start_btn.setText(text)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择主文件夹")
        if folder:
            self.path_entry.setText(folder)

    def start_processing(self):
        folder_path = self.path_entry.text().strip()
        keyword = self.keyword_entry.text().strip()

        if not folder_path or not os.path.isdir(folder_path):
            QMessageBox.warning(self, "提示", "请先选择一个有效的文件夹路径！")
            return
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入要筛选的关键字！")
            return

        # 启动后台线程执行核心逻辑
        threading.Thread(target=self.organize_task, args=(folder_path, keyword), daemon=True).start()

    def organize_task(self, root_folder, keyword):
        """核心整理逻辑 (在后台线程运行)"""
        self.btn_state_signal.emit(False, "正在处理...")
        self.log_signal.emit("=" * 40)
        self.log_signal.emit(f"📂 正在扫描: {root_folder}")
        self.log_signal.emit(f"🔍 目标关键字: [{keyword}]")
        self.log_signal.emit("=" * 40)

        # 文件类型限制
        video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.rmvb', '.m4v', '.ts', '.webm', '.vob'}
        image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.heic', '.raw'}

        target_subfolder = os.path.join(root_folder, keyword)
        keyword_lower = keyword.lower()
        moved_count = 0

        try:
            # 获取当前目录下的文件（不递归子文件夹）
            files = [f for f in os.listdir(root_folder) if os.path.isfile(os.path.join(root_folder, f))]

            for file in files:
                file_name, file_ext = os.path.splitext(file)
                ext = file_ext.lower()

                # 判断文件类型
                ftype = None
                if ext in video_exts:
                    ftype = "视频"
                elif ext in image_exts:
                    ftype = "图片"
                else:
                    continue  # 忽略无关文件

                # 关键字匹配 (不区分大小写)
                if keyword_lower in file_name.lower():
                    # 懒加载：匹配成功才创建目标文件夹
                    if not os.path.exists(target_subfolder):
                        os.makedirs(target_subfolder)
                        self.log_signal.emit(f"📁 创建归档文件夹: {target_subfolder}")

                    file_path = os.path.join(root_folder, file)
                    target_path = os.path.join(target_subfolder, file)

                    # 防重名覆盖
                    if os.path.exists(target_path):
                        counter = 1
                        base_name = file_name
                        while os.path.exists(target_path):
                            target_path = os.path.join(target_subfolder, f"{base_name}_{counter}{file_ext}")
                            counter += 1
                        self.log_signal.emit(f"⚠️ 文件重名，重命名为: {os.path.basename(target_path)}")

                    # 执行移动
                    try:
                        shutil.move(file_path, target_path)
                        self.log_signal.emit(f"✅ [{ftype}] 已归档: {file}")
                        moved_count += 1
                    except Exception as e:
                        self.log_signal.emit(f"❌ 移动失败 {file}: {e}")

            self.log_signal.emit("-" * 40)
            if moved_count == 0:
                self.log_signal.emit("⚠️ 未找到包含该关键字的视频或图片。")
            else:
                self.log_signal.emit(f"🎉 处理完成！共归档了 {moved_count} 个文件到 '{keyword}' 文件夹。")

        except Exception as e:
            self.log_signal.emit(f"❌ 发生严重错误: {e}")

        finally:
            self.btn_state_signal.emit(True, "开始归类整理")