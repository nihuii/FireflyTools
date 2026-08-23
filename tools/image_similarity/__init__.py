"""提供图片相似度扫描、分组和安全回收站处理的公共接口。"""

from tools.image_similarity.models import (
    GroupType,
    ImageFingerprint,
    ImageRecord,
    RecycleItemResult,
    RecycleStatus,
    ScanErrorCode,
    ScanFailure,
    ScanPhase,
    ScanProgress,
    ScanResult,
    SimilarityGroup,
    SimilarityPreset,
    SimilarityThresholds,
    thresholds_for,
)

__all__ = [
    "GroupType",
    "ImageFingerprint",
    "ImageRecord",
    "RecycleItemResult",
    "RecycleStatus",
    "ScanErrorCode",
    "ScanFailure",
    "ScanPhase",
    "ScanProgress",
    "ScanResult",
    "SimilarityGroup",
    "SimilarityPreset",
    "SimilarityThresholds",
    "thresholds_for",
]
