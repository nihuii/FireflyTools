"""安全枚举图片，并在有界后台并发中协调指纹与分组。"""

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import replace
import os
from pathlib import Path
import stat
import threading
import time
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from tools.image_similarity.fingerprints import (
    FingerprintError,
    fingerprint_image,
    sha256_file,
)
from tools.image_similarity.grouping import (
    GroupingCancelled,
    build_similarity_groups,
)
from tools.image_similarity.models import (
    ImageFingerprint,
    ScanErrorCode,
    ScanFailure,
    ScanPhase,
    ScanProgress,
    ScanResult,
    SimilarityPreset,
)


SUPPORTED_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
)
REPARSE_POINT_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)


def _is_reparse_or_symlink(path: Path) -> bool:
    """判断路径是否为符号链接或 Windows reparse point。"""
    try:
        if path.is_symlink():
            return True
        path_stat = path.stat(follow_symlinks=False)
    except OSError:
        return True
    attributes = getattr(path_stat, "st_file_attributes", 0)
    return bool(attributes & REPARSE_POINT_FLAG)


def _enumerate_images(
    root: Path,
    recursive: bool,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[Path], list[ScanFailure]]:
    """枚举受支持图片，同时隔离逐目录和逐条目文件系统错误。"""
    images: list[Path] = []
    failures: list[ScanFailure] = []
    pending_directories = [root]

    is_cancelled = cancelled or (lambda: False)
    while pending_directories and not is_cancelled():
        directory = pending_directories.pop(0)
        try:
            with os.scandir(directory) as iterator:
                entries = []
                for entry in iterator:
                    if is_cancelled():
                        break
                    entries.append(entry)
                entries.sort(key=lambda item: item.name.casefold())
        except PermissionError:
            failures.append(
                ScanFailure(
                    path=directory,
                    code=ScanErrorCode.PERMISSION_DENIED,
                    message="没有权限读取目录",
                )
            )
            continue
        except OSError:
            failures.append(
                ScanFailure(
                    path=directory,
                    code=ScanErrorCode.UNKNOWN_SCAN_ERROR,
                    message="读取目录失败",
                )
            )
            continue

        for entry in entries:
            if is_cancelled():
                break
            path = Path(entry.path)
            try:
                if _is_reparse_or_symlink(path):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        pending_directories.append(path)
                    continue
                if (
                    entry.is_file(follow_symlinks=False)
                    and path.suffix.casefold() in SUPPORTED_EXTENSIONS
                ):
                    images.append(path)
            except PermissionError:
                failures.append(
                    ScanFailure(
                        path=path,
                        code=ScanErrorCode.PERMISSION_DENIED,
                        message="没有权限读取路径信息",
                    )
                )
            except OSError:
                failures.append(
                    ScanFailure(
                        path=path,
                        code=ScanErrorCode.FILE_DISAPPEARED,
                        message="路径在枚举期间消失",
                    )
                )

    return images, failures


def _bounded_futures(
    executor: ThreadPoolExecutor,
    items,
    submit_item: Callable[[object], Future],
    cancellation: threading.Event,
    max_pending: int,
):
    """仅维持有限在途任务，并在取消后停止提交且撤销尚未运行的任务。"""
    iterator = iter(items)
    pending: dict[Future, object] = {}
    exhausted = False

    def fill_pending() -> None:
        """在未取消时把在途窗口补到上限。"""
        nonlocal exhausted
        while (
            not exhausted
            and not cancellation.is_set()
            and len(pending) < max_pending
        ):
            try:
                item = next(iterator)
            except StopIteration:
                exhausted = True
                break
            pending[submit_item(item)] = item

    fill_pending()
    while pending:
        if cancellation.is_set():
            for future in pending:
                future.cancel()
            return
        done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
        for future in done:
            item = pending.pop(future)
            yield future, item
        fill_pending()


class _ProgressReporter:
    """限制普通进度刷新频率，同时允许阶段切换立即送达。"""

    def __init__(
        self,
        callback: Callable[[ScanProgress], None] | None,
        interval: float,
        clock: Callable[[], float],
    ):
        """保存回调、最小间隔和可测试时钟。"""
        self._callback = callback
        self._interval = interval
        self._clock = clock
        self._last_emitted_at = float("-inf")

    def emit(self, progress: ScanProgress, *, force: bool = False) -> None:
        """在强制阶段或达到节流间隔时调用进度回调。"""
        if self._callback is None:
            return
        now = self._clock()
        if force or now - self._last_emitted_at >= self._interval:
            self._callback(progress)
            self._last_emitted_at = now


