from typing import Callable, Protocol

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import DiagnosticReport, MediaCandidate


class VideoAdapter(Protocol):
    name: str
    priority: int

    def can_handle(self, candidate: MediaCandidate) -> bool:
        ...

    def download(self, candidate: MediaCandidate, output_filename: str) -> str:
        ...

    def diagnose(self, url: str) -> DiagnosticReport:
        ...


class VideoDownloadOrchestrator:
    def __init__(
        self,
        adapters: list[VideoAdapter],
        candidate_resolver: Callable[[str], MediaCandidate],
    ):
        self.adapters = sorted(
            adapters,
            key=lambda adapter: adapter.priority,
            reverse=True,
        )
        self.candidate_resolver = candidate_resolver

    def select_adapter(self, candidate: MediaCandidate) -> VideoAdapter:
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
        candidate = self.candidate_resolver(url)
        adapter = self.select_adapter(candidate)
        return adapter.download(candidate, output_filename)
