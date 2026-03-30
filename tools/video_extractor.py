import os
import shutil
import threading
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QCheckBox, QTextEdit, QFileDialog, QMessageBox, QFrame)
from PyQt6.QtCore import pyqtSignal, Qt
from tools.theme_utils import apply_shadow


class VideoExtractorTool(QWidget):
    log_signal = pyqtSignal(str)
    btn_state_signal = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setFixedWidth(600)
        apply_shadow(self.container)  # 应用悬浮阴影

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        main_layout.addWidget(self.container)

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("主文件夹路径:"))
        self.path_entry = QLineEdit()
        top_layout.addWidget(self.path_entry)
        self.browse_btn = QPushButton("浏览选择...")
        self.browse_btn.clicked.connect(self.select_folder)
        top_layout.addWidget(self.browse_btn)
        layout.addLayout(top_layout)

        mid_layout = QHBoxLayout()
        self.clean_chk = QCheckBox("完成后删除空的子文件夹")
        self.clean_chk.setChecked(True)
        mid_layout.addWidget(self.clean_chk)
        mid_layout.addStretch()
        layout.addLayout(mid_layout)

        self.start_button = QPushButton("开始执行提取")
        self.start_button.setMinimumHeight(45)
        self.start_button.clicked.connect(self.start_processing)
        layout.addWidget(self.start_button)

        layout.addWidget(QLabel("运行日志:"))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(150)
        layout.addWidget(self.log_area)

        self.log_signal.connect(self.append_log)
        self.btn_state_signal.connect(self.update_btn)
        self.log_signal.emit("👋 欢迎使用！请点击上方的“浏览选择”按钮指定包含子文件夹的主目录。")

    def append_log(self, msg):
        self.log_area.append(msg)

    def update_btn(self, enabled, text):
        self.start_button.setEnabled(enabled)
        self.start_button.setText(text)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择主文件夹")
        if folder:
            self.path_entry.setText(folder)
            self.log_signal.emit(f"已选择目标路径: {folder}")

    def start_processing(self):
        folder_path = self.path_entry.text().strip()
        if not folder_path or not os.path.isdir(folder_path):
            QMessageBox.warning(self, "提示", "请先选择一个有效的主文件夹路径！")
            return

        need_clean = self.clean_chk.isChecked()
        threading.Thread(target=self.extract_task, args=(folder_path, need_clean), daemon=True).start()

    # (核心业务逻辑保留不变)
    def extract_task(self, root_folder, need_clean):
        self.btn_state_signal.emit(False, "正在处理中...")
        self.log_signal.emit("=" * 40)
        self.log_signal.emit(f"📂 开始扫描文件夹: {root_folder}")

        video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.rmvb', '.m4v', '.ts', '.iso', '.vob',
                            '.webm'}
        moved_count, error_count = 0, 0

        try:
            for current_root, dirs, files in os.walk(root_folder):
                if current_root == root_folder: continue
                for file in files:
                    file_ext = os.path.splitext(file)[1].lower()
                    if file_ext in video_extensions:
                        source_path = os.path.join(current_root, file)
                        target_path = os.path.join(root_folder, file)

                        # 防覆盖重命名逻辑
                        if os.path.exists(target_path):
                            base_name, ext = os.path.splitext(file)
                            counter = 1
                            while os.path.exists(target_path):
                                target_path = os.path.join(root_folder, f"{base_name}_{counter}{ext}")
                                counter += 1
                            self.log_signal.emit(f"⚠️ 发现重名，自动重命名为: {os.path.basename(target_path)}")

                        # 移动文件
                        try:
                            shutil.move(source_path, target_path)
                            self.log_signal.emit(f"✅ 已提取: {file}")
                            moved_count += 1
                        except Exception as e:
                            self.log_signal.emit(f"❌ 移动失败 {file}: {e}")
                            error_count += 1

            self.log_signal.emit("-" * 40)
            self.log_signal.emit(f"🎉 提取阶段完成！成功移动: {moved_count} 个, 失败: {error_count} 个。")

            # 完善清理空文件夹的详细日志反馈
            if need_clean:
                self.log_signal.emit("\n🧹 开始清理空文件夹...")
                removed_count = 0
                for current_root_sub, dirs, files in os.walk(root_folder, topdown=False):
                    if current_root_sub == root_folder: continue
                    try:
                        if not os.listdir(current_root_sub):
                            os.rmdir(current_root_sub)
                            self.log_signal.emit(f"   已删除空目录: {current_root_sub}")
                            removed_count += 1
                    except Exception:
                        pass
                self.log_signal.emit(f"🧹 清理完成，共删除了 {removed_count} 个空目录。")

        except Exception as e:
            self.log_signal.emit(f"\n❌ 发生严重错误: {e}")
        finally:
            self.log_signal.emit("\n✨ 所有操作全部完成！")
            self.btn_state_signal.emit(True, "开始执行提取")