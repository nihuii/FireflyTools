from tools.video_crawler.adapters.dash import parse_mpd_capabilities
from tools.video_crawler.models import DiagnosticReport, MediaCandidate, MediaKind


class VideoDiagnosticService:
    def __init__(self, sniffer=None):
        self.sniffer = sniffer

    def analyze_static_url(self, url: str) -> DiagnosticReport:
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
        report = self.analyze_static_url(url)
        if report.candidates or self.sniffer is None:
            return report
        return self.sniffer.sniff(url)
