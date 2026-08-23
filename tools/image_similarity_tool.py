"""提供图片相似度检测、结果复核和安全回收站操作的 PyQt6 页面。"""

from collections import Counter, OrderedDict
from dataclasses import replace
from pathlib import Path
import threading
from typing import Callable

from PyQt6.QtCore import (
    QAbstractListModel,
    QAbstractTableModel,
    QFile,
    QIODevice,
    QModelIndex,
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QDesktopServices, QImage, QImageReader, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from tools.image_similarity.grouping import (
    GroupingCancelled,
    build_similarity_groups,
)
from tools.image_similarity.models import (
    GroupType,
    ImageFingerprint,
    RecycleItemResult,
    RecycleStatus,
    ScanPhase,
    ScanProgress,
    ScanResult,
    SimilarityGroup,
    SimilarityPreset,
)
from tools.image_similarity.recycle_bin import RecycleBinService
from tools.image_similarity.scanner import ImageScanWorker
from tools.theme_utils import apply_shadow


PHASE_LABELS = {
    ScanPhase.ENUMERATING: "正在枚举图片",
    ScanPhase.FINGERPRINTING: "正在读取图片并计算指纹",
    ScanPhase.HASHING: "正在校验完全重复图片",
    ScanPhase.GROUPING: "正在生成相似分组",
    ScanPhase.THUMBNAILS: "正在准备缩略图",
    ScanPhase.COMPLETED: "扫描完成",
    ScanPhase.CANCELLED: "扫描已取消",
}
GROUP_LABELS = {
    GroupType.EXACT: "完全重复",
    GroupType.VISUAL: "视觉相似",
}


def format_size(size_bytes: int) -> str:
    """把字节数格式化为用户容易阅读的二进制单位。"""
    size = float(max(0, size_bytes))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _decode_thumbnail(path: Path, target_size: QSize) -> QImage:
    """在后台线程读取并缩放图片，只返回可跨线程传递的 QImage。"""
    source = QFile(str(path))
    if not source.open(QIODevice.OpenModeFlag.ReadOnly):
        return QImage()
    try:
        reader = QImageReader(source)
        reader.setAutoTransform(True)
        original_size = reader.size()
        if original_size.isValid():
            original_size.scale(target_size, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(original_size)
        return reader.read()
    finally:
        source.close()


class _ThumbnailTask(QRunnable):
    """在线程池中执行一次不接触 QPixmap 的缩略图解码。"""

    def __init__(
        self,
        path: Path,
        target_size: QSize,
        generation: int,
        task_id: int,
        decoder: Callable[[Path, QSize], QImage],
    ):
        """保存解码参数、缓存代次和线程安全的完成信号。"""
        super().__init__()
        self.path = path
        self.target_size = QSize(target_size)
        self.generation = generation
        self.task_id = task_id
        self.decoder = decoder
        self.signals = _ThumbnailTaskSignals()
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        """请求尚未开始或正在解码的任务停止交付结果。"""
        self.cancel_event.set()

    def run(self) -> None:
        """读取 QImage；单图失败时返回空图而不终止线程池。"""
        try:
            if self.cancel_event.is_set():
                return
            try:
                image = self.decoder(self.path, self.target_size)
            except Exception:
                image = QImage()
            if not self.cancel_event.is_set():
                self.signals.decoded.emit(
                    self.path,
                    image,
                    self.generation,
                    self.task_id,
                )
        finally:
            self.signals.finished.emit(
                self.path,
                self.generation,
                self.task_id,
            )


class _ThumbnailTaskSignals(QObject):
    """让缩略图任务拥有独立信号，缓存销毁时连接可由 Qt 自动断开。"""

    decoded = pyqtSignal(object, object, int, int)
    finished = pyqtSignal(object, int, int)


class ThumbnailCache(QObject):
    """异步解码图片，并仅在 GUI 线程创建有容量上限的 QPixmap 缓存。"""

    thumbnail_ready = pyqtSignal(object)
    thumbnail_finished = pyqtSignal(object)
    capacity_available = pyqtSignal()
    tasks_idle = pyqtSignal()

    def __init__(
        self,
        capacity: int = 128,
        thumbnail_size: QSize | None = None,
        *,
        asynchronous: bool = True,
        max_workers: int = 4,
        max_pending: int = 32,
        max_deferred: int | None = None,
        decoder: Callable[[Path, QSize], QImage] = _decode_thumbnail,
        parent=None,
    ):
        """设置缓存容量和默认缩略图尺寸。"""
        super().__init__(parent)
        self.capacity = max(1, capacity)
        self.thumbnail_size = thumbnail_size or QSize(88, 64)
        self.asynchronous = asynchronous
        self.max_pending = max(1, max_pending)
        self.max_deferred = max(1, max_deferred or self.capacity)
        self.decoder = decoder
        self._cache: OrderedDict[Path, QPixmap] = OrderedDict()
        self._tasks: dict[int, _ThumbnailTask] = {}
        self._path_to_task: dict[tuple[int, Path], int] = {}
        self._deferred_requests: OrderedDict[
            tuple[int, Path], QSize
        ] = OrderedDict()
        self._next_task_id = 0
        self._generation = 0
        self._accept_requests = True
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(max(1, min(4, max_workers)))

    def get(self, path: Path, size: QSize | None = None) -> QPixmap:
        """立即返回缓存；缺失项在后台排队并暂时返回空像素图。"""
        image_path = Path(path)
        cached = self._cache.pop(image_path, None)
        if cached is not None:
            self._cache[image_path] = cached
            return cached

        target_size = QSize(size or self.thumbnail_size)
        if self._accept_requests:
            generation = self._generation
            if self.asynchronous:
                self._submit_or_defer(image_path, target_size, generation)
            else:
                try:
                    image = self.decoder(image_path, target_size)
                except Exception:
                    image = QImage()
                self._store_decoded(image_path, image, generation, -1)
        return self._cache.get(image_path, QPixmap())

    def _submit_or_defer(
        self,
        image_path: Path,
        target_size: QSize,
        generation: int,
    ) -> None:
        """提交当前代次请求；容量不足时放入有界延迟队列。"""
        request_key = (generation, image_path)
        if request_key in self._path_to_task:
            return
        if request_key in self._deferred_requests:
            self._deferred_requests.move_to_end(request_key)
            return
        if len(self._tasks) < self.max_pending:
            self._start_task(image_path, target_size, generation)
            return
        self._deferred_requests[request_key] = QSize(target_size)
        while len(self._deferred_requests) > self.max_deferred:
            self._deferred_requests.popitem(last=False)

    def _start_task(
        self,
        image_path: Path,
        target_size: QSize,
        generation: int,
    ) -> None:
        """创建一个带代次键的后台解码任务并交给线程池。"""
        task_id = self._next_task_id
        self._next_task_id += 1
        task = _ThumbnailTask(
            image_path,
            target_size,
            generation,
            task_id,
            self.decoder,
        )
        task.signals.decoded.connect(
            self._store_decoded,
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            self._task_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._tasks[task_id] = task
        self._path_to_task[(generation, image_path)] = task_id
        self._thread_pool.start(task)

    def _schedule_deferred(self) -> None:
        """容量释放后按请求顺序补排仍属于当前代次的缩略图。"""
        while (
            self._accept_requests
            and len(self._tasks) < self.max_pending
            and self._deferred_requests
        ):
            request_key, target_size = self._deferred_requests.popitem(
                last=False
            )
            generation, image_path = request_key
            if generation != self._generation or image_path in self._cache:
                continue
            if request_key in self._path_to_task:
                continue
            self._start_task(image_path, target_size, generation)

    @pyqtSlot(object, object, int, int)
    def _store_decoded(
        self,
        image_path: Path,
        image: QImage,
        generation: int,
        task_id: int,
    ) -> None:
        """在缓存所属 GUI 线程把 QImage 转为 QPixmap 并通知模型刷新。"""
        image_path = Path(image_path)
        if generation != self._generation:
            return
        request_key = (generation, image_path)
        if task_id >= 0 and self._path_to_task.get(request_key) != task_id:
            return
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        self._cache[image_path] = pixmap
        while len(self._cache) > self.capacity:
            self._cache.popitem(last=False)
        self.thumbnail_ready.emit(image_path)

    @pyqtSlot(object, int, int)
    def _task_finished(
        self,
        image_path: Path,
        generation: int,
        task_id: int,
    ) -> None:
        """释放任务引用、通知模型重试，并在全部结束时报告空闲。"""
        self._tasks.pop(task_id, None)
        image_path = Path(image_path)
        request_key = (generation, image_path)
        if self._path_to_task.get(request_key) == task_id:
            self._path_to_task.pop(request_key, None)
        if generation == self._generation:
            self.thumbnail_finished.emit(image_path)
            self.capacity_available.emit()
        self._schedule_deferred()
        if not self._tasks:
            # finished 信号先于 QRunnable.run() 返回；短暂等待可确保文件句柄已释放。
            self._thread_pool.waitForDone(1000)
            self.tasks_idle.emit()

    def clear(self) -> None:
        """释放当前扫描持有的全部缩略图。"""
        self._generation += 1
        self._cache.clear()
        self._deferred_requests.clear()
        removed_any = False
        for task_id, task in tuple(self._tasks.items()):
            task.cancel()
            try:
                removed = self._thread_pool.tryTake(task)
            except RuntimeError:
                removed = False
            if removed:
                removed_any = True
                self._tasks.pop(task_id, None)
                request_key = (task.generation, task.path)
                if self._path_to_task.get(request_key) == task_id:
                    self._path_to_task.pop(request_key, None)
        if removed_any:
            self.capacity_available.emit()
        if not self._tasks:
            self.tasks_idle.emit()

    def has_active_tasks(self) -> bool:
        """返回是否仍有已提交但尚未结束的缩略图任务。"""
        return bool(self._tasks or self._deferred_requests)

    def resume(self) -> None:
        """允许新扫描结果重新提交缩略图请求。"""
        self._accept_requests = True

    def shutdown(self) -> None:
        """停止接受新缩略图请求并取消当前代次任务。"""
        self._accept_requests = False
        self.clear()

    def pending_count(self) -> int:
        """返回运行中与线程池排队中的缩略图任务总数。"""
        return len(self._tasks)

    def prefetch(self, paths) -> tuple[Path, ...]:
        """在队列上限内预热一组缩略图并返回实际跟踪的路径。"""
        tracked = []
        for path in dict.fromkeys(Path(path) for path in paths):
            self.get(path)
            request_key = (self._generation, path)
            if (
                path in self._cache
                or request_key in self._path_to_task
                or request_key in self._deferred_requests
            ):
                tracked.append(path)
        return tuple(tracked)

    def paths(self) -> tuple[Path, ...]:
        """返回当前缓存路径，供容量测试和诊断使用。"""
        return tuple(self._cache.keys())

    def __len__(self) -> int:
        """返回当前缓存项目数量。"""
        return len(self._cache)


class GroupListModel(QAbstractListModel):
    """以轻量 model/view 形式展示相似组摘要。"""

    def __init__(self, thumbnail_cache: ThumbnailCache, parent=None):
        """创建空组模型并复用页面级缩略图缓存。"""
        super().__init__(parent)
        self._all_groups: tuple[SimilarityGroup, ...] = ()
        self._groups: tuple[SimilarityGroup, ...] = ()
        self._filter: GroupType | None = None
        self._thumbnail_rows: dict[Path, tuple[int, ...]] = {}
        self._thumbnail_cache = thumbnail_cache
        self._thumbnail_cache.thumbnail_ready.connect(
            self._on_thumbnail_ready
        )

    @pyqtSlot(object)
    def _on_thumbnail_ready(self, path: Path) -> None:
        """缩略图完成后只刷新使用该代表图的可见组行。"""
        for row in self._thumbnail_rows.get(Path(path), ()):
            index = self.index(row, 0)
            self.dataChanged.emit(
                index,
                index,
                [Qt.ItemDataRole.DecorationRole],
            )

    def rowCount(self, parent=QModelIndex()) -> int:
        """返回当前筛选后的相似组数量。"""
        if parent.isValid():
            return 0
        return len(self._groups)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """按显示、装饰和业务角色返回组摘要数据。"""
        if not index.isValid() or not 0 <= index.row() < len(self._groups):
            return None
        group = self._groups[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                f"{GROUP_LABELS[group.group_type]} · {len(group.members)} 张\n"
                f"总大小 {format_size(group.total_size_bytes)}"
            )
        if role == Qt.ItemDataRole.DecorationRole:
            return self._thumbnail_cache.get(group.representative.record.path)
        if role == Qt.ItemDataRole.UserRole:
            return group
        return None

    def set_groups(
        self,
        groups: tuple[SimilarityGroup, ...],
        group_filter: GroupType | None = None,
    ) -> None:
        """替换正式组结果并应用类别筛选。"""
        self.beginResetModel()
        self._all_groups = tuple(groups)
        self._filter = group_filter
        self._groups = tuple(
            group
            for group in self._all_groups
            if group_filter is None or group.group_type == group_filter
        )
        thumbnail_rows: dict[Path, list[int]] = {}
        for row, group in enumerate(self._groups):
            thumbnail_rows.setdefault(
                group.representative.record.path,
                [],
            ).append(row)
        self._thumbnail_rows = {
            path: tuple(rows) for path, rows in thumbnail_rows.items()
        }
        self.endResetModel()

    def apply_filter(self, group_filter: GroupType | None) -> None:
        """在不改变原始结果的情况下切换组类别筛选。"""
        self.set_groups(self._all_groups, group_filter)

    def group_at(self, row: int) -> SimilarityGroup | None:
        """返回指定可见行对应的相似组。"""
        if 0 <= row < len(self._groups):
            return self._groups[row]
        return None


class ImageTableModel(QAbstractTableModel):
    """展示当前组图片，并维护只由用户动作改变的勾选集合。"""

    selection_changed = pyqtSignal()
    COLUMNS = ("选择", "缩略图", "文件名", "完整路径", "尺寸", "格式", "大小", "关系")

    def __init__(
        self,
        selected_paths: set[Path],
        thumbnail_cache: ThumbnailCache,
        parent=None,
    ):
        """绑定页面级选择集合和有界缩略图缓存。"""
        super().__init__(parent)
        self.selected_paths = selected_paths
        self.thumbnail_cache = thumbnail_cache
        self.group: SimilarityGroup | None = None
        self._duplicate_hashes: set[str] = set()
        self._thumbnail_rows: dict[Path, tuple[int, ...]] = {}
        self.thumbnail_cache.thumbnail_ready.connect(
            self._on_thumbnail_ready
        )

    @pyqtSlot(object)
    def _on_thumbnail_ready(self, path: Path) -> None:
        """缩略图完成后刷新当前组中对应图片的装饰角色。"""
        for row in self._thumbnail_rows.get(Path(path), ()):
            index = self.index(row, 1)
            self.dataChanged.emit(
                index,
                index,
                [Qt.ItemDataRole.DecorationRole],
            )

    def set_group(self, group: SimilarityGroup | None) -> None:
        """切换右侧明细所展示的当前相似组。"""
        self.beginResetModel()
        self.group = group
        hash_counts = Counter(
            member.sha256
            for member in group.members
            if member.sha256 is not None
        ) if group is not None else Counter()
        self._duplicate_hashes = {
            sha256 for sha256, count in hash_counts.items() if count > 1
        }
        thumbnail_rows: dict[Path, list[int]] = {}
        if group is not None:
            for row, member in enumerate(group.members):
                thumbnail_rows.setdefault(member.record.path, []).append(row)
        self._thumbnail_rows = {
            path: tuple(rows) for path, rows in thumbnail_rows.items()
        }
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        """返回当前组图片数量。"""
        if parent.isValid() or self.group is None:
            return 0
        return len(self.group.members)

    def columnCount(self, parent=QModelIndex()) -> int:
        """返回明细表固定列数量。"""
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """为水平表头返回中文列名。"""
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.COLUMNS)
        ):
            return self.COLUMNS[section]
        return super().headerData(section, orientation, role)

    def _member(self, row: int) -> ImageFingerprint | None:
        """返回指定明细行的指纹对象。"""
        if self.group is None or not 0 <= row < len(self.group.members):
            return None
        return self.group.members[row]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """按角色返回复选状态、缩略图、元数据或业务对象。"""
        if not index.isValid():
            return None
        member = self._member(index.row())
        if member is None:
            return None
        record = member.record

        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return (
                Qt.CheckState.Checked
                if record.path in self.selected_paths
                else Qt.CheckState.Unchecked
            )
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 1:
            return self.thumbnail_cache.get(record.path)
        if role == Qt.ItemDataRole.UserRole:
            return member
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        suggested = self.group is not None and member == self.group.suggested_keep
        values = (
            "",
            "",
            f"{record.path.name}{'  [建议保留]' if suggested else ''}",
            str(record.path),
            f"{record.width} × {record.height}",
            record.image_format,
            format_size(record.size_bytes),
            self._relation_label(member),
        )
        return values[index.column()]

    def _relation_label(self, member: ImageFingerprint) -> str:
        """返回成员相对当前组代表图的可解释关系。"""
        if self.group is None:
            return ""
        if member.sha256 is not None and member.sha256 in self._duplicate_hashes:
            return "完全重复"
        return "视觉相似"

    def flags(self, index):
        """仅让第一列具有用户可勾选能力。"""
        base_flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            return base_flags | Qt.ItemFlag.ItemIsUserCheckable
        return base_flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        """响应用户复选动作并同步页面级选择集合。"""
        if (
            role != Qt.ItemDataRole.CheckStateRole
            or not index.isValid()
            or index.column() != 0
        ):
            return False
        member = self._member(index.row())
        if member is None:
            return False
        checked = value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value
        if checked:
            self.selected_paths.add(member.record.path)
        else:
            self.selected_paths.discard(member.record.path)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.selection_changed.emit()
        return True

    def check_state(self, row: int) -> Qt.CheckState:
        """返回指定行的复选状态，供测试和辅助操作使用。"""
        index = self.index(row, 0)
        value = self.data(index, Qt.ItemDataRole.CheckStateRole)
        return value or Qt.CheckState.Unchecked

    def select_all_except_suggested(self) -> None:
        """在用户主动请求时选中当前组除建议保留项外的成员。"""
        if self.group is None:
            return
        self.selected_paths.discard(self.group.suggested_keep.record.path)
        for member in self.group.members:
            if member != self.group.suggested_keep:
                self.selected_paths.add(member.record.path)
        if self.rowCount() > 0:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, 0),
                [Qt.ItemDataRole.CheckStateRole],
            )
        self.selection_changed.emit()


