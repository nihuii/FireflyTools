import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QRadioButton, QListWidget, QFileDialog, QMessageBox, QFrame, QApplication)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PIL import Image, ImageOps
from tools.theme_utils import apply_shadow


class SmartImageResizerTool(QWidget):
    def __init__(self):
        super().__init__()
        self.file_paths = []
        self.output_dir = os.path.join(os.path.expanduser("~"), "Desktop", "Processed_Images")

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setFixedWidth(600)
        apply_shadow(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        main_layout.addWidget(self.container)

        title = QLabel("图片批量智能裁剪与缩放")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        set_layout = QHBoxLayout()
        set_layout.addWidget(QLabel("目标宽度:"))
        self.width_entry = QLineEdit("320")
        set_layout.addWidget(self.width_entry)
        set_layout.addWidget(QLabel("目标高度:"))
        self.height_entry = QLineEdit("240")
        set_layout.addWidget(self.height_entry)
        layout.addLayout(set_layout)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("智能裁剪重心:"))
        self.radio_center = QRadioButton("居中")
        self.radio_center.setChecked(True)
        self.radio_top = QRadioButton("顶部(保头)")
        self.radio_bottom = QRadioButton("底部(保脚)")
        mode_layout.addWidget(self.radio_center)
        mode_layout.addWidget(self.radio_top)
        mode_layout.addWidget(self.radio_bottom)
        layout.addLayout(mode_layout)

        out_layout = QHBoxLayout()
        out_layout.addWidget(QLabel("输出位置:"))
        self.path_entry = QLineEdit(self.output_dir)
        self.path_entry.setReadOnly(True)
        out_layout.addWidget(self.path_entry)
        btn_out = QPushButton("修改...")
        btn_out.clicked.connect(self.choose_output_dir)
        out_layout.addWidget(btn_out)
        layout.addLayout(out_layout)

        layout.addWidget(QLabel("待处理文件列表:"))
        self.listbox = QListWidget()
        self.listbox.setMaximumHeight(120)
        layout.addWidget(self.listbox)

        btn_layout = QHBoxLayout()
        btn_import = QPushButton("导入图片")
        btn_import.clicked.connect(self.select_files)
        self.btn_run = QPushButton("开始处理")
        self.btn_run.clicked.connect(self.start_processing)
        btn_layout.addWidget(btn_import)
        btn_layout.addWidget(self.btn_run)
        layout.addLayout(btn_layout)

        self.status_label = QLabel("准备就绪")
        layout.addWidget(self.status_label)

    def choose_output_dir(self):
        selected_dir = QFileDialog.getExistingDirectory(self, "选择保存位置")
        if selected_dir:
            self.output_dir = selected_dir
            self.path_entry.setText(self.output_dir)

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择图片", "", "Images (*.jpg *.jpeg *.png *.bmp *.webp *.tiff)")
        if files:
            self.file_paths = files
            self.listbox.clear()
            self.listbox.addItems([os.path.basename(f) for f in files])
            self.status_label.setText(f"已导入 {len(files)} 张图片")

    def start_processing(self):
        if not self.file_paths:
            QMessageBox.warning(self, "提示", "请先导入图片！")
            return

        # 1. 严格的安全校验
        try:
            target_w, target_h = int(self.width_entry.text()), int(self.height_entry.text())
            if target_w <= 0 or target_h <= 0:
                raise ValueError("宽高必须是正整数")
        except ValueError:
            QMessageBox.critical(self, "错误", "宽高必须是大于 0 的正整数！")
            return

        # 2. 文件夹创建的异常捕获
        if not os.path.exists(self.output_dir):
            try:
                os.makedirs(self.output_dir)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法创建保存文件夹:\n{e}")
                return

        if self.radio_top.isChecked():
            centering = (0.5, 0.0)
        elif self.radio_bottom.isChecked():
            centering = (0.5, 1.0)
        else:
            centering = (0.5, 0.5)

        success_count = 0
        self.btn_run.setEnabled(False)

        for file_path in self.file_paths:
            try:
                filename = os.path.basename(file_path)
                self.status_label.setText(f"正在处理: {filename} ...")
                QApplication.processEvents()

                with Image.open(file_path) as img:
                    if img.mode in ('RGBA', 'LA'):
                        bg = Image.new('RGB', img.size, (255, 255, 255))
                        bg.paste(img, mask=img.split()[-1])
                        img = bg
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')

                    new_img = ImageOps.fit(img, (target_w, target_h), method=Image.Resampling.LANCZOS,
                                           centering=centering)
                    save_name = f"{os.path.splitext(filename)[0]}_{target_w}x{target_h}.jpg"
                    new_img.save(os.path.join(self.output_dir, save_name), quality=95)
                    success_count += 1
            except Exception as e:
                print(f"失败 {file_path}: {e}")

        self.btn_run.setEnabled(True)
        self.status_label.setText("处理完成")
        QMessageBox.information(self, "成功", f"处理完成！\n成功保存 {success_count} 张图片至:\n{self.output_dir}")

        # 3. 跨平台支持：完成后自动打开输出文件夹
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_dir))
        except Exception:
            pass