def _hash_fingerprint(fingerprint: ImageFingerprint) -> ImageFingerprint:
    """为同大小候选计算 SHA-256，并复核扫描快照未变化。"""
    record = fingerprint.record
    digest = sha256_file(record.path)
    current = record.path.stat()
    if (
        current.st_size != record.size_bytes
        or current.st_mtime_ns != record.mtime_ns
        or (
            record.device_id is not None
            and current.st_dev != record.device_id
        )
        or (
            record.file_id is not None
            and current.st_ino != record.file_id
        )
    ):
        raise FingerprintError(
            record.path,
            ScanErrorCode.FILE_DISAPPEARED,
            "图片在完整哈希期间发生变化",
        )
    return replace(fingerprint, sha256=digest)


def _failure_from_exception(path: Path, error: Exception) -> ScanFailure:
    """把指纹或文件异常归一化为单项扫描失败。"""
    if isinstance(error, FingerprintError):
        return ScanFailure(error.path, error.code, error.message)
    if isinstance(error, FileNotFoundError):
        return ScanFailure(
            path,
            ScanErrorCode.FILE_DISAPPEARED,
            "图片在扫描期间消失",
        )
    if isinstance(error, PermissionError):
        return ScanFailure(
            path,
            ScanErrorCode.PERMISSION_DENIED,
            "没有权限读取图片",
        )
    return ScanFailure(
        path,
        ScanErrorCode.UNKNOWN_SCAN_ERROR,
        "处理图片时发生未知错误",
    )


