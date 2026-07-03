"""定义下载适配器协议，并按优先级选择可处理候选的适配器。"""

from typing import Callable, Protocol

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import DiagnosticReport, MediaCandidate


class VideoAdapter(Protocol):
    """约束所有内置视频下载适配器必须实现的接口。"""
    name: str
    priority: int

    def can_handle(self, candidate: MediaCandidate) -> bool:
        """判断当前适配器是否支持给定媒体候选。"""
        ...

    def download(self, candidate: MediaCandidate, output_filename: str) -> str:
        """下载候选媒体并返回最终输出文件路径。"""
        ...

    def diagnose(self, url: str) -> DiagnosticReport:
        """返回候选媒体在当前适配器下的静态诊断报告。"""
        ...


class VideoDownloadOrchestrator:
    """按适配器优先级选择实现并执行下载。"""

    def __init__(
        self,
        adapters: list[VideoAdapter],
        candidate_resolver: Callable[[str], MediaCandidate],
    ):
        """保存候选解析器，并把适配器预先按优先级降序排列。

        排序只做一次，使每次下载都能稳定选择同一实现；新增适配器时只需
        声明 `priority` 和 `can_handle`，无需修改编排分支。
        """
        self.adapters = sorted(
            adapters,
            key=lambda adapter: adapter.priority,
            reverse=True,
        )
        self.candidate_resolver = candidate_resolver

    def select_adapter(self, candidate: MediaCandidate) -> VideoAdapter:
        """按优先级返回第一个声明可处理候选的适配器。"""
        for adapter in self.adapters:
            if adapter.can_handle(candidate):
                return adapter
        raise VideoDownloadError(
            VideoErrorCode.NO_MEDIA_FOUND,
            f"没有可处理该媒体类型的适配器: {candidate.kind.value}",
            details={"url": candidate.url, "kind": candidate.kind.value},
            retryable=False,
        )

    def download(self, url: str, output_filename: str) -> str:
        """选择可处理候选的最高优先级适配器并执行下载。"""
        candidate = self.candidate_resolver(url)
        adapter = self.select_adapter(candidate)
        return adapter.download(candidate, output_filename)
