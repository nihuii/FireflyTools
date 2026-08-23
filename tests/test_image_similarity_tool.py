"""验证图片相似度标签页的默认值、选择和安全确认。"""

from dataclasses import replace
import os
from pathlib import Path
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QThread, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QHeaderView, QMessageBox

from tools.image_similarity.grouping import build_similarity_groups
from tools.image_similarity.models import (
    GroupType,
    ImageFingerprint,
    ImageRecord,
    RecycleItemResult,
    RecycleStatus,
    ScanResult,
    SimilarityGroup,
    SimilarityPreset,
)
from tools.image_similarity_tool import (
    ImageSimilarityTool,
    ThumbnailCache,
    TrashConfirmationDialog,
)


class ImageSimilarityToolTests(unittest.TestCase):
    """在 offscreen Qt 环境中验证第 5 个工具页。"""

    @classmethod
    def setUpClass(cls):
        """为本测试类复用一个 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """创建页面和一次性图片目录。"""
        test_temp_root = Path(__file__).resolve().parent / ".tmp"
        test_temp_root.mkdir(exist_ok=True)
        self.root = test_temp_root / "image-similarity-tool"
        self.root.mkdir(exist_ok=True)
        self.created_paths = []
        self.tool = ImageSimilarityTool()

    def tearDown(self):
        """关闭页面并清理合成图片。"""
        idle = QSignalSpy(self.tool.thumbnail_cache.tasks_idle)
        self.tool.close()
        if self.tool.thumbnail_cache.has_active_tasks():
            self.assertTrue(idle.wait(3000))
        self.app.processEvents()
        for path in self.created_paths:
            path.unlink(missing_ok=True)

    def _fingerprint(self, name, order, *, suggested=False):
        """创建可用于 UI 模型和缩略图的合成图片指纹。"""
        path = self.root / name
        Image.new("RGB", (32 + order, 24), (50 * order, 80, 120)).save(path)
        self.created_paths.append(path)
        path_stat = path.stat()
        record = ImageRecord(
            path=path,
            canonical_path=path.resolve(),
            size_bytes=path_stat.st_size,
            mtime_ns=path_stat.st_mtime_ns,
            image_format="PNG",
            width=32 + order,
            height=24,
            order=order,
        )
        return ImageFingerprint(
            record=record,
            phash=order,
            dhash=order,
            grayscale=bytes([80 + order]) * 256,
            sha256="same" if suggested else None,
        )

    def _scan_result(self):
        """创建一个包含两张图片的视觉相似结果。"""
        keep = self._fingerprint("keep.png", 0, suggested=True)
        other = self._fingerprint("other.png", 1)
        group = SimilarityGroup(
            group_id="group-00001",
            group_type=GroupType.VISUAL,
            representative=keep,
            members=(keep, other),
            suggested_keep=keep,
        )
        return ScanResult(
            root=self.root.resolve(),
            preset=SimilarityPreset.STRICT,
            groups=(group,),
            fingerprints=(keep, other),
            failures=(),
        )

    def test_defaults_are_recursive_strict_and_unselected(self):
        """第一版默认递归、严格模式且没有任何删除选择。"""
        self.assertTrue(self.tool.recursive_checkbox.isChecked())
        self.assertTrue(self.tool.strict_radio.isChecked())
        self.assertEqual(set(), self.tool.selected_paths)
        self.assertFalse(self.tool.trash_button.isEnabled())

    def test_scan_cannot_start_while_recycle_batch_is_running(self):
        """回收站批次未完成时不能并发启动新扫描并替换当前结果。"""
        self.tool.recycle_thread = object()

        try:
            with mock.patch.object(QMessageBox, "warning") as warning:
                self.tool.start_scan()

            warning.assert_not_called()
            self.assertIsNone(self.tool.scan_thread)
        finally:
            self.tool.recycle_thread = None

    def test_trash_button_stays_disabled_while_regroup_is_running(self):
        """回收线程先结束且仍有失败选择时不得在重分组期间再次确认。"""
        result = self._scan_result()
        self.tool.show_scan_result(result)
        self.tool.selected_paths.add(result.fingerprints[1].record.path)
        self.tool.recycle_thread = object()
        self.tool.regroup_thread = object()

        try:
            self.tool._on_recycle_thread_finished()

            self.assertFalse(self.tool.trash_button.isEnabled())
        finally:
            self.tool.regroup_thread = None

    def test_root_scroll_area_viewport_and_content_are_transparent(self):
        """根页面和滚动区三层不使用遮挡壁纸的不透明背景。"""
        self.assertTrue(
            self.tool.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        )
        self.assertTrue(
            self.tool.scroll_area.viewport().testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground
            )
        )
        self.assertTrue(
            self.tool.scroll_content.testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground
            )
        )
        self.assertFalse(self.tool.scroll_area.viewport().autoFillBackground())

    def test_showing_results_keeps_all_delete_checkboxes_unselected(self):
        """扫描结果进入模型后仍不能自动勾选删除项。"""
        self.tool.show_scan_result(self._scan_result())

        self.assertEqual(set(), self.tool.selected_paths)
        self.assertFalse(self.tool.trash_button.isEnabled())
        self.assertEqual(Qt.CheckState.Unchecked, self.tool.image_model.check_state(0))

    def test_open_image_button_opens_current_image_without_slot_error(self):
        """按钮携带的 checked 布尔值不能被误当作表格索引。"""
        result = self._scan_result()
        self.tool.show_scan_result(result)
        current = self.tool.image_model.index(0, 0)
        self.tool.image_view.setCurrentIndex(current)
        unhandled = []

        with (
            mock.patch(
                "sys.excepthook",
                side_effect=lambda kind, value, tb: unhandled.append(value),
            ),
            mock.patch(
                "tools.image_similarity_tool.QDesktopServices.openUrl",
                return_value=True,
            ) as open_url,
        ):
            self.tool.open_image_button.click()

        self.assertEqual([], unhandled)
        open_url.assert_called_once()

    def test_select_group_others_keeps_suggested_item_unselected(self):
        """用户主动点击组内快捷选择时仍保留系统建议项。"""
        result = self._scan_result()
        self.tool.show_scan_result(result)
        self.tool.selected_paths.add(
            result.groups[0].suggested_keep.record.path
        )

        self.tool.select_group_others()

        self.assertNotIn(
            result.groups[0].suggested_keep.record.path,
            self.tool.selected_paths,
        )
        self.assertIn(result.groups[0].members[1].record.path, self.tool.selected_paths)
        self.assertTrue(self.tool.trash_button.isEnabled())

    def test_thumbnail_capacity_release_does_not_refresh_entire_models(self):
        """单个任务释放容量时不能要求两张表重新读取全部缩略图。"""
        result = self._scan_result()
        self.tool.group_model.set_groups(result.groups)
        self.tool.image_model.set_group(result.groups[0])
        group_changes = QSignalSpy(self.tool.group_model.dataChanged)
        image_changes = QSignalSpy(self.tool.image_model.dataChanged)

        self.tool.thumbnail_cache.capacity_available.emit()
        self.app.processEvents()

        self.assertEqual(0, len(group_changes))
        self.assertEqual(0, len(image_changes))

    def test_thumbnail_ready_uses_row_indexes_without_scanning_models(self):
        """单图完成通知必须通过路径索引定位，不能重新线性扫描模型。"""
        class CountingSequence:
            """记录被整体迭代次数，同时保留模型所需的序列接口。"""

            def __init__(self, items):
                self.items = tuple(items)
                self.iterations = 0

            def __len__(self):
                return len(self.items)

            def __getitem__(self, index):
                return self.items[index]

            def __iter__(self):
                self.iterations += 1
                return iter(self.items)

        result = self._scan_result()
        group = result.groups[0]
        self.tool.group_model.set_groups(result.groups)
        self.tool.image_model.set_group(group)
        guarded_groups = CountingSequence(result.groups)
        guarded_members = CountingSequence(group.members)
        self.tool.group_model._groups = guarded_groups
        self.tool.image_model.group = replace(group, members=guarded_members)

        self.tool.group_model._on_thumbnail_ready(
            group.representative.record.path
        )
        self.tool.image_model._on_thumbnail_ready(group.members[0].record.path)

        self.assertEqual(0, guarded_groups.iterations)
        self.assertEqual(0, guarded_members.iterations)

    def test_thumbnail_column_uses_fixed_width_without_global_content_scan(self):
        """明细表只让必要列参与布局，缩略图列不能触发全结果内容测量。"""
        header = self.tool.image_view.horizontalHeader()

        self.assertEqual(
            QHeaderView.ResizeMode.ResizeToContents,
            header.sectionResizeMode(0),
        )
        self.assertEqual(
            QHeaderView.ResizeMode.Fixed,
            header.sectionResizeMode(1),
        )
        self.assertEqual(
            QHeaderView.ResizeMode.Stretch,
            header.sectionResizeMode(3),
        )

    def test_confirmation_lists_count_size_paths_and_recycle_bin_warning(self):
        """二次确认完整展示数量、总大小、路径和回收站说明。"""
        paths = (self.root / "one.png", self.root / "two.png")
        dialog = TrashConfirmationDialog(paths, total_size_bytes=4096)

        self.assertIn("2", dialog.summary_label.text())
        self.assertIn("4.0 KB", dialog.summary_label.text())
        self.assertIn(str(paths[0]), dialog.paths_view.toPlainText())
        self.assertIn(str(paths[1]), dialog.paths_view.toPlainText())
        self.assertIn("不会永久删除", dialog.notice_label.text())
        self.assertIn("清空回收站后", dialog.notice_label.text())
        self.assertFalse(dialog.confirm_button.isDefault())
        self.assertFalse(dialog.confirm_button.autoDefault())
        self.assertTrue(dialog.cancel_button.isDefault())
        self.assertEqual("trashConfirmationDialog", dialog.objectName())
        dialog.close()

    def test_thumbnail_cache_never_exceeds_capacity(self):
        """缩略图 LRU 不随扫描图片数量无界增长。"""
        cache = ThumbnailCache(capacity=2, asynchronous=False)
        paths = []
        for index in range(3):
            path = self.root / f"thumb-{index}.png"
            Image.new("RGB", (20, 20), (index * 50, 20, 30)).save(path)
            self.created_paths.append(path)
            paths.append(path)

        for path in paths:
            self.assertFalse(cache.get(path).isNull())

        self.assertEqual(2, len(cache))
        self.assertNotIn(paths[0], cache.paths())

    def test_thumbnail_decode_runs_outside_gui_thread(self):
        """生产模式的缩略图解码应异步执行，主线程只接收并生成像素图。"""
        path = self.root / "async-thumb.png"
        path.write_bytes(b"decoder input is injected")
        self.created_paths.append(path)
        decoder_threads = []

        def decoder(_path, _size):
            """记录解码所在线程并返回线程安全的 QImage。"""
            decoder_threads.append(QThread.currentThread())
            image = QImage(20, 20, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.red)
            return image

        cache = ThumbnailCache(capacity=2, decoder=decoder)
        ready = QSignalSpy(cache.thumbnail_ready)

        initial = cache.get(path)

        self.assertTrue(initial.isNull())
        self.assertTrue(ready.wait(3000))
        self.assertTrue(decoder_threads)
        self.assertIsNot(decoder_threads[0], self.app.thread())
        self.assertFalse(cache.get(path).isNull())
        cache.clear()

    def test_thumbnail_queue_is_bounded_and_clear_cancels_queued_tasks(self):
        """缩略图待处理总量有上限，换批时应撤销尚未运行的旧任务。"""
        release = threading.Event()
        started = threading.Event()

        def blocking_decoder(_path, _size):
            """阻塞少量运行任务，让测试能够观察线程池队列。"""
            started.set()
            release.wait(3)
            image = QImage(8, 8, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.blue)
            return image

        cache = ThumbnailCache(
            capacity=4,
            max_workers=2,
            max_pending=6,
            decoder=blocking_decoder,
        )
        idle = QSignalSpy(cache.tasks_idle)

        for index in range(20):
            cache.get(self.root / f"bounded-{index}.png")

        self.assertTrue(started.wait(1))
        self.assertLessEqual(cache.pending_count(), 6)

        cache.clear()

        self.assertLess(cache.pending_count(), 6)
        release.set()
        self.assertTrue(idle.wait(3000))
        self.assertEqual(0, cache.pending_count())

    def test_thumbnail_request_is_retried_after_queue_capacity_returns(self):
        """队列满时的可见缩略图请求必须在容量释放后自动补排。"""
        release = threading.Event()
        started = threading.Event()

        def blocking_decoder(_path, _size):
            """阻塞首项以制造待处理队列已满状态。"""
            started.set()
            release.wait(3)
            return QImage(8, 8, QImage.Format.Format_RGB32)

        cache = ThumbnailCache(
            capacity=4,
            max_workers=1,
            max_pending=1,
            decoder=blocking_decoder,
        )
        idle = QSignalSpy(cache.tasks_idle)
        first = self.root / "queue-first.png"
        second = self.root / "queue-second.png"

        cache.get(first)
        self.assertTrue(started.wait(1))
        cache.get(second)
        release.set()

        self.assertTrue(idle.wait(3000))
        self.app.processEvents()
        self.assertIn(second, cache.paths())
        cache.shutdown()

    def test_thumbnail_prefetch_requeues_same_path_for_new_generation(self):
        """旧批次同路径仍在退出时，新批次预热必须绑定到当前代次。"""
        release = threading.Event()
        started = threading.Event()
        decode_count = 0

        def generation_decoder(_path, _size):
            """只阻塞旧代次解码，让新代次请求与其路径重叠。"""
            nonlocal decode_count
            decode_count += 1
            if decode_count == 1:
                started.set()
                release.wait(3)
            return QImage(8, 8, QImage.Format.Format_RGB32)

        cache = ThumbnailCache(
            capacity=4,
            max_workers=1,
            max_pending=1,
            decoder=generation_decoder,
        )
        idle = QSignalSpy(cache.tasks_idle)
        path = self.root / "same-path.png"

        self.assertEqual((path,), cache.prefetch((path,)))
        self.assertTrue(started.wait(1))
        cache.clear()
        cache.resume()
        self.assertEqual((path,), cache.prefetch((path,)))
        release.set()

        self.assertTrue(idle.wait(3000))
        self.app.processEvents()
        self.assertEqual(2, decode_count)
        self.assertIn(path, cache.paths())
        cache.shutdown()

    def test_thumbnail_phase_tracks_real_background_prefetch(self):
        """缩略图阶段应覆盖实际后台预热，而不是扫描器中的空过渡。"""
        release = threading.Event()
        started = threading.Event()

        def blocking_decoder(path, size):
            """阻塞真实图片解码前的测试检查点。"""
            started.set()
            release.wait(3)
            reader = QImage(str(path))
            return reader.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
            )

        self.tool.thumbnail_cache.decoder = blocking_decoder
        idle = QSignalSpy(self.tool.thumbnail_cache.tasks_idle)

        self.tool.show_scan_result(self._scan_result())

        self.assertTrue(started.wait(1))
        self.assertIn("缩略图", self.tool.phase_label.text())
        self.assertTrue(self.tool._thumbnail_prewarm_paths)

        release.set()
        self.assertTrue(idle.wait(3000))
        self.app.processEvents()

        self.assertEqual("扫描完成", self.tool.phase_label.text())
        self.assertEqual(set(), self.tool._thumbnail_prewarm_paths)

    def test_removing_star_representative_rebuilds_remaining_groups(self):
        """删除星形分组代表图后，互不相似的剩余图片不能继续留在同组。"""
        representative = self._fingerprint("star-a.png", 0)
        left = self._fingerprint("star-b.png", 1)
        right = self._fingerprint("star-c.png", 2)
        common_gray = bytes([90]) * 256
        representative = replace(
            representative,
            record=replace(representative.record, width=32, height=24),
            phash=0,
            dhash=0,
            grayscale=common_gray,
        )
        left = replace(
            left,
            record=replace(left.record, width=32, height=24),
            phash=0b1111,
            dhash=0b1111,
            grayscale=common_gray,
        )
        right = replace(
            right,
            record=replace(right.record, width=32, height=24),
            phash=0b11110000,
            dhash=0b11110000,
            grayscale=common_gray,
        )
        group = SimilarityGroup(
            group_id="group-00001",
            group_type=GroupType.VISUAL,
            representative=representative,
            members=(representative, left, right),
            suggested_keep=representative,
        )
        result = ScanResult(
            root=self.root.resolve(),
            preset=SimilarityPreset.STRICT,
            groups=(group,),
            fingerprints=(representative, left, right),
            failures=(),
        )

        updated = self.tool._result_without_paths(
            result,
            {representative.record.path},
        )

        self.assertEqual((), updated.groups)
        self.assertEqual((left, right), updated.fingerprints)

    def test_repeated_sha_subgroup_is_labeled_as_exact_duplicate(self):
        """视觉组内任意重复 SHA 子组都应标注完全重复，而非只比较代表图。"""
        representative = replace(
            self._fingerprint("relation-a.png", 0),
            sha256="representative",
        )
        duplicate_one = replace(
            self._fingerprint("relation-b.png", 1),
            sha256="duplicate-subgroup",
        )
        duplicate_two = replace(
            self._fingerprint("relation-c.png", 2),
            sha256="duplicate-subgroup",
        )
        group = SimilarityGroup(
            group_id="group-00001",
            group_type=GroupType.VISUAL,
            representative=representative,
            members=(representative, duplicate_one, duplicate_two),
            suggested_keep=representative,
        )
        self.tool.image_model.set_group(group)

        labels = [
            self.tool.image_model.data(
                self.tool.image_model.index(row, 7),
                Qt.ItemDataRole.DisplayRole,
            )
            for row in (1, 2)
        ]

        self.assertEqual(["完全重复", "完全重复"], labels)

    def test_recycle_reasons_remain_visible_and_can_be_copied(self):
        """回收失败或跳过原因应保留在页面上，并能一键复制完整明细。"""
        result = self._scan_result()
        self.tool.show_scan_result(result)
        failed_path = result.fingerprints[1].record.path
        recycle_result = RecycleItemResult(
            path=failed_path,
            status=RecycleStatus.SKIPPED_CHANGED,
            message="文件已变化，请重新扫描",
        )

        with mock.patch.object(QMessageBox, "information"):
            self.tool._on_recycle_completed((recycle_result,))

        details = self.tool.recycle_details_view.toPlainText()
        self.assertTrue(self.tool.recycle_details_view.isVisibleTo(self.tool))
        self.assertIn(str(failed_path), details)
        self.assertIn("文件已变化，请重新扫描", details)
        self.assertTrue(self.tool.copy_recycle_summary_button.isEnabled())

        self.tool.copy_recycle_summary()

        self.assertEqual(details, QApplication.clipboard().text())

    def test_recycle_service_initialization_failure_is_visible_and_safe(self):
        """扫描根目录失效时应显示批次错误，且不能启动回收线程。"""
        result = self._scan_result()
        self.tool.show_scan_result(result)
        selected = (result.fingerprints[1].record.path,)

        with (
            mock.patch(
                "tools.image_similarity_tool.RecycleBinService",
                side_effect=RuntimeError("扫描根目录已失效"),
            ),
            mock.patch.object(QMessageBox, "critical") as critical,
        ):
            self.tool._start_recycle(selected)

        self.assertIsNone(self.tool.recycle_thread)
        critical.assert_called_once()
        self.assertIn("扫描根目录已失效", critical.call_args.args[2])

    def test_successful_recycle_regroups_outside_gui_thread(self):
        """成功移除图片后的关系重建必须在受管理后台线程中执行。"""
        result = self._scan_result()
        self.tool.show_scan_result(result)
        moved_path = result.fingerprints[1].record.path
        recycle_result = RecycleItemResult(
            path=moved_path,
            status=RecycleStatus.MOVED_TO_TRASH,
            message="已移入系统回收站",
        )
        regroup_threads = []
        original_grouping = build_similarity_groups
        idle = QSignalSpy(self.tool.workers_idle)

        def recording_grouping(*args, **kwargs):
            """记录重分组运行线程后调用真实纯算法。"""
            regroup_threads.append(QThread.currentThread())
            return original_grouping(*args, **kwargs)

        with (
            mock.patch(
                "tools.image_similarity_tool.build_similarity_groups",
                side_effect=recording_grouping,
            ),
            mock.patch.object(QMessageBox, "information"),
        ):
            self.tool._on_recycle_completed((recycle_result,))
            self.assertIsNotNone(self.tool.regroup_thread)
            self.assertTrue(idle.wait(3000))
            self.app.processEvents()

        self.assertTrue(regroup_threads)
        self.assertIsNot(regroup_threads[0], self.app.thread())
        self.assertEqual((), self.tool.current_result.groups)
        self.assertEqual(1, len(self.tool.current_result.fingerprints))

    def test_successful_recycle_commits_safe_state_before_regroup_starts(self):
        """后台重分组即使随后失败，已回收路径也必须立即退出正式状态与选择。"""
        result = self._scan_result()
        self.tool.show_scan_result(result)
        moved_path = result.fingerprints[1].record.path
        self.tool.selected_paths.add(moved_path)
        recycle_result = RecycleItemResult(
            path=moved_path,
            status=RecycleStatus.MOVED_TO_TRASH,
            message="已移入系统回收站",
        )

        with (
            mock.patch.object(self.tool, "_start_regroup") as start_regroup,
            mock.patch.object(QMessageBox, "information"),
        ):
            self.tool._on_recycle_completed((recycle_result,))

        remaining_paths = {
            fingerprint.record.path
            for fingerprint in self.tool.current_result.fingerprints
        }
        self.assertNotIn(moved_path, remaining_paths)
        self.assertNotIn(moved_path, self.tool.selected_paths)
        self.assertEqual(0, self.tool.group_model.rowCount())
        self.assertEqual(0, self.tool.image_model.rowCount())
        start_regroup.assert_called_once_with(self.tool.current_result, set())


if __name__ == "__main__":
    unittest.main()
