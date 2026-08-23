"""验证回收站操作前的路径和文件快照安全复核。"""

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.image_similarity.models import (
    ImageRecord,
    RecycleStatus,
)
from tools.image_similarity.recycle_bin import RecycleBinService


class ImageSimilarityRecycleBinTests(unittest.TestCase):
    """用记录调用的假后端验证逻辑，绝不真实处理文件。"""

    def setUp(self):
        """创建扫描根目录和一个根目录外的一次性目录。"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.root = self.base / "scan"
        self.root.mkdir()
        self.outside = self.base / "outside.png"
        self.outside.write_bytes(b"outside")

    def tearDown(self):
        """清理假后端未删除的一次性文件。"""
        self.temp_dir.cleanup()

    def _create_record(self, name, content=b"image"):
        """在扫描根目录写入文件并返回对应扫描快照。"""
        path = self.root / name
        path.write_bytes(content)
        path_stat = path.stat()
        return ImageRecord(
            path=path,
            canonical_path=path.resolve(),
            size_bytes=path_stat.st_size,
            mtime_ns=path_stat.st_mtime_ns,
            image_format="PNG",
            width=10,
            height=10,
            order=0,
        )

    def test_safe_unchanged_file_calls_backend_once(self):
        """只有属于正式结果且快照未变化的普通文件会进入后端。"""
        record = self._create_record("safe.png")
        calls = []
        service = RecycleBinService(
            self.root,
            (record,),
            backend=lambda path: calls.append(Path(path)),
        )

        results = service.move_items((record.path,))

        self.assertEqual([record.path], calls)
        self.assertEqual(RecycleStatus.MOVED_TO_TRASH, results[0].status)

    def test_changed_file_is_skipped_without_backend_call(self):
        """大小或修改时间变化的文件要求重新扫描。"""
        record = self._create_record("changed.png")
        record.path.write_bytes(b"changed content with another size")
        calls = []
        service = RecycleBinService(
            self.root,
            (record,),
            backend=lambda path: calls.append(path),
        )

        results = service.move_items((record.path,))

        self.assertEqual([], calls)
        self.assertEqual(RecycleStatus.SKIPPED_CHANGED, results[0].status)

    def test_replaced_file_with_same_size_and_mtime_is_skipped(self):
        """文件身份变化时即使大小和时间相同也必须拒绝回收。"""
        record = self._create_record("replaced.png", b"original")
        original_stat = record.path.stat()
        object.__setattr__(record, "device_id", original_stat.st_dev)
        object.__setattr__(record, "file_id", original_stat.st_ino)
        replacement = self.root / "replacement.tmp"
        replacement.write_bytes(b"replaced")
        os.utime(
            replacement,
            ns=(replacement.stat().st_atime_ns, record.mtime_ns),
        )
        os.replace(replacement, record.path)
        current_stat = record.path.stat()
        if (
            current_stat.st_dev == original_stat.st_dev
            and current_stat.st_ino == original_stat.st_ino
        ):
            self.skipTest("当前文件系统复用了相同文件身份")
        calls = []
        service = RecycleBinService(
            self.root,
            (record,),
            backend=lambda path: calls.append(path),
        )

        result = service.move_items((record.path,))[0]

        self.assertEqual([], calls)
        self.assertEqual(RecycleStatus.SKIPPED_CHANGED, result.status)

    def test_snapshot_is_rechecked_immediately_before_backend_call(self):
        """最终复核发现状态漂移时不得把路径交给回收站后端。"""
        record = self._create_record("late-change.png")
        calls = []
        service = RecycleBinService(
            self.root,
            (record,),
            backend=lambda path: calls.append(path),
        )

        with mock.patch(
            "tools.image_similarity.recycle_bin._snapshot_matches_record",
            side_effect=(True, False),
            create=True,
        ) as snapshot_matches:
            result = service.move_items((record.path,))[0]

        self.assertEqual(2, snapshot_matches.call_count)
        self.assertEqual([], calls)
        self.assertEqual(RecycleStatus.SKIPPED_CHANGED, result.status)

    def test_outside_unscanned_and_directory_paths_are_rejected(self):
        """根目录外、结果集外和目录路径都属于不安全选择。"""
        scanned = self._create_record("scanned.png")
        unscanned = self.root / "unscanned.png"
        unscanned.write_bytes(b"not in result")
        calls = []
        service = RecycleBinService(
            self.root,
            (scanned,),
            backend=lambda path: calls.append(path),
        )

        results = service.move_items((self.outside, unscanned, self.root))

        self.assertEqual([], calls)
        self.assertEqual(
            [RecycleStatus.SKIPPED_UNSAFE_PATH] * 3,
            [result.status for result in results],
        )

    def test_backend_failure_does_not_stop_later_items(self):
        """单项回收站异常不会阻止后续安全文件继续处理。"""
        first = self._create_record("first.png", b"first")
        second = self._create_record("second.png", b"second")
        calls = []

        def backend(path):
            """记录调用并让第一项抛出模拟后端异常。"""
            path = Path(path)
            calls.append(path)
            if path.name == "first.png":
                raise OSError("simulated trash failure")

        service = RecycleBinService(self.root, (first, second), backend=backend)

        results = service.move_items((first.path, second.path))

        self.assertEqual([first.path, second.path], calls)
        self.assertEqual(
            [
                RecycleStatus.TRASH_OPERATION_FAILED,
                RecycleStatus.MOVED_TO_TRASH,
            ],
            [result.status for result in results],
        )

    def test_missing_file_is_reported_as_changed(self):
        """扫描后消失的文件视为状态变化，不转为永久删除路径。"""
        record = self._create_record("missing.png")
        os.remove(record.path)
        calls = []
        service = RecycleBinService(
            self.root,
            (record,),
            backend=lambda path: calls.append(path),
        )

        result = service.move_items((record.path,))[0]

        self.assertEqual([], calls)
        self.assertEqual(RecycleStatus.SKIPPED_CHANGED, result.status)

    def test_path_resolution_failure_is_rejected_and_later_items_continue(self):
        """路径解析异常必须按不安全项拒绝，且不能中断同批后续文件。"""
        first = self._create_record("resolve-error.png", b"first")
        second = self._create_record("after-resolve-error.png", b"second")
        calls = []
        service = RecycleBinService(
            self.root,
            (first, second),
            backend=lambda path: calls.append(Path(path)),
        )
        original_resolve = Path.resolve

        def resolve_with_failure(path, strict=False):
            """只为第一项模拟操作系统拒绝解析路径。"""
            if path == first.path:
                raise OSError("simulated resolve failure")
            return original_resolve(path, strict=strict)

        with mock.patch.object(Path, "resolve", new=resolve_with_failure):
            results = service.move_items((first.path, second.path))

        self.assertEqual([second.path], calls)
        self.assertEqual(
            [
                RecycleStatus.SKIPPED_UNSAFE_PATH,
                RecycleStatus.MOVED_TO_TRASH,
            ],
            [result.status for result in results],
        )

    def test_reparse_inspection_failure_is_rejected_without_backend_call(self):
        """无法确认链接或重解析属性时必须失败关闭而不是继续回收。"""
        record = self._create_record("inspection-error.png")
        calls = []
        service = RecycleBinService(
            self.root,
            (record,),
            backend=lambda path: calls.append(path),
        )

        with mock.patch.object(
            Path,
            "is_symlink",
            side_effect=OSError("simulated inspection failure"),
        ):
            result = service.move_items((record.path,))[0]

        self.assertEqual([], calls)
        self.assertEqual(RecycleStatus.SKIPPED_UNSAFE_PATH, result.status)

    def test_symlink_loop_runtime_error_is_rejected_and_batch_continues(self):
        """旧版 Python 的符号链接循环 RuntimeError 也必须逐项失败关闭。"""
        first = self._create_record("runtime-error.png", b"first")
        second = self._create_record("after-runtime-error.png", b"second")
        calls = []
        service = RecycleBinService(
            self.root,
            (first, second),
            backend=lambda path: calls.append(Path(path)),
        )
        original_resolve = Path.resolve

        def resolve_with_runtime_error(path, strict=False):
            """只为第一项模拟符号链接循环异常。"""
            if path == first.path:
                raise RuntimeError("simulated symlink loop")
            return original_resolve(path, strict=strict)

        with mock.patch.object(Path, "resolve", new=resolve_with_runtime_error):
            results = service.move_items((first.path, second.path))

        self.assertEqual([second.path], calls)
        self.assertEqual(
            [
                RecycleStatus.SKIPPED_UNSAFE_PATH,
                RecycleStatus.MOVED_TO_TRASH,
            ],
            [result.status for result in results],
        )

    def test_selected_path_cannot_drift_to_another_scanned_record(self):
        """扫描后原路径即使解析到另一正式记录，也不能借用后者快照通过复核。"""
        original = self._create_record("original.png", b"same-size")
        other = self._create_record("other.png", b"same-size")
        current_stat = original.path.stat()
        other = replace(
            other,
            size_bytes=current_stat.st_size,
            mtime_ns=current_stat.st_mtime_ns,
        )
        calls = []
        service = RecycleBinService(
            self.root,
            (original, other),
            backend=lambda path: calls.append(Path(path)),
        )
        original_resolve = Path.resolve

        def resolve_to_other(path, strict=False):
            """模拟扫描后原路径被目录连接重定向到另一条已扫描路径。"""
            if path == original.path:
                return other.canonical_path
            return original_resolve(path, strict=strict)

        with mock.patch.object(Path, "resolve", new=resolve_to_other):
            result = service.move_items((original.path,))[0]

        self.assertEqual([], calls)
        self.assertEqual(RecycleStatus.SKIPPED_UNSAFE_PATH, result.status)

    def test_reparse_ancestor_is_rejected_without_backend_call(self):
        """文件本身普通但任一祖先目录是重解析点时也必须拒绝回收。"""
        nested = self.root / "nested"
        nested.mkdir()
        record = self._create_record("nested/image.png")
        calls = []
        service = RecycleBinService(
            self.root,
            (record,),
            backend=lambda path: calls.append(Path(path)),
        )

        def reports_reparse_ancestor(path):
            """只把中间目录标记成链接，验证检查不会停在最终文件。"""
            return path == nested

        with mock.patch.object(Path, "is_symlink", new=reports_reparse_ancestor):
            result = service.move_items((record.path,))[0]

        self.assertEqual([], calls)
        self.assertEqual(RecycleStatus.SKIPPED_UNSAFE_PATH, result.status)


if __name__ == "__main__":
    unittest.main()
