from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MediaKind(str, Enum):
    DIRECT_MP4 = "DIRECT_MP4"
    HLS = "HLS"
    DASH = "DASH"
    DRM = "DRM"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MediaCandidate:
    url: str
    kind: MediaKind
    source: str
    score: int = 0
    content_type: str = ""
    segment_count: int | None = None
    bandwidth: int | None = None
    requires_session: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BrowserSessionSnapshot:
    user_agent: str = ""
    referer: str = ""
    origin: str = ""
    cookies: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    headers: dict[str, str] = field(default_factory=dict)
    local_storage: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiagnosticReport:
    source_url: str
    candidates: list[MediaCandidate] = field(default_factory=list)
    session: BrowserSessionSnapshot = field(default_factory=BrowserSessionSnapshot)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def best_candidate(self) -> MediaCandidate | None:
        if not self.candidates:
            return None
        return sorted(self.candidates, key=lambda item: item.score, reverse=True)[0]

    @property
    def has_downloadable_candidate(self) -> bool:
        candidate = self.best_candidate
        return candidate is not None and candidate.kind in {
            MediaKind.DIRECT_MP4,
            MediaKind.HLS,
            MediaKind.DASH,
        }

    def to_user_summary(self) -> str:
        lines = [f"诊断 URL: {self.source_url}"]
        if self.candidates:
            lines.append(f"发现候选流: {len(self.candidates)} 个")
            for candidate in self.candidates:
                segment_text = (
                    f"，切片数 {candidate.segment_count}"
                    if candidate.segment_count is not None
                    else ""
                )
                lines.append(
                    f"- {candidate.kind.value}: {candidate.url}{segment_text}"
                )
        else:
            lines.append("未发现可下载的 MP4/M3U8/MPD 候选流。")
        lines.extend(f"警告: {warning}" for warning in self.warnings)
        lines.extend(f"错误: {error}" for error in self.errors)
        return "\n".join(lines)