class TrashConfirmationDialog(QDialog):
    """展示数量、大小和完整路径，并让取消成为默认动作。"""

    def __init__(
        self,
        paths: tuple[Path, ...] | list[Path],
        total_size_bytes: int,
        parent=None,
    ):
        """构建不允许回车误触确认按钮的二次确认窗口。"""
        super().__init__(parent)
        self.setObjectName("trashConfirmationDialog")
        normalized_paths = tuple(Path(path) for path in paths)
        self.setWindowTitle("确认移入系统回收站")
        self.resize(680, 460)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel(
            f"已选择 {len(normalized_paths)} 个文件，总大小 {format_size(total_size_bytes)}"
        )
        layout.addWidget(self.summary_label)

        self.notice_label = QLabel(
            "文件将移入系统回收站，不会永久删除。\n"
            "只有清空回收站后才会真正释放相应空间。"
        )
        self.notice_label.setWordWrap(True)
        layout.addWidget(self.notice_label)

        layout.addWidget(QLabel("请再次核对完整路径："))
        self.paths_view = QPlainTextEdit()
        self.paths_view.setReadOnly(True)
        self.paths_view.setPlainText("\n".join(str(path) for path in normalized_paths))
        layout.addWidget(self.paths_view, 1)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)
        self.confirm_button = QPushButton("移入回收站")
        self.confirm_button.setDefault(False)
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.accept)
        button_layout.addWidget(self.confirm_button)
        layout.addLayout(button_layout)


