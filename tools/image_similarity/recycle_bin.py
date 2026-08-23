"""在逐项安全复核后把用户明确选择的图片移入系统回收站。"""

import os
from pathlib import Path
import stat
from typing import Callable, Iterable

from send2trash import send2trash

from tools.image_similarity.models import (
    ImageRecord,
    RecycleItemResult,
    RecycleStatus,
)


REPARSE_POINT_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)


def _path_key(path: Path) -> str:
    """生成适合当前平台比较扫描路径的规范键。"""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_relative_to(path: Path, root: Path) -> bool:
    """判断规范路径是否位于本次扫描根目录内。"""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_reparse_or_symlink(path: Path) -> bool:
    """拒绝符号链接和 Windows reparse point。"""
    try:
        if path.is_symlink():
            return True
        path_stat = path.stat(follow_symlinks=False)
    except OSError:
        # 无法确认路径属性时必须失败关闭，避免把未知对象当作普通文件处理。
        return True
    return bool(getattr(path_stat, "st_file_attributes", 0) & REPARSE_POINT_FLAG)


def _has_reparse_or_symlink_component(path: Path, root: Path) -> bool:
    """检查从扫描根目录到文件的每一级，避免中间目录被连接替换。"""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True

    current = root
    if _is_reparse_or_symlink(current):
        return True
    for part in relative.parts:
        current = current / part
        if _is_reparse_or_symlink(current):
            return True
    return False


def _snapshot_matches_record(path: Path, record: ImageRecord) -> bool:
    """比较当前文件状态与扫描快照，包括稳定文件身份。"""
    current = path.stat()
    return not (
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
    )


class RecycleBinService:
    """只对正式扫描结果中的未变化安全文件调用回收站后端。"""

    def __init__(
        self,
        root: Path,
        scanned_records: Iterable[ImageRecord],
        backend: Callable[[str | Path], None] = send2trash,
    ):
        """快照扫描根目录、正式记录集合和可注入回收站后端。"""
        self.root = Path(root).resolve(strict=True)
        self.backend = backend
        self._records = {
            _path_key(record.path): record
            for record in scanned_records
        }

    def _unsafe_result(self, path: Path, message: str) -> RecycleItemResult:
        """构造不调用后端的路径安全拒绝结果。"""
        return RecycleItemResult(
            path=path,
            status=RecycleStatus.SKIPPED_UNSAFE_PATH,
            message=message,
        )

    def _changed_result(self, path: Path, message: str) -> RecycleItemResult:
        """构造要求用户重新扫描的文件变化结果。"""
        return RecycleItemResult(
            path=path,
            status=RecycleStatus.SKIPPED_CHANGED,
            message=message,
        )

    def _move_one(self, selected_path: Path) -> RecycleItemResult:
        """复核一个选择并在全部条件满足时调用回收站后端。"""
        path = Path(selected_path)
        record = self._records.get(_path_key(path))
        if record is None:
            return self._unsafe_result(path, "路径不属于本次正式扫描结果")

        lexical_path = Path(os.path.abspath(os.fspath(path)))
        if not _is_relative_to(lexical_path, self.root):
            return self._unsafe_result(path, "路径不在本次扫描根目录内")
        try:
            candidate = path.resolve(strict=False)
        except (OSError, RuntimeError):
            return self._unsafe_result(path, "无法安全解析所选路径")
        if not _is_relative_to(candidate, self.root):
            return self._unsafe_result(path, "路径不在本次扫描根目录内")
        if _path_key(candidate) != _path_key(record.canonical_path):
            return self._unsafe_result(path, "路径在扫描后指向了其他位置")
        if not path.exists():
            return self._changed_result(path, "文件已不存在，请重新扫描")
        if _has_reparse_or_symlink_component(lexical_path, self.root):
            return self._unsafe_result(path, "路径包含链接或重解析目录，不能移入回收站")
        if not path.is_file():
            return self._unsafe_result(path, "所选路径不是普通文件")

        try:
            snapshot_matches = _snapshot_matches_record(path, record)
        except OSError:
            return self._changed_result(path, "无法读取当前文件状态，请重新扫描")
        if not snapshot_matches:
            return self._changed_result(path, "文件在扫描后发生变化，请重新扫描")

        # 路径型回收站后端会重新打开目标；在调用前尽可能紧邻地再次复核路径和文件身份。
        try:
            final_candidate = path.resolve(strict=True)
        except FileNotFoundError:
            return self._changed_result(path, "文件已不存在，请重新扫描")
        except (OSError, RuntimeError):
            return self._unsafe_result(path, "无法安全解析所选路径")
        if (
            not _is_relative_to(final_candidate, self.root)
            or _path_key(final_candidate) != _path_key(record.canonical_path)
            or _has_reparse_or_symlink_component(lexical_path, self.root)
            or not path.is_file()
        ):
            return self._unsafe_result(path, "路径在最终复核时发生变化")
        try:
            final_snapshot_matches = _snapshot_matches_record(path, record)
        except OSError:
            return self._changed_result(path, "无法读取最终文件状态，请重新扫描")
        if not final_snapshot_matches:
            return self._changed_result(path, "文件在最终复核时发生变化，请重新扫描")

        try:
            self.backend(path)
        except Exception as error:
            return RecycleItemResult(
                path=path,
                status=RecycleStatus.TRASH_OPERATION_FAILED,
                message=f"移入系统回收站失败：{error}",
            )
        return RecycleItemResult(
            path=path,
            status=RecycleStatus.MOVED_TO_TRASH,
            message="已移入系统回收站",
        )

    def move_items(
        self,
        paths: Iterable[Path],
    ) -> tuple[RecycleItemResult, ...]:
        """按用户给出的顺序逐项处理，单项失败不阻止后续项目。"""
        return tuple(self._move_one(Path(path)) for path in paths)
