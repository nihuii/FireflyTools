import os
import threading
import queue

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QListWidget, QTextEdit, QFileDialog,
                             QMessageBox, QFrame, QSpinBox, QCheckBox)
from PyQt6.QtCore import pyqtSignal, Qt

from tools.theme_utils import apply_shadow
from tools.video_crawler.adapters.ytdlp import YtDlpAdapter
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.logging_utils import redact_for_display
from tools.video_crawler.sniffer import PageSniffer
from tools.video_crawler.spider import UniversalVideoSpider


# ==========================================
# 核心爬虫业务逻辑层 (严格对齐最新 fMP4+防广告 逻辑)
# ==========================================
class VideoDownloaderTool(QWidget):
    log_signal = pyqtSignal(str)
    queue_pop_signal = pyqtSignal()
    batch_finished_signal = pyqtSignal(object)

    def __init__(self, start_worker=True, spider_factory=UniversalVideoSpider):
        super().__init__()
        self.spider_factory = spider_factory
        self.task_queue = queue.Queue()
        self._batch_results = []
        self.is_high_speed_mode = False  # 默认使用低速稳定模式

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setFixedWidth(650)
        apply_shadow(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        main_layout.addWidget(self.container)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("目标网址:"))
        self.url_entry = QLineEdit()
        row1.addWidget(self.url_entry)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("保存名称:"))
        self.name_entry = QLineEdit("my_video_01")
        row2.addWidget(self.name_entry)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("保存位置:"))
        self.path_entry = QLineEdit(os.path.abspath("./downloads"))
        row3.addWidget(self.path_entry)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self.select_folder)
        row3.addWidget(btn_browse)
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("切片并发数:"))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 100)
        self.concurrency_spin.setValue(5)
        self.concurrency_spin.setSuffix(" 个")
        self.concurrency_spin.setFixedWidth(120)
        row4.addWidget(self.concurrency_spin)
        self.resume_chk = QCheckBox("复用未完成切片")
        self.resume_chk.setChecked(True)
        row4.addWidget(self.resume_chk)
        self.ytdlp_chk = QCheckBox("公开平台失败时尝试 yt-dlp")
        self.ytdlp_chk.setChecked(False)
        row4.addWidget(self.ytdlp_chk)
        self.live_seconds_spin = QSpinBox()
        self.live_seconds_spin.setRange(30, 7200)
        self.live_seconds_spin.setValue(300)
        self.live_seconds_spin.setSuffix(" 秒直播录制")
        self.live_seconds_spin.setFixedWidth(150)
        row4.addWidget(self.live_seconds_spin)
        row4.addStretch()
        layout.addLayout(row4)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # 模式切换按钮 (保留了无硬编码颜色的 Emoji 主题自适应版本)
        self.mode_btn = QPushButton("🛡️ 当前: 低速稳定模式")
        self.mode_btn.clicked.connect(self.toggle_mode)
        btn_layout.addWidget(self.mode_btn)

        self.diagnose_btn = QPushButton("诊断链接")
        self.diagnose_btn.clicked.connect(self.diagnose_current_url)
        btn_layout.addWidget(self.diagnose_btn)

        self.add_btn = QPushButton("➕ 添加到下载队列")
        self.add_btn.clicked.connect(self.add_to_queue)
        btn_layout.addWidget(self.add_btn)
        layout.addLayout(btn_layout)

        layout.addWidget(QLabel("等待队列:"))
        self.queue_listbox = QListWidget()
        self.queue_listbox.setMaximumHeight(80)
        layout.addWidget(self.queue_listbox)

        layout.addWidget(QLabel("运行日志:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        layout.addWidget(self.log_text)

        self.log_signal.connect(self.append_log)
        self.queue_pop_signal.connect(self.pop_queue_ui)
        self.batch_finished_signal.connect(self.show_batch_results)

        self.log_signal.emit("欢迎使用视频爬虫工具！等待添加任务...\n")

        if start_worker:
            threading.Thread(target=self.queue_worker, daemon=True).start()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择保存位置")
        if folder: self.path_entry.setText(folder)

    def append_log(self, message):
        self.log_text.append(redact_for_display(message))
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def pop_queue_ui(self):
        if self.queue_listbox.count() > 0:
            self.queue_listbox.takeItem(0)

    def toggle_mode(self):
        self.is_high_speed_mode = not self.is_high_speed_mode
        if self.is_high_speed_mode:
            self.concurrency_spin.setValue(30)
            self.mode_btn.setText("⚡ 当前: 高速爆发模式")
            self.log_signal.emit("[!] 已切换至【高速爆发模式】: 速度极快，但容易遇到 503 报错导致丢切片。")
        else:
            self.concurrency_spin.setValue(5)
            self.mode_btn.setText("🛡️ 当前: 低速稳定模式")
            self.log_signal.emit("[*] 已切换至【低速稳定模式】: 带防封禁和退避重试，保证视频完整性。")

    def diagnose_current_url(self):
        url = self.url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请先输入目标网址。")
            return
        threading.Thread(target=self._diagnose_task, args=(url,), daemon=True).start()

    def _diagnose_task(self, url):
        try:
            from tools.video_crawler.diagnostics import VideoDiagnosticService
            from tools.video_crawler.reporting import format_diagnostic_report

            service = VideoDiagnosticService(
                sniffer=PageSniffer(headers={}, log_callback=self.log_signal.emit)
            )
            report = service.analyze(url)
            self.log_signal.emit("\n" + format_diagnostic_report(report))
        except Exception as exc:
            self.log_signal.emit(f"\n[X] 诊断失败: {exc}")

    def add_to_queue(self):
        url, name, save_dir = self.url_entry.text().strip(), self.name_entry.text().strip(), self.path_entry.text().strip()
        if not all([url, name, save_dir]):
            QMessageBox.warning(self, "警告", "请填写完整信息！")
            return

        task = {
            "url": url,
            "name": name,
            "save_dir": save_dir,
            "is_high_speed": self.is_high_speed_mode,
            "segment_concurrency": self.concurrency_spin.value(),
            "resume_enabled": self.resume_chk.isChecked(),
            "use_ytdlp_fallback": self.ytdlp_chk.isChecked(),
            "live_record_seconds": self.live_seconds_spin.value(),
        }
        self._enqueue_task(task)
        self.url_entry.clear()

    def _enqueue_task(self, task):
        queued_task = dict(task)
        self.task_queue.put(queued_task)
        mode_label = "高速" if queued_task["is_high_speed"] else "稳定"
        concurrency = queued_task["segment_concurrency"]
        display_text = (
            f"[{mode_label} / {concurrency}并发] "
            f"{queued_task['name']} -> {queued_task['url'][:40]}..."
        )
        self.queue_listbox.addItem(display_text)
        self.log_signal.emit(
            f"[+] 已添加队列 ({mode_label}模式 / {concurrency}并发): "
            f"{queued_task['name']}"
        )

    def _execute_task(self, task):
        try:
            spider = self.spider_factory(
                output_dir=task["save_dir"],
                temp_dir="./temp",
                log_callback=self.log_signal.emit,
                is_high_speed=task["is_high_speed"],
                segment_concurrency=task["segment_concurrency"],
                resume_enabled=task.get("resume_enabled", True),
                live_record_seconds=task.get("live_record_seconds", 300),
            )
            output_path = spider.run(task["url"], task["name"])
            return {
                "task": task,
                "success": True,
                "output_path": output_path,
                "error": "",
                "engine": "builtin",
            }
        except VideoDownloadError as e:
            if self._should_use_ytdlp_fallback(task, e):
                try:
                    return self._execute_ytdlp_fallback(task)
                except VideoDownloadError as fallback_error:
                    self.log_signal.emit(f"\n[X] 错误: {fallback_error}")
                    return {
                        "task": task,
                        "success": False,
                        "output_path": "",
                        "error": str(fallback_error),
                        "error_code": fallback_error.code.value,
                        "retryable": fallback_error.retryable,
                    }
            self.log_signal.emit(f"\n[X] 错误: {e}")
            return {
                "task": task,
                "success": False,
                "output_path": "",
                "error": str(e),
                "error_code": e.code.value,
                "retryable": e.retryable,
            }
        except Exception as e:
            self.log_signal.emit(f"\n[X] 错误: {e}")
            return {
                "task": task,
                "success": False,
                "output_path": "",
                "error": str(e),
                "error_code": VideoErrorCode.UNKNOWN.value,
                "retryable": False,
            }

    @staticmethod
    def _should_use_ytdlp_fallback(task, error):
        return bool(task.get("use_ytdlp_fallback", False)) and error.code in {
            VideoErrorCode.NO_MEDIA_FOUND,
            VideoErrorCode.UNSUPPORTED_DASH,
        }

    def _execute_ytdlp_fallback(self, task):
        self.log_signal.emit("[*] 内置下载未命中可处理媒体，尝试 yt-dlp 外部后备引擎...")
        adapter = YtDlpAdapter(
            enabled=True,
            output_dir=task["save_dir"],
            log_callback=self.log_signal.emit,
        )
        output_path = adapter.download(task["url"], task["name"])
        return {
            "task": task,
            "success": True,
            "output_path": output_path,
            "error": "",
            "engine": "yt-dlp",
        }

    def _finish_task(self, result):
        self._batch_results.append(result)
        self.task_queue.task_done()
        if self.task_queue.unfinished_tasks == 0:
            completed_batch = list(self._batch_results)
            self._batch_results.clear()
            self.batch_finished_signal.emit(completed_batch)

    def retry_failed_tasks(self, results):
        for result in results:
            if not result["success"] and result.get("retryable", True):
                self._enqueue_task(result["task"])

    @staticmethod
    def has_retryable_failures(results):
        return any(
            not result["success"] and result.get("retryable", True)
            for result in results
        )

    @staticmethod
    def format_batch_results(results):
        succeeded = [result for result in results if result["success"]]
        failed = [result for result in results if not result["success"]]
        summary = f"队列处理完成：成功 {len(succeeded)} 个，失败 {len(failed)} 个。"

        detail_lines = []
        if succeeded:
            detail_lines.append("成功任务：")
            detail_lines.extend(
                VideoDownloaderTool._format_success_line(result)
                for result in succeeded
            )
        if failed:
            if detail_lines:
                detail_lines.append("")
            detail_lines.append("失败任务：")
            error_counts = {}
            for result in failed:
                code = result.get("error_code", VideoErrorCode.UNKNOWN.value)
                retry_text = (
                    "可重试" if result.get("retryable", True) else "不建议直接重试"
                )
                error_counts[code] = error_counts.get(code, 0) + 1
                detail_lines.append(
                    f"  ✗ {result['task']['name']}: "
                    f"{result['error']} [{code} / {retry_text}]"
                )
            detail_lines.append("")
            detail_lines.append("错误统计:")
            detail_lines.extend(
                f"  {code}: {count} 个"
                for code, count in sorted(error_counts.items())
            )
            if not VideoDownloaderTool.has_retryable_failures(results):
                detail_lines.append(
                    "  建议: 这些失败不建议直接重试，请先检查链接、权限或资源类型。"
                )
        return summary, "\n".join(detail_lines)

    @staticmethod
    def _format_success_line(result):
        engine_label = " [yt-dlp 外部引擎]" if result.get("engine") == "yt-dlp" else ""
        return f"  ✓ {result['task']['name']}{engine_label}"

    def show_batch_results(self, results):
        summary, details = self.format_batch_results(results)
        failed = [result for result in results if not result["success"]]

        message_box = QMessageBox(self)
        message_box.setWindowTitle("下载队列处理完成")
        message_box.setText(summary)
        message_box.setInformativeText(details)
        message_box.setIcon(
            QMessageBox.Icon.Warning if failed else QMessageBox.Icon.Information
        )

        retry_button = None
        if self.has_retryable_failures(results):
            retry_button = message_box.addButton(
                "重试可恢复失败任务", QMessageBox.ButtonRole.AcceptRole
            )
        message_box.addButton(QMessageBox.StandardButton.Close)
        message_box.exec()
        if retry_button is not None and message_box.clickedButton() is retry_button:
            self.retry_failed_tasks(results)

    def queue_worker(self):
        while True:
            task = self.task_queue.get()
            self.queue_pop_signal.emit()

            self.log_signal.emit("\n" + "=" * 50)
            self.log_signal.emit(f"▶ 开始执行: {task['name']}")
            result = self._execute_task(task)
            self.log_signal.emit(
                f"⏹ 任务 {task['name']} 结束。等待下一个任务...\n"
            )
            self._finish_task(result)