class RecycleWorker(QObject):
    """在线程中逐项调用回收站服务，避免阻塞 Qt 主线程。"""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, service: RecycleBinService, paths: tuple[Path, ...]):
        """保存回收站服务和用户已确认的路径快照。"""
        super().__init__()
        self.service = service
        self.paths = paths

    @pyqtSlot()
    def run(self) -> None:
        """执行逐项处理并通过信号返回结构化结果。"""
        try:
            self.completed.emit(self.service.move_items(self.paths))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


def _rebuild_result_without_paths(
    result: ScanResult,
    removed_paths: set[Path],
    cancelled: Callable[[], bool] | None = None,
) -> ScanResult:
    """移除成功项并按原预设重新计算剩余图片关系。"""
    fingerprints = tuple(
        fingerprint
        for fingerprint in result.fingerprints
        if fingerprint.record.path not in removed_paths
    )
    return replace(
        result,
        groups=build_similarity_groups(
            fingerprints,
            result.preset,
            cancelled=cancelled,
        ),
        fingerprints=fingerprints,
    )


class RegroupWorker(QObject):
    """在独立线程中重建回收成功后的相似组关系。"""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, result: ScanResult, removed_paths: set[Path]):
        """快照本次正式结果和已经进入回收站的路径。"""
        super().__init__()
        self.result = result
        self.removed_paths = set(removed_paths)
        self.cancel_event = threading.Event()

    @pyqtSlot()
    def run(self) -> None:
        """执行可协作取消的纯数据重分组。"""
        try:
            updated = _rebuild_result_without_paths(
                self.result,
                self.removed_paths,
                self.cancel_event.is_set,
            )
        except GroupingCancelled:
            pass
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.completed.emit(updated)
        finally:
            self.finished.emit()

    @pyqtSlot()
    def cancel(self) -> None:
        """请求重分组在下一个计算边界安全停止。"""
        self.cancel_event.set()