class ImageScanner:
    """执行只读目录扫描、混合指纹计算和安全相似分组。"""

    def __init__(
        self,
        max_workers: int = 4,
        progress_interval: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
    ):
        """设置不超过四个线程的并发和每秒十次的默认进度上限。"""
        self.max_workers = max(1, min(4, max_workers))
        self.progress_interval = max(0.0, progress_interval)
        self.clock = clock

    def _cancelled_result(
        self,
        root: Path,
        preset: SimilarityPreset,
        failures: list[ScanFailure],
        reporter: _ProgressReporter,
        total: int,
    ) -> ScanResult:
        """构造不包含任何正式中间数据的取消结果。"""
        reporter.emit(
            ScanProgress(ScanPhase.CANCELLED, 0, total, len(failures)),
            force=True,
        )
        return ScanResult(
            root=root,
            preset=preset,
            groups=(),
            fingerprints=(),
            failures=tuple(failures),
            cancelled=True,
        )

    def scan(
        self,
        root: str | Path,
        recursive: bool = True,
        preset: SimilarityPreset = SimilarityPreset.STRICT,
        progress: Callable[[ScanProgress], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ScanResult:
        """扫描一个根目录并返回与 Qt 控件无关的稳定纯数据结果。"""
        root_path = Path(root).expanduser()
        if not root_path.exists() or not root_path.is_dir():
            raise ValueError("扫描根目录不存在或不是目录")
        if _is_reparse_or_symlink(root_path):
            raise ValueError("扫描根目录不能是链接或重解析目标")
        canonical_root = root_path.resolve(strict=True)
        cancellation = cancel_event or threading.Event()
        reporter = _ProgressReporter(
            progress,
            self.progress_interval,
            self.clock,
        )
        failures: list[ScanFailure] = []

        reporter.emit(
            ScanProgress(ScanPhase.ENUMERATING, 0, 0, 0),
            force=True,
        )
        if cancellation.is_set():
            return self._cancelled_result(
                canonical_root,
                preset,
                failures,
                reporter,
                0,
            )

        paths, enumeration_failures = _enumerate_images(
            canonical_root,
            recursive,
            cancellation.is_set,
        )
        failures.extend(enumeration_failures)
        reporter.emit(
            ScanProgress(
                ScanPhase.ENUMERATING,
                len(paths),
                len(paths),
                len(failures),
            ),
            force=True,
        )
        if cancellation.is_set():
            return self._cancelled_result(
                canonical_root,
                preset,
                failures,
                reporter,
                len(paths),
            )

        reporter.emit(
            ScanProgress(
                ScanPhase.FINGERPRINTING,
                0,
                len(paths),
                len(failures),
            ),
            force=True,
        )
        fingerprints_by_order: dict[int, ImageFingerprint] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            processed = 0
            for future, item in _bounded_futures(
                executor,
                enumerate(paths),
                lambda value: executor.submit(
                    fingerprint_image,
                    value[1],
                    value[0],
                ),
                cancellation,
                self.max_workers * 2,
            ):
                order, path = item
                try:
                    fingerprints_by_order[order] = future.result()
                except Exception as error:
                    failures.append(_failure_from_exception(path, error))
                processed += 1
                reporter.emit(
                    ScanProgress(
                        ScanPhase.FINGERPRINTING,
                        processed,
                        len(paths),
                        len(failures),
                    )
                )

        if cancellation.is_set():
            return self._cancelled_result(
                canonical_root,
                preset,
                failures,
                reporter,
                len(paths),
            )

        size_buckets: dict[int, list[ImageFingerprint]] = {}
        for fingerprint in fingerprints_by_order.values():
            size_buckets.setdefault(fingerprint.record.size_bytes, []).append(fingerprint)
        hash_candidates = [
            fingerprint
            for bucket in size_buckets.values()
            if len(bucket) > 1
            for fingerprint in bucket
        ]
        if hash_candidates:
            reporter.emit(
                ScanProgress(
                    ScanPhase.HASHING,
                    0,
                    len(hash_candidates),
                    len(failures),
                ),
                force=True,
            )
            hash_processed = 0
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                for future, original in _bounded_futures(
                    executor,
                    hash_candidates,
                    lambda fingerprint: executor.submit(
                        _hash_fingerprint,
                        fingerprint,
                    ),
                    cancellation,
                    self.max_workers * 2,
                ):
                    try:
                        hashed = future.result()
                    except Exception as error:
                        failures.append(
                            _failure_from_exception(original.record.path, error)
                        )
                        fingerprints_by_order.pop(original.record.order, None)
                    else:
                        fingerprints_by_order[original.record.order] = hashed
                    hash_processed += 1
                    reporter.emit(
                        ScanProgress(
                            ScanPhase.HASHING,
                            hash_processed,
                            len(hash_candidates),
                            len(failures),
                        ),
                        force=hash_processed == len(hash_candidates),
                    )

        if cancellation.is_set():
            return self._cancelled_result(
                canonical_root,
                preset,
                failures,
                reporter,
                len(paths),
            )

        ordered_fingerprints = tuple(
            fingerprints_by_order[index]
            for index in sorted(fingerprints_by_order)
        )
        reporter.emit(
            ScanProgress(
                ScanPhase.GROUPING,
                0,
                len(ordered_fingerprints),
                len(failures),
            ),
            force=True,
        )
        try:
            groups = build_similarity_groups(
                ordered_fingerprints,
                preset,
                cancelled=cancellation.is_set,
            )
        except GroupingCancelled:
            return self._cancelled_result(
                canonical_root,
                preset,
                failures,
                reporter,
                len(paths),
            )
        if cancellation.is_set():
            return self._cancelled_result(
                canonical_root,
                preset,
                failures,
                reporter,
                len(paths),
            )
        result = ScanResult(
            root=canonical_root,
            preset=preset,
            groups=groups,
            fingerprints=ordered_fingerprints,
            failures=tuple(failures),
        )
        reporter.emit(
            ScanProgress(
                ScanPhase.COMPLETED,
                len(ordered_fingerprints),
                len(ordered_fingerprints),
                len(failures),
            ),
            force=True,
        )
        return result


class ImageScanWorker(QObject):
    """把同步扫描器包装成可移动到 QThread 的 Qt worker。"""

    progress_changed = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(
        self,
        root: Path,
        recursive: bool,
        preset: SimilarityPreset,
        scanner: ImageScanner | None = None,
    ):
        """保存一次任务快照和线程安全取消事件。"""
        super().__init__()
        self.root = root
        self.recursive = recursive
        self.preset = preset
        self.scanner = scanner or ImageScanner()
        self.cancel_event = threading.Event()

    @pyqtSlot()
    def run(self) -> None:
        """在线程中执行扫描，并以信号交付纯数据或错误。"""
        try:
            result = self.scanner.scan(
                self.root,
                recursive=self.recursive,
                preset=self.preset,
                progress=self.progress_changed.emit,
                cancel_event=self.cancel_event,
            )
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()

    @pyqtSlot()
    def cancel(self) -> None:
        """请求协作式取消，不强制终止执行线程。"""
        self.cancel_event.set()
