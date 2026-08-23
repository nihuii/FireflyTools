"""定义图片相似度功能使用的枚举、阈值和不可变数据模型。"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SimilarityPreset(Enum):
    """表示用户可选择的三档相似度预设。"""

    STRICT = "strict"
    STANDARD = "standard"
    LOOSE = "loose"


@dataclass(frozen=True)
class SimilarityThresholds:
    """集中保存一次视觉相似判断所需的全部阈值。"""

    phash_distance: int
    dhash_distance: int
    aspect_ratio_difference: float
    grayscale_similarity: float


PRESET_THRESHOLDS = {
    SimilarityPreset.STRICT: SimilarityThresholds(4, 4, 0.01, 0.94),
    SimilarityPreset.STANDARD: SimilarityThresholds(8, 8, 0.02, 0.90),
    SimilarityPreset.LOOSE: SimilarityThresholds(12, 10, 0.03, 0.86),
}


def thresholds_for(preset: SimilarityPreset) -> SimilarityThresholds:
    """返回预设的集中阈值，并拒绝 UI 之外的任意数值注入。"""
    if not isinstance(preset, SimilarityPreset):
        raise ValueError("相似度必须使用预定义档位")
    return PRESET_THRESHOLDS[preset]


class ScanPhase(Enum):
    """描述扫描任务当前所在的稳定阶段。"""

    ENUMERATING = "enumerating"
    FINGERPRINTING = "fingerprinting"
    HASHING = "hashing"
    GROUPING = "grouping"
    THUMBNAILS = "thumbnails"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ScanErrorCode(Enum):
    """为单张图片或目录扫描失败提供稳定错误码。"""

    PERMISSION_DENIED = "PERMISSION_DENIED"
    DECODE_FAILED = "DECODE_FAILED"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    FILE_DISAPPEARED = "FILE_DISAPPEARED"
    UNSUPPORTED_IMAGE = "UNSUPPORTED_IMAGE"
    SCAN_CANCELLED = "SCAN_CANCELLED"
    UNKNOWN_SCAN_ERROR = "UNKNOWN_SCAN_ERROR"


class GroupType(Enum):
    """区分内容完全重复组和视觉相似组。"""

    EXACT = "exact"
    VISUAL = "visual"


class RecycleStatus(Enum):
    """表示单个文件移入回收站后的结构化状态。"""

    MOVED_TO_TRASH = "MOVED_TO_TRASH"
    SKIPPED_CHANGED = "SKIPPED_CHANGED"
    SKIPPED_UNSAFE_PATH = "SKIPPED_UNSAFE_PATH"
    TRASH_OPERATION_FAILED = "TRASH_OPERATION_FAILED"


@dataclass(frozen=True)
class ImageRecord:
    """保存扫描时取得且供删除前复核的图片快照。"""

    path: Path
    canonical_path: Path
    size_bytes: int
    mtime_ns: int
    image_format: str
    width: int
    height: int
    order: int
    device_id: int | None = None
    file_id: int | None = None

    @property
    def dimensions(self) -> tuple[int, int]:
        """返回经过 EXIF 方向修正后的宽高。"""
        return self.width, self.height

    @property
    def pixel_count(self) -> int:
        """返回方向修正后的像素总数。"""
        return self.width * self.height

    @property
    def aspect_ratio(self) -> float:
        """返回宽高比，异常零高度快照按零处理。"""
        return self.width / self.height if self.height else 0.0


@dataclass(frozen=True)
class ImageFingerprint:
    """把一张图片的文件快照与混合指纹绑定在一起。"""

    record: ImageRecord
    phash: int
    dhash: int
    grayscale: bytes
    rgba: bytes = b""
    sha256: str | None = None


@dataclass(frozen=True)
class SimilarityGroup:
    """表示可展示的一组完全重复或视觉相似图片。"""

    group_id: str
    group_type: GroupType
    representative: ImageFingerprint
    members: tuple[ImageFingerprint, ...]
    suggested_keep: ImageFingerprint

    @property
    def total_size_bytes(self) -> int:
        """返回组内所有文件的总大小。"""
        return sum(member.record.size_bytes for member in self.members)


@dataclass(frozen=True)
class ScanFailure:
    """记录一个未中断整个批次的结构化扫描失败。"""

    path: Path
    code: ScanErrorCode
    message: str


@dataclass(frozen=True)
class ScanProgress:
    """向 UI 传递可节流的扫描阶段与计数。"""

    phase: ScanPhase
    processed: int
    total: int
    failures: int

    @property
    def percentage(self) -> int:
        """返回适合进度条显示的零到一百整数百分比。"""
        if self.total <= 0:
            return 0
        return min(100, max(0, round(self.processed * 100 / self.total)))


@dataclass(frozen=True)
class ScanResult:
    """保存一次正式扫描的纯数据结果。"""

    root: Path
    preset: SimilarityPreset
    groups: tuple[SimilarityGroup, ...]
    fingerprints: tuple[ImageFingerprint, ...]
    failures: tuple[ScanFailure, ...]
    cancelled: bool = False

    @property
    def records(self) -> tuple[ImageRecord, ...]:
        """返回正式结果包含的全部图片快照。"""
        return tuple(fingerprint.record for fingerprint in self.fingerprints)


@dataclass(frozen=True)
class RecycleItemResult:
    """保存单个路径的回收站处理状态和可显示原因。"""

    path: Path
    status: RecycleStatus
    message: str