class ImageSimilarityTool(QWidget):
    """组装图片扫描、相似组浏览和回收站确认的第 5 个工具页。"""

    workers_idle = pyqtSignal()

    def __init__(self):
        """构建透明滚动页、两个悬浮容器和 model/view 结果区。"""
        super().__init__()
        self.setObjectName("imageSimilarityRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            "QWidget#imageSimilarityRoot { background: transparent; }"
        )

        self.current_result: ScanResult | None = None
        self.selected_paths: set[Path] = set()
        self.thumbnail_cache = ThumbnailCache(capacity=128)
        self.thumbnail_cache.tasks_idle.connect(self._notify_workers_idle)
        self.thumbnail_cache.thumbnail_finished.connect(
            self._on_thumbnail_finished
        )
        self._thumbnail_prewarm_paths: set[Path] = set()
        self._thumbnail_prewarm_total = 0
        self.scan_thread: QThread | None = None
        self.scan_worker: ImageScanWorker | None = None
        self.recycle_thread: QThread | None = None
        self.recycle_worker: RecycleWorker | None = None
        self.regroup_thread: QThread | None = None
        self.regroup_worker: RegroupWorker | None = None
        self.last_recycle_summary = ""
        self._shutdown_requested = False
        self._standalone_close_pending = False
        self.workers_idle.connect(self._resume_standalone_close)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("imageSimilarityScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.scroll_area.setAutoFillBackground(False)
        self.scroll_area.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self.scroll_area.viewport().setAutoFillBackground(False)
        self.scroll_area.setStyleSheet(
            "QScrollArea#imageSimilarityScroll { background: transparent; border: none; }"
        )
        root_layout.addWidget(self.scroll_area)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("imageSimilarityContent")
        self.scroll_content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.scroll_content.setAutoFillBackground(False)
        self.scroll_content.setStyleSheet(
            "QWidget#imageSimilarityContent { background: transparent; }"
        )
        self.scroll_area.setWidget(self.scroll_content)
        content_layout = QVBoxLayout(self.scroll_content)
        content_layout.setContentsMargins(20, 16, 20, 24)
        content_layout.setSpacing(16)

        self._build_scan_panel(content_layout)
        self._build_results_panel(content_layout)
        self._update_selection_summary()

    def _build_scan_panel(self, parent_layout: QVBoxLayout) -> None:
        """构建目录、递归、预设、命令和分阶段进度控件。"""
        self.scan_panel = QFrame()
        self.scan_panel.setObjectName("container")
        apply_shadow(self.scan_panel)
        layout = QVBoxLayout(self.scan_panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("图片相似度检测")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        directory_layout = QHBoxLayout()
        directory_layout.addWidget(QLabel("扫描目录："))
        self.directory_entry = QLineEdit()
        self.directory_entry.setReadOnly(True)
        self.directory_entry.setPlaceholderText("请选择一个图片根目录")
        directory_layout.addWidget(self.directory_entry, 1)
        self.choose_directory_button = QPushButton("选择文件夹")
        self.choose_directory_button.clicked.connect(self.choose_directory)
        directory_layout.addWidget(self.choose_directory_button)
        layout.addLayout(directory_layout)

        options_layout = QHBoxLayout()
        self.recursive_checkbox = QCheckBox("包含子文件夹")
        self.recursive_checkbox.setChecked(True)
        options_layout.addWidget(self.recursive_checkbox)
        options_layout.addSpacing(18)
        options_layout.addWidget(QLabel("相似度："))
        self.preset_group = QButtonGroup(self)
        self.strict_radio = QRadioButton("严格")
        self.standard_radio = QRadioButton("标准")
        self.loose_radio = QRadioButton("宽松")
        self.strict_radio.setChecked(True)
        for button in (self.strict_radio, self.standard_radio, self.loose_radio):
            self.preset_group.addButton(button)
            options_layout.addWidget(button)
        options_layout.addStretch()
        self.start_button = QPushButton("开始扫描")
        self.start_button.clicked.connect(self.start_scan)
        options_layout.addWidget(self.start_button)
        self.cancel_button = QPushButton("取消扫描")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_scan)
        options_layout.addWidget(self.cancel_button)
        layout.addLayout(options_layout)

        progress_layout = QHBoxLayout()
        self.phase_label = QLabel("准备就绪")
        progress_layout.addWidget(self.phase_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar, 1)
        self.progress_count_label = QLabel("0 / 0 · 失败 0")
        progress_layout.addWidget(self.progress_count_label)
        layout.addLayout(progress_layout)
        parent_layout.addWidget(self.scan_panel)

    def _build_results_panel(self, parent_layout: QVBoxLayout) -> None:
        """构建组筛选、图片明细、路径操作和回收站命令区。"""
        self.results_panel = QFrame()
        self.results_panel.setObjectName("container")
        apply_shadow(self.results_panel)
        layout = QVBoxLayout(self.results_panel)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("结果筛选："))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部", None)
        self.filter_combo.addItem("完全重复", GroupType.EXACT)
        self.filter_combo.addItem("视觉相似", GroupType.VISUAL)
        self.filter_combo.currentIndexChanged.connect(self.apply_group_filter)
        toolbar.addWidget(self.filter_combo)
        self.select_others_button = QPushButton("保留建议项并选择本组其他图片")
        self.select_others_button.clicked.connect(self.select_group_others)
        self.select_others_button.setEnabled(False)
        toolbar.addWidget(self.select_others_button)
        toolbar.addStretch()
        self.open_image_button = QPushButton("打开图片")
        self.open_image_button.clicked.connect(self.open_current_image)
        toolbar.addWidget(self.open_image_button)
        self.open_folder_button = QPushButton("打开所在文件夹")
        self.open_folder_button.clicked.connect(self.open_current_folder)
        toolbar.addWidget(self.open_folder_button)
        self.copy_path_button = QPushButton("复制完整路径")
        self.copy_path_button.clicked.connect(self.copy_current_path)
        toolbar.addWidget(self.copy_path_button)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.group_view = QListView()
        self.group_view.setObjectName("similarityGroupView")
        self.group_view.setMinimumWidth(210)
        self.group_view.setIconSize(QSize(72, 54))
        self.group_model = GroupListModel(self.thumbnail_cache, self)
        self.group_view.setModel(self.group_model)
        self.group_view.selectionModel().currentChanged.connect(
            self._on_group_changed
        )
        splitter.addWidget(self.group_view)

        self.image_view = QTableView()
        self.image_view.setObjectName("similarityImageView")
        self.image_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.image_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.image_view.setIconSize(QSize(88, 64))
        self.image_view.verticalHeader().setDefaultSectionSize(70)
        self.image_model = ImageTableModel(
            self.selected_paths,
            self.thumbnail_cache,
            self,
        )
        self.image_model.selection_changed.connect(self._update_selection_summary)
        self.image_view.setModel(self.image_model)
        self.image_view.doubleClicked.connect(self.open_current_image)
        image_header = self.image_view.horizontalHeader()
        image_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        image_header.setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        image_header.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Fixed,
        )
        image_header.resizeSection(1, 104)
        image_header.setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.Stretch,
        )
        splitter.addWidget(self.image_view)
        splitter.setSizes([240, 650])
        layout.addWidget(splitter, 1)

        recycle_details_header = QHBoxLayout()
        self.recycle_details_label = QLabel("最近一次回收站处理明细")
        self.recycle_details_label.setVisible(False)
        recycle_details_header.addWidget(self.recycle_details_label)
        recycle_details_header.addStretch()
        self.copy_recycle_summary_button = QPushButton("复制处理明细")
        self.copy_recycle_summary_button.setEnabled(False)
        self.copy_recycle_summary_button.setVisible(False)
        self.copy_recycle_summary_button.clicked.connect(
            self.copy_recycle_summary
        )
        recycle_details_header.addWidget(self.copy_recycle_summary_button)
        layout.addLayout(recycle_details_header)
        self.recycle_details_view = QPlainTextEdit()
        self.recycle_details_view.setReadOnly(True)
        self.recycle_details_view.setMaximumHeight(110)
        self.recycle_details_view.setVisible(False)
        layout.addWidget(self.recycle_details_view)

        bottom = QHBoxLayout()
        self.result_summary_label = QLabel("尚未扫描")
        bottom.addWidget(self.result_summary_label)
        bottom.addStretch()
        self.selection_summary_label = QLabel("已选 0 张 · 0 B")
        bottom.addWidget(self.selection_summary_label)
        self.trash_button = QPushButton("移入回收站")
        self.trash_button.setEnabled(False)
        self.trash_button.clicked.connect(self.confirm_move_to_trash)
        bottom.addWidget(self.trash_button)
        layout.addLayout(bottom)
        parent_layout.addWidget(self.results_panel, 1)

    def choose_directory(self) -> None:
        """让用户选择单个图片根目录并更新只读输入框。"""
        selected = QFileDialog.getExistingDirectory(self, "选择图片根目录")
        if selected:
            self.directory_entry.setText(selected)

    def _selected_preset(self) -> SimilarityPreset:
        """把三枚单选按钮映射为受控相似度预设。"""
        if self.loose_radio.isChecked():
            return SimilarityPreset.LOOSE
        if self.standard_radio.isChecked():
            return SimilarityPreset.STANDARD
        return SimilarityPreset.STRICT

    def start_scan(self) -> None:
        """校验目录并把扫描任务移动到独立 QThread。"""
        if (
            self.scan_thread is not None
            or self.recycle_thread is not None
            or self.regroup_thread is not None
        ):
            return
        directory = self.directory_entry.text().strip()
        if not directory:
            QMessageBox.warning(self, "提示", "请先选择图片根目录。")
            return

        root = Path(directory)
        if not root.exists() or not root.is_dir():
            QMessageBox.warning(self, "提示", "所选扫描目录不存在或不是目录。")
            return

        self.selected_paths.clear()
        self.thumbnail_cache.resume()
        self.thumbnail_cache.clear()
        self.group_model.set_groups(())
        self.image_model.set_group(None)
        self._update_selection_summary()
        self.start_button.setEnabled(False)
        self.choose_directory_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.phase_label.setText("准备扫描")

        self.scan_thread = QThread(self)
        self.scan_worker = ImageScanWorker(
            root=root,
            recursive=self.recursive_checkbox.isChecked(),
            preset=self._selected_preset(),
        )
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.progress_changed.connect(self._on_scan_progress)
        self.scan_worker.completed.connect(self._on_scan_completed)
        self.scan_worker.failed.connect(self._on_scan_failed)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.finished.connect(self.scan_worker.deleteLater)
        self.scan_thread.finished.connect(self._on_scan_thread_finished)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)
        self.scan_thread.start()

    def cancel_scan(self) -> None:
        """请求当前 worker 协作式取消并更新可见状态。"""
        if self.scan_worker is not None:
            self.scan_worker.cancel()
            self.cancel_button.setEnabled(False)
            self.phase_label.setText("正在安全取消…")

    @pyqtSlot(object)
    def _on_scan_progress(self, progress: ScanProgress) -> None:
        """把结构化进度转换为阶段文字、百分比和计数。"""
        self.phase_label.setText(PHASE_LABELS.get(progress.phase, "处理中"))
        self.progress_bar.setValue(progress.percentage)
        self.progress_count_label.setText(
            f"{progress.processed} / {progress.total} · 失败 {progress.failures}"
        )

    @pyqtSlot(object)
    def _on_scan_completed(self, result: ScanResult) -> None:
        """仅在未取消时把正式纯数据结果交给 Qt 模型。"""
        if self._shutdown_requested:
            return
        if result.cancelled:
            self.current_result = None
            self.phase_label.setText("扫描已取消，未生成正式结果")
            self.result_summary_label.setText("扫描已取消")
            return
        self.show_scan_result(result)

    @pyqtSlot(str)
    def _on_scan_failed(self, message: str) -> None:
        """显示任务级异常，单图异常仍由结果失败列表承担。"""
        self.phase_label.setText("扫描失败")
        QMessageBox.critical(self, "扫描失败", message)

    @pyqtSlot()
    def _on_scan_thread_finished(self) -> None:
        """释放扫描线程引用并恢复页面命令状态。"""
        self.start_button.setEnabled(True)
        self.choose_directory_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.scan_worker = None
        self.scan_thread = None
        self._notify_workers_idle()

    def show_scan_result(self, result: ScanResult) -> None:
        """清空旧选择后展示一次正式扫描结果。"""
        self.current_result = result
        self.selected_paths.clear()
        self._thumbnail_prewarm_paths.clear()
        self._thumbnail_prewarm_total = 0
        self.thumbnail_cache.resume()
        self.thumbnail_cache.clear()
        self.filter_combo.blockSignals(True)
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.blockSignals(False)
        self.group_model.set_groups(result.groups)
        self.result_summary_label.setText(
            f"相似组 {len(result.groups)} 个 · 图片 {len(result.fingerprints)} 张 · "
            f"失败 {len(result.failures)}"
        )
        if self.group_model.rowCount() > 0:
            first_index = self.group_model.index(0, 0)
            self.group_view.setCurrentIndex(first_index)
            self.image_model.set_group(self.group_model.group_at(0))
        else:
            self.group_view.setCurrentIndex(QModelIndex())
            self.image_model.set_group(None)
        self._update_selection_summary()
        prewarm_paths = [
            group.representative.record.path
            for group in result.groups[:8]
        ]
        if result.groups:
            prewarm_paths.extend(
                member.record.path
                for member in result.groups[0].members[:8]
            )
        tracked_paths = self.thumbnail_cache.prefetch(prewarm_paths)
        self._thumbnail_prewarm_paths = set(tracked_paths)
        self._thumbnail_prewarm_total = len(tracked_paths)
        if tracked_paths:
            self.phase_label.setText("正在准备缩略图")
            self.progress_bar.setValue(0)
            self.progress_count_label.setText(
                f"0 / {len(tracked_paths)} · 失败 {len(result.failures)}"
            )
        else:
            self.phase_label.setText("扫描完成")
            self.progress_bar.setValue(100)

    @pyqtSlot(object)
    def _on_thumbnail_finished(self, path: Path) -> None:
        """用真实后台预热完成数更新缩略图阶段并在结束后标记扫描完成。"""
        path = Path(path)
        if path not in self._thumbnail_prewarm_paths:
            return
        self._thumbnail_prewarm_paths.discard(path)
        completed = self._thumbnail_prewarm_total - len(
            self._thumbnail_prewarm_paths
        )
        total = self._thumbnail_prewarm_total
        self.progress_bar.setValue(round(completed * 100 / total) if total else 100)
        failure_count = len(self.current_result.failures) if self.current_result else 0
        self.progress_count_label.setText(
            f"{completed} / {total} · 失败 {failure_count}"
        )
        if not self._thumbnail_prewarm_paths:
            self.phase_label.setText("扫描完成")

    def apply_group_filter(self) -> None:
        """应用全部、完全重复或视觉相似筛选并选择首组。"""
        group_filter = self.filter_combo.currentData()
        self.group_model.apply_filter(group_filter)
        if self.group_model.rowCount() > 0:
            index = self.group_model.index(0, 0)
            self.group_view.setCurrentIndex(index)
            self.image_model.set_group(self.group_model.group_at(0))
        else:
            self.image_model.set_group(None)

    def _on_group_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        """在左栏当前组变化后刷新右侧图片明细。"""
        del previous
        group = self.group_model.group_at(current.row()) if current.isValid() else None
        self.image_model.set_group(group)
        self.select_others_button.setEnabled(group is not None)

    def select_group_others(self) -> None:
        """响应用户按钮动作，选中当前组除建议项外的图片。"""
        self.image_model.select_all_except_suggested()

    def _update_selection_summary(self) -> None:
        """更新用户已选数量、总大小和回收站按钮状态。"""
        record_sizes = {}
        if self.current_result is not None:
            record_sizes = {
                fingerprint.record.path: fingerprint.record.size_bytes
                for fingerprint in self.current_result.fingerprints
            }
        total_size = sum(record_sizes.get(path, 0) for path in self.selected_paths)
        self.selection_summary_label.setText(
            f"已选 {len(self.selected_paths)} 张 · {format_size(total_size)}"
        )
        self.trash_button.setEnabled(
            bool(self.selected_paths)
            and self.scan_thread is None
            and self.recycle_thread is None
            and self.regroup_thread is None
        )

    def _current_fingerprint(self) -> ImageFingerprint | None:
        """返回右侧当前行对应的图片指纹。"""
        index = self.image_view.currentIndex()
        if not index.isValid():
            return None
        return self.image_model._member(index.row())

    def open_current_image(
        self,
        index: QModelIndex | bool | None = None,
    ) -> None:
        """使用系统默认查看器打开当前图片。"""
        if isinstance(index, QModelIndex) and index.isValid():
            self.image_view.setCurrentIndex(index)
        fingerprint = self._current_fingerprint()
        if fingerprint is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(fingerprint.record.path)))

    def open_current_folder(self) -> None:
        """使用系统文件管理器打开当前图片所在文件夹。"""
        fingerprint = self._current_fingerprint()
        if fingerprint is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(fingerprint.record.path.parent))
            )

    def copy_current_path(self) -> None:
        """把当前图片完整路径复制到系统剪贴板。"""
        fingerprint = self._current_fingerprint()
        if fingerprint is not None:
            QApplication.clipboard().setText(str(fingerprint.record.path))

    def confirm_move_to_trash(self) -> None:
        """展示二次确认，并只处理用户确认的路径快照。"""
        if (
            self.current_result is None
            or not self.selected_paths
            or self.scan_thread is not None
            or self.recycle_thread is not None
            or self.regroup_thread is not None
        ):
            return
        record_by_path = {
            fingerprint.record.path: fingerprint.record
            for fingerprint in self.current_result.fingerprints
        }
        selected = tuple(
            sorted(self.selected_paths, key=lambda path: str(path).casefold())
        )
        total_size = sum(record_by_path[path].size_bytes for path in selected)
        dialog = TrashConfirmationDialog(selected, total_size, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._start_recycle(selected)

    def _start_recycle(self, selected: tuple[Path, ...]) -> None:
        """把已确认的回收站批次放入独立 QThread。"""
        if (
            self.current_result is None
            or self.scan_thread is not None
            or self.recycle_thread is not None
            or self.regroup_thread is not None
        ):
            return
        try:
            service = RecycleBinService(
                self.current_result.root,
                self.current_result.records,
            )
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.critical(
                self,
                "无法启动回收站处理",
                f"扫描根目录已失效或无法安全复核：{error}",
            )
            return
        self.recycle_thread = QThread(self)
        self.recycle_worker = RecycleWorker(service, selected)
        self.recycle_worker.moveToThread(self.recycle_thread)
        self.recycle_thread.started.connect(self.recycle_worker.run)
        self.recycle_worker.completed.connect(self._on_recycle_completed)
        self.recycle_worker.failed.connect(self._on_recycle_failed)
        self.recycle_worker.finished.connect(self.recycle_thread.quit)
        self.recycle_worker.finished.connect(self.recycle_worker.deleteLater)
        self.recycle_thread.finished.connect(self._on_recycle_thread_finished)
        self.recycle_thread.finished.connect(self.recycle_thread.deleteLater)
        self.start_button.setEnabled(False)
        self.choose_directory_button.setEnabled(False)
        self.trash_button.setEnabled(False)
        self.recycle_thread.start()

    @pyqtSlot(object)
    def _on_recycle_completed(
        self,
        results: tuple[RecycleItemResult, ...],
    ) -> None:
        """移除成功项，保留失败项，并展示最近一次操作摘要。"""
        moved_paths = {
            result.path
            for result in results
            if result.status == RecycleStatus.MOVED_TO_TRASH
        }
        self.last_recycle_summary = "\n".join(
            f"{result.status.value}: {result.path} - {result.message}"
            for result in results
        )
        self.recycle_details_view.setPlainText(self.last_recycle_summary)
        has_details = bool(self.last_recycle_summary)
        self.recycle_details_label.setVisible(has_details)
        self.recycle_details_view.setVisible(has_details)
        self.copy_recycle_summary_button.setVisible(has_details)
        self.copy_recycle_summary_button.setEnabled(has_details)
        if self.current_result is not None and moved_paths:
            remaining_fingerprints = tuple(
                fingerprint
                for fingerprint in self.current_result.fingerprints
                if fingerprint.record.path not in moved_paths
            )
            self.current_result = replace(
                self.current_result,
                groups=(),
                fingerprints=remaining_fingerprints,
            )
            self.selected_paths.difference_update(moved_paths)
            self.group_model.set_groups(())
            self.group_view.setCurrentIndex(QModelIndex())
            self.image_model.set_group(None)
            self.select_others_button.setEnabled(False)
            self._update_selection_summary()
            if not self._shutdown_requested:
                self._start_regroup(self.current_result, set())
        else:
            self.selected_paths.difference_update(moved_paths)
            self._update_selection_summary()

        succeeded = sum(
            result.status == RecycleStatus.MOVED_TO_TRASH for result in results
        )
        failed = len(results) - succeeded
        QMessageBox.information(
            self,
            "回收站处理完成",
            f"成功 {succeeded} 个，失败或跳过 {failed} 个。\n"
            "失败项仍保留在结果中，可复制原因后重新扫描。",
        )

    def _result_without_paths(
        self,
        result: ScanResult,
        removed_paths: set[Path],
    ) -> ScanResult:
        """从当前会话移除成功项，并按原预设重新计算剩余图片关系。"""
        return _rebuild_result_without_paths(result, removed_paths)

    def _start_regroup(
        self,
        result: ScanResult,
        removed_paths: set[Path],
    ) -> None:
        """在独立 QThread 中更新回收成功后的正式结果。"""
        if self.regroup_thread is not None:
            return
        self.regroup_thread = QThread(self)
        self.regroup_worker = RegroupWorker(result, removed_paths)
        self.regroup_worker.moveToThread(self.regroup_thread)
        self.regroup_thread.started.connect(self.regroup_worker.run)
        self.regroup_worker.completed.connect(self._on_regroup_completed)
        self.regroup_worker.failed.connect(self._on_regroup_failed)
        self.regroup_worker.finished.connect(self.regroup_thread.quit)
        self.regroup_worker.finished.connect(self.regroup_worker.deleteLater)
        self.regroup_thread.finished.connect(self._on_regroup_thread_finished)
        self.regroup_thread.finished.connect(self.regroup_thread.deleteLater)
        self.start_button.setEnabled(False)
        self.choose_directory_button.setEnabled(False)
        self.trash_button.setEnabled(False)
        self.result_summary_label.setText("正在更新回收后的相似分组…")
        self.regroup_thread.start()

    @pyqtSlot(object)
    def _on_regroup_completed(self, result: ScanResult) -> None:
        """在主线程中一次性替换后台重建完成的纯数据结果。"""
        if not self._shutdown_requested:
            self.show_scan_result(result)

    @pyqtSlot(str)
    def _on_regroup_failed(self, message: str) -> None:
        """显示重分组失败并提示重新扫描，不回退任何文件操作。"""
        self.result_summary_label.setText("结果更新失败，请重新扫描")
        QMessageBox.critical(
            self,
            "结果更新失败",
            f"文件已按逐项结果处理，但相似组更新失败，请重新扫描。\n{message}",
        )

    @pyqtSlot()
    def _on_regroup_thread_finished(self) -> None:
        """释放重分组线程并在其他任务均空闲时恢复扫描命令。"""
        self.regroup_worker = None
        self.regroup_thread = None
        if self.scan_thread is None and self.recycle_thread is None:
            self.start_button.setEnabled(True)
            self.choose_directory_button.setEnabled(True)
        self._update_selection_summary()
        self._notify_workers_idle()

    def copy_recycle_summary(self) -> None:
        """把最近一次逐项回收站状态和原因复制到系统剪贴板。"""
        if self.last_recycle_summary:
            QApplication.clipboard().setText(self.last_recycle_summary)

    @pyqtSlot(str)
    def _on_recycle_failed(self, message: str) -> None:
        """显示任务级回收站异常且绝不降级为永久删除。"""
        QMessageBox.critical(
            self,
            "回收站处理失败",
            f"未执行永久删除。\n{message}",
        )

    @pyqtSlot()
    def _on_recycle_thread_finished(self) -> None:
        """释放回收站线程引用并恢复选择按钮状态。"""
        self.recycle_worker = None
        self.recycle_thread = None
        if self.scan_thread is None and self.regroup_thread is None:
            self.start_button.setEnabled(True)
            self.choose_directory_button.setEnabled(True)
        self._update_selection_summary()
        self._notify_workers_idle()

    def has_active_workers(self) -> bool:
        """返回扫描或回收线程是否仍处于启动、运行或收尾状态。"""
        return (
            self.scan_thread is not None
            or self.recycle_thread is not None
            or self.regroup_thread is not None
            or self.thumbnail_cache.has_active_tasks()
        )

    def request_shutdown(self) -> None:
        """请求可取消的扫描停止；回收任务则保持运行直到逐项处理完成。"""
        self._shutdown_requested = True
        if self.scan_worker is not None:
            self.scan_worker.cancel()
            self.cancel_button.setEnabled(False)
            self.phase_label.setText("正在安全取消，完成后关闭…")
        if self.regroup_worker is not None:
            self.regroup_worker.cancel()
        self.thumbnail_cache.shutdown()
        self._notify_workers_idle()

    def _notify_workers_idle(self) -> None:
        """当所有工作线程引用均已释放时通知主窗口继续关闭。"""
        if not self.has_active_workers():
            self.workers_idle.emit()

    @pyqtSlot()
    def _resume_standalone_close(self) -> None:
        """独立展示该页面时，在后台任务真正结束后重新提交关闭事件。"""
        if self._standalone_close_pending and not self.has_active_workers():
            self._standalone_close_pending = False
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:
        """独立关闭页面时等待后台任务协作结束，绝不强制销毁运行线程。"""
        if self.has_active_workers():
            self._standalone_close_pending = True
            self.request_shutdown()
            event.ignore()
            return
        self.thumbnail_cache.shutdown()
        super().closeEvent(event)
