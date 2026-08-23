"""验证图片目录扫描、并发顺序、失败隔离和取消语义。"""

from pathlib import Path
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image

from tools.image_similarity import scanner as scanner_module
from tools.image_similarity.grouping import GroupingCancelled
from tools.image_similarity.models import (
    GroupType,
    ScanErrorCode,
    ScanPhase,
    SimilarityPreset,
)
from tools.image_similarity.scanner import ImageScanner


class ImageSimilarityScannerTests(unittest.TestCase):
    """只在一次性目录中创建和读取合成图片。"""

    def setUp(self):
        """创建独立扫描根目录。"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        """清理一次性扫描目录。"""
        self.temp_dir.cleanup()

    def _save_image(self, relative_path, color, size=(48, 32)):
        """保存一张可正常解码的测试图片并返回路径。"""
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, color).save(path)
        return path

    def test_recursive_switch_controls_nested_images(self):
        """关闭递归时只读取根目录图片，开启时包含子目录。"""
        self._save_image("root.png", "red")
        self._save_image("nested/child.png", "blue")
        scanner = ImageScanner(max_workers=2)

        flat = scanner.scan(self.root, recursive=False)
        recursive = scanner.scan(self.root, recursive=True)

        self.assertEqual(["root.png"], [item.record.path.name for item in flat.fingerprints])
        self.assertEqual(
            ["child.png", "root.png"],
            sorted(item.record.path.name for item in recursive.fingerprints),
        )

    def test_exact_duplicates_are_hashed_and_grouped(self):
        """同大小候选经过 SHA-256 后形成完全重复组。"""
        first = self._save_image("first.png", "purple")
        second = self.root / "second.png"
        shutil.copyfile(first, second)

        result = ImageScanner().scan(self.root)

        self.assertEqual(1, len(result.groups))
        self.assertEqual(GroupType.EXACT, result.groups[0].group_type)
        self.assertTrue(all(item.sha256 for item in result.fingerprints))

    def test_replaced_file_is_rejected_during_complete_hashing(self):
        """视觉指纹后被同大小同时间文件替换时不得生成不一致 SHA 快照。"""
        first = self._save_image("identity-first.png", "purple")
        shutil.copyfile(first, self.root / "identity-second.png")
        original_fingerprint = scanner_module.fingerprint_image
        replaced = False
        replacement_errors = []

        def replace_after_fingerprint(path, order):
            """在第一张图完成视觉指纹后以同属性的新文件替换它。"""
            nonlocal replaced
            fingerprint = original_fingerprint(path, order)
            if not replaced:
                replaced = True
                try:
                    replacement = self.root / "identity-replacement.tmp"
                    shutil.copyfile(path, replacement)
                    os.utime(
                        replacement,
                        ns=(
                            replacement.stat().st_atime_ns,
                            fingerprint.record.mtime_ns,
                        ),
                    )
                    os.replace(replacement, path)
                except Exception as error:
                    replacement_errors.append(error)
            return fingerprint

        with mock.patch.object(
            scanner_module,
            "fingerprint_image",
            side_effect=replace_after_fingerprint,
        ):
            result = ImageScanner(max_workers=1).scan(self.root)

        self.assertEqual([], replacement_errors)
        self.assertEqual(1, len(result.fingerprints))
        self.assertEqual(1, len(result.failures))
        self.assertEqual(
            ScanErrorCode.FILE_DISAPPEARED,
            result.failures[0].code,
            result.failures[0],
        )

    def test_corrupt_image_does_not_abort_other_images(self):
        """损坏图片记录结构化失败，其他有效图片仍进入结果。"""
        self._save_image("valid.png", "green")
        (self.root / "broken.png").write_bytes(b"not an image")

        result = ImageScanner().scan(self.root)

        self.assertEqual(1, len(result.fingerprints))
        self.assertEqual(1, len(result.failures))
        self.assertIn(
            result.failures[0].code,
            {ScanErrorCode.UNSUPPORTED_IMAGE, ScanErrorCode.DECODE_FAILED},
        )

    def test_cancelled_scan_returns_no_formal_result(self):
        """预先取消的任务不暴露指纹、分组或文件操作结果。"""
        self._save_image("first.png", "red")
        cancel_event = threading.Event()
        cancel_event.set()

        result = ImageScanner().scan(self.root, cancel_event=cancel_event)

        self.assertTrue(result.cancelled)
        self.assertEqual((), result.fingerprints)
        self.assertEqual((), result.groups)

    def test_concurrent_completion_does_not_change_enumeration_order(self):
        """工作线程完成顺序不能影响正式指纹顺序。"""
        self._save_image("a.png", "red")
        self._save_image("b.png", "green")
        self._save_image("c.png", "blue")
        original = scanner_module.fingerprint_image

        def delayed_fingerprint(path, order):
            """让较早枚举的文件更晚完成以制造乱序。"""
            time.sleep((3 - order) * 0.01)
            return original(path, order)

        with mock.patch.object(
            scanner_module,
            "fingerprint_image",
            side_effect=delayed_fingerprint,
        ):
            result = ImageScanner(max_workers=3).scan(self.root)

        self.assertEqual(
            ["a.png", "b.png", "c.png"],
            [item.record.path.name for item in result.fingerprints],
        )

    def test_progress_reports_stable_phases_and_completion(self):
        """UI 至少能观察枚举、指纹、分组和完成阶段。"""
        self._save_image("first.png", "red")
        progress = []

        result = ImageScanner().scan(self.root, progress=progress.append)

        self.assertFalse(result.cancelled)
        phases = {item.phase for item in progress}
        self.assertTrue(
            {
                ScanPhase.ENUMERATING,
                ScanPhase.FINGERPRINTING,
                ScanPhase.GROUPING,
                ScanPhase.COMPLETED,
            }.issubset(phases)
        )
        self.assertNotIn(ScanPhase.THUMBNAILS, phases)
        self.assertEqual(ScanPhase.COMPLETED, progress[-1].phase)

    def test_complete_hashing_reports_its_own_progress(self):
        """同大小候选的 SHA-256 阶段必须持续报告已处理数量。"""
        first = self._save_image("hash-first.png", "purple")
        shutil.copyfile(first, self.root / "hash-second.png")
        progress = []

        ImageScanner(progress_interval=0).scan(
            self.root,
            progress=progress.append,
        )

        hashing = [item for item in progress if item.phase.value == "hashing"]
        self.assertTrue(hashing)
        self.assertEqual(0, hashing[0].processed)
        self.assertEqual(2, hashing[-1].processed)
        self.assertEqual(2, hashing[-1].total)

    def test_file_symlink_is_not_scanned(self):
        """文件符号链接不得进入正式结果，避免删除根目录外目标。"""
        target = self._save_image("target.png", "red")
        link = self.root / "linked.png"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"当前环境不能创建符号链接: {error}")

        result = ImageScanner().scan(self.root)

        self.assertEqual([target], [item.record.path for item in result.fingerprints])

    def test_cancellation_stops_directory_enumeration_early(self):
        """用户在枚举期间取消后，不应继续检查目录中的全部条目。"""
        for index in range(30):
            self._save_image(f"enumerate-{index:02d}.png", "red")
        cancellation = threading.Event()
        original_check = scanner_module._is_reparse_or_symlink
        inspected = 0

        def cancelling_check(path):
            """检查少量路径后触发取消。"""
            nonlocal inspected
            inspected += 1
            result = original_check(path)
            if inspected == 6:
                cancellation.set()
            return result

        with mock.patch.object(
            scanner_module,
            "_is_reparse_or_symlink",
            side_effect=cancelling_check,
        ):
            result = ImageScanner().scan(
                self.root,
                cancel_event=cancellation,
            )

        self.assertTrue(result.cancelled)
        self.assertLess(inspected, 31)

    def test_fingerprint_submission_is_bounded_when_cancelled(self):
        """指纹任务只能保持小批在途，取消后不得预先提交全部图片。"""
        for index in range(30):
            self._save_image(f"fingerprint-{index:02d}.png", "blue")
        cancellation = threading.Event()
        original_submit = scanner_module.ThreadPoolExecutor.submit
        submissions = 0

        def cancelling_submit(executor, function, *args, **kwargs):
            """首个任务提交后立即模拟用户取消。"""
            nonlocal submissions
            submissions += 1
            future = original_submit(executor, function, *args, **kwargs)
            if submissions == 1:
                cancellation.set()
            return future

        with mock.patch.object(
            scanner_module.ThreadPoolExecutor,
            "submit",
            new=cancelling_submit,
        ):
            result = ImageScanner(max_workers=4).scan(
                self.root,
                cancel_event=cancellation,
            )

        self.assertTrue(result.cancelled)
        self.assertLessEqual(submissions, 8)

    def test_grouping_cancellation_returns_no_formal_result(self):
        """分组阶段取消应转换为无正式结果的取消状态。"""
        self._save_image("group-a.png", "red")
        self._save_image("group-b.png", "red")
        cancellation = threading.Event()

        def interrupt_grouping(fingerprints, preset, *, cancelled):
            """确认扫描器传入取消回调后模拟分组被中止。"""
            del fingerprints, preset
            self.assertFalse(cancelled())
            cancellation.set()
            self.assertTrue(cancelled())
            raise GroupingCancelled()

        with mock.patch.object(
            scanner_module,
            "build_similarity_groups",
            side_effect=interrupt_grouping,
        ):
            result = ImageScanner().scan(
                self.root,
                cancel_event=cancellation,
            )

        self.assertTrue(result.cancelled)
        self.assertEqual((), result.groups)
        self.assertEqual((), result.fingerprints)


if __name__ == "__main__":
    unittest.main()
