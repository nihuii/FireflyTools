"""按 URL 类型执行静态分析或网页嗅探，并生成统一诊断报告。"""

from tools.video_crawler.adapters.dash import parse_mpd_capabilities
from tools.video_crawler.models import DiagnosticReport, MediaCandidate, MediaKind


class VideoDiagnosticService:
    """在静态 URL 分析和 Playwright 页面嗅探之间进行路由。"""
    def __init__(self, sniffer=None):
        """保存可选页面嗅探器；静态 URL 分析不需要浏览器。"""
        self.sniffer = sniffer

    def analyze_static_url(self, url: str) -> DiagnosticReport:
        """根据直链后缀生成无需联网嗅探的基础诊断报告。"""
        lower_url = url.lower()
        if lower_url.endswith(".mp4") or ".mp4?" in lower_url:
            return DiagnosticReport(
                source_url=url,
                candidates=[
                    MediaCandidate(
                        url=url,
                        kind=MediaKind.DIRECT_MP4,
                        source="direct-url",
                        score=100,
                    )
                ],
            )
        if lower_url.endswith(".m3u8") or ".m3u8?" in lower_url:
            return DiagnosticReport(
                source_url=url,
                candidates=[
                    MediaCandidate(
                        url=url,
                        kind=MediaKind.HLS,
                        source="direct-url",
                        score=100,
                    )
                ],
            )
        if lower_url.endswith(".mpd") or ".mpd?" in lower_url:
            return DiagnosticReport(
                source_url=url,
                candidates=[
                    MediaCandidate(
                        url=url,
                        kind=MediaKind.DASH,
                        source="direct-url",
                        score=70,
                    )
                ],
                warnings=["发现 DASH/MPD 候选流。"],
            )
        return DiagnosticReport(source_url=url)

    def analyze_mpd_text(self, url: str, mpd_text: str) -> DiagnosticReport:
        """解析 MPD 文本并报告 DASH 支持范围或 DRM 风险。"""
        capabilities = parse_mpd_capabilities(mpd_text)
        if capabilities.has_drm:
            return DiagnosticReport(
                source_url=url,
                candidates=[
                    MediaCandidate(
                        url=url,
                        kind=MediaKind.DRM,
                        source="mpd",
                        score=0,
                    )
                ],
                warnings=["发现 DRM 保护内容，本工具不会绕过 DRM。"],
            )
        return DiagnosticReport(
            source_url=url,
            candidates=[
                MediaCandidate(
                    url=url,
                    kind=MediaKind.DASH,
                    source="mpd",
                    score=70,
                )
            ],
        )

    def analyze(self, url: str) -> DiagnosticReport:
        """优先处理已知直链类型，其余 URL 交给页面嗅探器。"""
        report = self.analyze_static_url(url)
        if report.candidates or self.sniffer is None:
            return report
        return self.sniffer.sniff(url)
