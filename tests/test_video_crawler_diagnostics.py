import unittest

from tools.video_crawler.diagnostics import VideoDiagnosticService
from tools.video_crawler.models import MediaKind
from tools.video_crawler.reporting import format_diagnostic_report
from tools.video_crawler.sniffer import (
    classify_media_response,
    extract_media_urls_from_text,
    merge_media_request_headers,
)


class VideoDiagnosticServiceTests(unittest.TestCase):
    def test_direct_mp4_url_reports_mp4_candidate(self):
        service = VideoDiagnosticService(sniffer=None)

        report = service.analyze_static_url("https://cdn.example.test/video.mp4?token=1")

        self.assertEqual(report.best_candidate.kind, MediaKind.DIRECT_MP4)
        self.assertTrue(report.has_downloadable_candidate)

    def test_direct_m3u8_url_reports_hls_candidate(self):
        service = VideoDiagnosticService(sniffer=None)

        report = service.analyze_static_url("https://cdn.example.test/master.m3u8")

        self.assertEqual(report.best_candidate.kind, MediaKind.HLS)

    def test_direct_mpd_url_reports_dash_candidate_with_warning(self):
        service = VideoDiagnosticService(sniffer=None)

        report = service.analyze_static_url("https://cdn.example.test/manifest.mpd")

        self.assertEqual(report.best_candidate.kind, MediaKind.DASH)
        self.assertIn("DASH", report.warnings[0])

    def test_mpd_text_with_content_protection_reports_drm_warning(self):
        service = VideoDiagnosticService(sniffer=None)
        mpd = """<?xml version="1.0"?>
        <MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
          <Period>
            <AdaptationSet mimeType="video/mp4">
              <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
            </AdaptationSet>
          </Period>
        </MPD>"""

        report = service.analyze_mpd_text("https://cdn.example.test/drm.mpd", mpd)

        self.assertEqual(report.best_candidate.kind, MediaKind.DRM)
        self.assertIn("DRM", report.warnings[0])

    def test_analyze_uses_sniffer_for_regular_web_page(self):
        class FakeSniffer:
            def __init__(self):
                self.seen_url = None

            def sniff(self, url):
                self.seen_url = url
                return VideoDiagnosticService(sniffer=None).analyze_static_url(
                    "https://cdn.example.test/video.mp4"
                )

        sniffer = FakeSniffer()
        service = VideoDiagnosticService(sniffer=sniffer)

        report = service.analyze("https://example.test/watch")

        self.assertEqual(sniffer.seen_url, "https://example.test/watch")
        self.assertEqual(report.best_candidate.kind, MediaKind.DIRECT_MP4)

    def test_reporting_formats_diagnostic_report(self):
        report = VideoDiagnosticService(sniffer=None).analyze_static_url(
            "https://cdn.example.test/video.mp4"
        )

        summary = format_diagnostic_report(report)

        self.assertIn("DIRECT_MP4", summary)


class PageSnifferClassificationTests(unittest.TestCase):
    def test_classifies_m3u8_response(self):
        candidate, warnings = classify_media_response(
            "https://cdn.example.test/master.m3u8",
            "application/vnd.apple.mpegurl",
        )

        self.assertEqual(candidate.kind, MediaKind.HLS)
        self.assertEqual(warnings, [])

    def test_classifies_mpd_response_with_warning(self):
        candidate, warnings = classify_media_response(
            "https://cdn.example.test/manifest.mpd",
            "application/dash+xml",
        )

        self.assertEqual(candidate.kind, MediaKind.DASH)
        self.assertIn("DASH", warnings[0])

    def test_classifies_drm_response_with_warning(self):
        candidate, warnings = classify_media_response(
            "https://license.example.test/widevine",
            "application/octet-stream",
        )

        self.assertEqual(candidate.kind, MediaKind.DRM)
        self.assertIn("DRM", warnings[0])


class PageSnifferSessionCaptureTests(unittest.TestCase):
    def test_merge_media_request_headers_keeps_latest_allowlisted_values(self):
        current = {"Accept": "video/*"}
        incoming = {
            "authorization": "Bearer fresh",
            "x-token": "edge",
            "cookie": "sid=hidden",
        }

        result = merge_media_request_headers(current, incoming)

        self.assertEqual(result["Accept"], "video/*")
        self.assertEqual(result["Authorization"], "Bearer fresh")
        self.assertEqual(result["X-Token"], "edge")
        self.assertNotIn("Cookie", result)


class MediaUrlExtractionTests(unittest.TestCase):
    def test_extracts_absolute_media_urls_from_json_text(self):
        text = '{"url":"https://cdn.example.test/path/master.m3u8?token=abc"}'

        urls = extract_media_urls_from_text("https://site.example/watch", text)

        self.assertEqual(
            urls,
            ["https://cdn.example.test/path/master.m3u8?token=abc"],
        )

    def test_extracts_relative_media_urls(self):
        text = 'window.source = "/video/stream.m3u8?ep=16";'

        urls = extract_media_urls_from_text("https://site.example/watch/page", text)

        self.assertEqual(urls, ["https://site.example/video/stream.m3u8?ep=16"])

    def test_deduplicates_urls_preserving_order(self):
        text = "https://cdn/a.m3u8 https://cdn/a.m3u8 https://cdn/b.mp4"

        urls = extract_media_urls_from_text("https://site.example", text)

        self.assertEqual(urls, ["https://cdn/a.m3u8", "https://cdn/b.mp4"])

    def test_ignores_escaped_regex_media_patterns(self):
        text = (
            r'const matcher = /\\.m3u8(?:\\?|$)/;'
            r'const fake = "https://www.aowu.tv/\\.m3u8";'
            r'const real = "https:\/\/cdn.example.test\/video\/master.m3u8?token=abc";'
        )

        urls = extract_media_urls_from_text("https://www.aowu.tv/watch", text)

        self.assertEqual(
            urls,
            ["https://cdn.example.test/video/master.m3u8?token=abc"],
        )


if __name__ == "__main__":
    unittest.main()
