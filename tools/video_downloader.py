"""实现视频下载爬虫的 PyQt6 界面、任务队列和批次结果反馈。"""

from datetime import datetime, timezone
import os
import threading
import queue

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QListWidget, QTextEdit, QFileDialog,
                             QMessageBox, QFrame, QSpinBox, QCheckBox, QScrollArea,
                             QSizePolicy, QApplication, QDialog)
from PyQt6.QtCore import pyqtSignal, Qt

from tools.edge_companion.protocol import EdgeProtocolError, parse_candidate_json
from tools.edge_companion.ui import EdgeCandidateDialog
from tools.theme_utils import apply_shadow
from tools.video_crawler.adapters.ytdlp import YtDlpAdapter
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.logging_utils import redact_for_display
from tools.video_crawler.models import SnifferOptions
from tools.video_crawler.sniffer import PageSniffer
from tools.video_crawler.spider import UniversalVideoSpider


# ==========================================
# 核心爬虫业务逻辑层 (严格对齐最新 fMP4+防广告 逻辑)
# ==========================================
class VideoDownloaderTool(QWidget):
    """管理视频下载表单、任务队列、后台执行和批次结果展示。"""

    # 工作线程不直接操作 Qt 控件；所有界面更新都通过信号切回主线程。
    log_signal = pyqtSignal(str)
    queue_pop_signal = pyqtSignal()
    batch_finished_signal = pyqtSignal(object)

    def __init__(
        self,
        start_worker=True,
        spider_factory=UniversalVideoSpider,
        clipboard_getter=None,
        edge_dialog_factory=None,
        now=None,
    ):
        """初始化下载界面并按需启动队列工作线程。

        Args:
            start_worker: 是否立即启动永久消费队列的守护线程。测试可关闭它。
            spider_factory: 创建爬虫实例的工厂，允许测试注入轻量替身。
            clipboard_getter: 按需读取剪贴板文本的可调用对象。
            edge_dialog_factory: 创建 Edge 候选确认对话框的可调用对象。
            now: 返回当前带时区时间的可调用对象。
        """
        super().__init__()
        self.spider_factory = spider_factory
        self._clipboard_getter = clipboard_getter or self._qt_clipboard_text
        self._edge_dialog_factory = edge_dialog_factory or (
            lambda candidate, parent: EdgeCandidateDialog(candidate, parent)
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pending_edge_candidate = None
        self._edge_waiting = False
        self.task_queue = queue.Queue()
        self._batch_results = []
        self.is_high_speed_mode = False  # 默认使用低速稳定模式

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # QScrollArea 默认会绘制不透明 viewport；这里同时关闭三层背景，
        # 否则滚动区域会盖住主窗口的壁纸。
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("videoDownloaderScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.scroll_area.setAutoFillBackground(False)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self.scroll_area.viewport().setAutoFillBackground(False)
        self.scroll_area.setStyleSheet(
            "#videoDownloaderScrollArea, "
            "#videoDownloaderScrollArea > QWidget, "
            "#videoDownloaderScrollContent { "
            "background: transparent; border: 0px; }"
        )

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("videoDownloaderScrollContent")
        self.scroll_content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.scroll_content.setAutoFillBackground(False)
        self.scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll_area)

        scroll_layout = QVBoxLayout(self.scroll_content)
        scroll_layout.setContentsMargins(18, 18, 18, 18)
        scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setMinimumWidth(650)
        self.container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        apply_shadow(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        scroll_layout.addWidget(self.container)

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

        options_layout = QVBoxLayout()
        options_layout.setSpacing(8)

        self.download_options_layout = QHBoxLayout()
        self.download_options_layout.setSpacing(12)
        self.download_options_layout.addWidget(QLabel("下载参数:"))
        self.download_options_layout.addWidget(QLabel("切片并发:"))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 100)
        self.concurrency_spin.setValue(5)
        self.concurrency_spin.setSuffix(" 个")
        self.concurrency_spin.setFixedWidth(120)
        self.download_options_layout.addWidget(self.concurrency_spin)
        self.resume_chk = QCheckBox("复用未完成切片")
        self.resume_chk.setChecked(True)
        self.download_options_layout.addWidget(self.resume_chk)
        self.ytdlp_chk = QCheckBox("公开平台失败时尝试 yt-dlp")
        self.ytdlp_chk.setChecked(False)
        self.download_options_layout.addWidget(self.ytdlp_chk)
        self.live_seconds_spin = QSpinBox()
        self.live_seconds_spin.setRange(30, 7200)
        self.live_seconds_spin.setValue(300)
        self.live_seconds_spin.setSuffix(" 秒直播录制")
        self.live_seconds_spin.setFixedWidth(150)
        self.download_options_layout.addWidget(self.live_seconds_spin)
        self.download_options_layout.addStretch()
        options_layout.addLayout(self.download_options_layout)

        self.sniff_options_layout = QHBoxLayout()
        self.sniff_options_layout.setSpacing(12)
        self.sniff_options_layout.addWidget(QLabel("嗅探选项:"))
        self.visible_sniff_chk = QCheckBox("可视化嗅探")
        self.visible_sniff_chk.setChecked(False)
        self.sniff_options_layout.addWidget(self.visible_sniff_chk)
        self.persistent_profile_chk = QCheckBox("复用浏览器会话")
        self.persistent_profile_chk.setChecked(False)
        self.sniff_options_layout.addWidget(self.persistent_profile_chk)
        self.system_chrome_chk = QCheckBox("系统 Chrome（实验）")
        self.system_chrome_chk.setChecked(False)
        self.system_chrome_chk.setToolTip(
            "使用本机 Google Chrome；仍由 Playwright 控制，"
            "不会隐藏自动化标记，也不保证通过网站验证。"
        )
        self.sniff_options_layout.addWidget(self.system_chrome_chk)
        self.sniff_options_layout.addWidget(QLabel("等待:"))
        self.sniff_wait_spin = QSpinBox()
        self.sniff_wait_spin.setRange(5, 180)
        self.sniff_wait_spin.setValue(25)
        self.sniff_wait_spin.setSuffix(" 秒等待")
        self.sniff_wait_spin.setFixedWidth(120)
        self.sniff_options_layout.addWidget(self.sniff_wait_spin)
        self.sniff_options_layout.addStretch()
        options_layout.addLayout(self.sniff_options_layout)

        self.edge_controls_layout = QHBoxLayout()
        self.edge_controls_layout.setSpacing(12)
        self.edge_controls_layout.addWidget(QLabel("Edge 捕获:"))
        self.edge_status_label = QLabel("未连接")
        self.edge_controls_layout.addWidget(self.edge_status_label)
        self.edge_wait_btn = QPushButton("等待 Edge 捕获")
        self.edge_wait_btn.clicked.connect(self.toggle_edge_waiting)
        self.edge_controls_layout.addWidget(self.edge_wait_btn)
        self.edge_paste_btn = QPushButton("粘贴 Edge 候选")
        self.edge_paste_btn.clicked.connect(self.paste_edge_candidate)
        self.edge_controls_layout.addWidget(self.edge_paste_btn)
        self.edge_controls_layout.addStretch()
        options_layout.addLayout(self.edge_controls_layout)
        layout.addLayout(options_layout)

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
        """打开目录选择器并把用户选择写回输入框。"""
        folder = QFileDialog.getExistingDirectory(self, "选择保存位置")
        if folder: self.path_entry.setText(folder)

    @staticmethod
    def _qt_clipboard_text():
        """Read current Qt clipboard text only when explicitly requested."""
        return QApplication.clipboard().text()

    def _set_playwright_controls_enabled(self, enabled):
        """Enable or disable all controls that configure Playwright sniffing."""
        for control in (
            self.visible_sniff_chk,
            self.persistent_profile_chk,
            self.system_chrome_chk,
            self.sniff_wait_spin,
        ):
            control.setEnabled(enabled)

    def clear_edge_candidate(self):
        """Clear exclusive Edge input state and restore Playwright controls."""
        self._pending_edge_candidate = None
        self._set_playwright_controls_enabled(True)
        if self._edge_waiting:
            self.edge_status_label.setText("等待捕获")
        else:
            self.edge_status_label.setText("未连接")

    def toggle_edge_waiting(self):
        """Toggle only the visible placeholder state for future Edge receiving."""
        self._edge_waiting = not self._edge_waiting
        if self._edge_waiting:
            self.edge_status_label.setText("等待捕获")
            self.edge_wait_btn.setText("停止等待")
        else:
            self.edge_status_label.setText("未连接")
            self.edge_wait_btn.setText("等待 Edge 捕获")

    def paste_edge_candidate(self):
        """Validate, confirm, and activate a candidate from clipboard JSON."""
        try:
            candidate = parse_candidate_json(self._clipboard_getter())
        except EdgeProtocolError:
            QMessageBox.warning(
                self,
                "Edge 候选无效",
                "Edge 候选无效，请复制完整的 V1 JSON 消息。",
            )
            return

        if candidate.is_expired(self._now()):
            QMessageBox.warning(
                self,
                "Edge 候选已过期",
                "Edge 候选已过期，请在 Edge 中重新捕获后再粘贴。",
            )
            return

        dialog = self._edge_dialog_factory(candidate, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._pending_edge_candidate = candidate
        self.url_entry.setText(candidate.media_url)
        self._edge_waiting = False
        self.edge_wait_btn.setText("等待 Edge 捕获")
        self.edge_status_label.setText("已收到候选")
        self._set_playwright_controls_enabled(False)

    def append_log(self, message):
        """把经过处理的消息追加到日志控件并滚动到底部。"""
        self.log_text.append(redact_for_display(message))
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def pop_queue_ui(self):
        """从界面列表移除已经被工作线程取走的队首任务。"""
        if self.queue_listbox.count() > 0:
            self.queue_listbox.takeItem(0)

    def toggle_mode(self):
        """切换高速/稳定模式，并同步推荐并发数和按钮说明。"""
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
        """校验 URL 后启动独立线程执行媒体诊断。"""
        url = self.url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请先输入目标网址。")
            return
        threading.Thread(target=self._diagnose_task, args=(url,), daemon=True).start()

    def _diagnose_task(self, url):
        """使用当前嗅探设置生成诊断报告并发送到 UI 日志。"""
        try:
            from tools.video_crawler.diagnostics import VideoDiagnosticService
            from tools.video_crawler.reporting import format_diagnostic_report

            sniffer_options = self._build_sniffer_options()
            if not sniffer_options.headless:
                self.log_signal.emit("[*] 诊断将打开可视化浏览器窗口。")
            if sniffer_options.use_system_chrome:
                self.log_signal.emit("[*] 诊断将使用本机 Google Chrome（实验）。")
            if sniffer_options.use_persistent_profile:
                self.log_signal.emit(
                    f"[*] 诊断将复用 {sniffer_options.active_profile_dir} 会话。"
                )
            service = VideoDiagnosticService(
                sniffer=PageSniffer(
                    headers={},
                    log_callback=self.log_signal.emit,
                    options=sniffer_options,
                )
            )
            report = service.analyze(url)
            self.log_signal.emit("\n" + format_diagnostic_report(report))
        except Exception as exc:
            self.log_signal.emit(f"\n[X] 诊断失败: {exc}")

    def add_to_queue(self):
        """校验表单并把当前所有下载设置快照为独立任务。"""
        url, name, save_dir = self.url_entry.text().strip(), self.name_entry.text().strip(), self.path_entry.text().strip()
        if not all([url, name, save_dir]):
            QMessageBox.warning(self, "警告", "请填写完整信息！")
            return

        # 任务必须保存控件值的快照。若工作线程稍后再读取 UI，用户修改
        # 下一项任务时会悄悄改变已排队任务的并发数或嗅探模式。
        task = {
            "url": url,
            "name": name,
            "save_dir": save_dir,
            "is_high_speed": self.is_high_speed_mode,
            "segment_concurrency": self.concurrency_spin.value(),
            "resume_enabled": self.resume_chk.isChecked(),
            "use_ytdlp_fallback": self.ytdlp_chk.isChecked(),
            "live_record_seconds": self.live_seconds_spin.value(),
            "sniffer_headless": not self.visible_sniff_chk.isChecked(),
            "sniffer_use_persistent_profile": self.persistent_profile_chk.isChecked(),
            "sniffer_use_system_chrome": self.system_chrome_chk.isChecked(),
            "sniffer_manual_wait_seconds": self.sniff_wait_spin.value(),
        }
        self._enqueue_task(task)
        self.url_entry.clear()

    def _enqueue_task(self, task):
        """复制任务、放入线程安全队列并更新队列列表。"""
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

    def _build_sniffer_options(self):
        """把当前嗅探控件值转换为不可变配置对象。"""
        return SnifferOptions(
            headless=not self.visible_sniff_chk.isChecked(),
            use_persistent_profile=self.persistent_profile_chk.isChecked(),
            use_system_chrome=self.system_chrome_chk.isChecked(),
            manual_wait_seconds=self.sniff_wait_spin.value(),
        )

    def _execute_task(self, task):
        """执行单个队列任务，并把异常统一转换为结构化结果字典。

        Args:
            task: 入队时冻结的下载参数字典。

        Returns:
            包含任务、成功状态、输出路径、引擎和错误元数据的字典。
            该方法吞掉下载异常，保证永久工作线程不会因单项失败退出。
        """
        try:
            # 从任务快照重建配置，而不是读取可能已被用户修改的控件。
            sniffer_options = SnifferOptions(
                headless=task.get("sniffer_headless", True),
                use_persistent_profile=task.get(
                    "sniffer_use_persistent_profile",
                    False,
                ),
                use_system_chrome=task.get(
                    "sniffer_use_system_chrome",
                    False,
                ),
                manual_wait_seconds=task.get("sniffer_manual_wait_seconds", 25),
            )
            spider = self.spider_factory(
                output_dir=task["save_dir"],
                temp_dir="./temp",
                log_callback=self.log_signal.emit,
                is_high_speed=task["is_high_speed"],
                segment_concurrency=task["segment_concurrency"],
                resume_enabled=task.get("resume_enabled", True),
                live_record_seconds=task.get("live_record_seconds", 300),
                sniffer_options=sniffer_options,
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
            # 仅真正未分类的异常落入 UNKNOWN；已知下载失败应在核心层
            # 提前包装为 VideoDownloadError，以便 UI 给出准确建议。
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
        """判断内置失败是否满足用户启用的 yt-dlp 后备条件。"""
        return bool(task.get("use_ytdlp_fallback", False)) and error.code in {
            VideoErrorCode.NO_MEDIA_FOUND,
            VideoErrorCode.UNSUPPORTED_DASH,
        }

    def _execute_ytdlp_fallback(self, task):
        """调用 yt-dlp 后备适配器并返回与内置流程一致的结果。"""
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
        """记录任务结果、维护队列计数并在批次结束时发射信号。"""
        self._batch_results.append(result)
        self.task_queue.task_done()
        if self.task_queue.unfinished_tasks == 0:
            # 发射副本后立即清空内部列表，使下一批任务从干净状态开始。
            completed_batch = list(self._batch_results)
            self._batch_results.clear()
            self.batch_finished_signal.emit(completed_batch)

    def retry_failed_tasks(self, results):
        """按原始配置重新入队所有标记为可重试的失败任务。"""
        for result in results:
            if not result["success"] and result.get("retryable", True):
                self._enqueue_task(result["task"])

    @staticmethod
    def has_retryable_failures(results):
        """判断批次结果中是否至少包含一个可重试失败。"""
        return any(
            not result["success"] and result.get("retryable", True)
            for result in results
        )

    @staticmethod
    def format_batch_results(results):
        """生成批次成功、失败、错误统计和重试建议文本。

        结果文本只依赖结构化字段，不解析异常字符串；这样核心层调整错误
        文案时不会破坏 UI 的重试判断和分组统计。
        """
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
            if error_counts.get(VideoErrorCode.HTTP_FORBIDDEN.value):
                detail_lines.append(
                    "  建议: 页面访问受限。请确认普通浏览器可播放；"
                    "若可播放，尝试启用“可视化嗅探”和“复用浏览器会话”，"
                    "在弹出的浏览器中完成允许的人工操作后再点击播放。"
                )
            if error_counts.get(VideoErrorCode.NETWORK_TIMEOUT.value):
                detail_lines.append(
                    "  建议: 网络超时。请确认候选 M3U8 在普通浏览器可访问；"
                    "若网页可播放，尝试启用“可视化嗅探”和“复用浏览器会话”，"
                    "延长等待时间后再点击播放，也可稍后重试或检查网络。"
                )
            if not VideoDownloaderTool.has_retryable_failures(results):
                detail_lines.append(
                    "  建议: 这些失败不建议直接重试，请先检查链接、权限或资源类型。"
                )
        return summary, "\n".join(detail_lines)

    @staticmethod
    def _format_success_line(result):
        """格式化单个成功任务，并标注实际使用的下载引擎。"""
        engine_label = " [yt-dlp 外部引擎]" if result.get("engine") == "yt-dlp" else ""
        return f"  ✓ {result['task']['name']}{engine_label}"

    def show_batch_results(self, results):
        """显示批次汇总对话框，并按结果决定是否提供重试按钮。"""
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
        """持续消费线程安全队列，在后台顺序执行下载任务。

        该守护线程设计为与工具生命周期一致，因此没有正常退出分支。
        所有 Qt 更新均通过信号完成，避免跨线程直接访问控件。
        """
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
