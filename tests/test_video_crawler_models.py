from datetime import datetime, timezone
import unittest

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    DiagnosticReport,
    EdgeCaptureCandidate,
    MediaCandidate,
    MediaKind,
)


class VideoCrawlerErrorTests(unittest.TestCase):
    def test_error_keeps_code_message_retryable_and_details(self):
        error = VideoDownloadError(
            VideoErrorCode.HTTP_FORBIDDEN,
            "服务器拒绝访问",
            details={"status": 403},
            retryable=False,
        )

        self.assertEqual(error.code, VideoErrorCode.HTTP_FORBIDDEN)
        self.assertEqual(str(error), "服务器拒绝访问")
        self.assertEqual(error.details["status"], 403)
        self.assertFalse(error.retryable)

    def test_error_accepts_legacy_string_message(self):
        error = VideoDownloadError("嗅探失败，未能找到视频流")

        self.assertEqual(error.code, VideoErrorCode.UNKNOWN)
        self.assertEqual(str(error), "嗅探失败，未能找到视频流")

    def test_edge_candidate_error_codes_are_stable(self):
        self.assertEqual(
            VideoErrorCode.EDGE_CANDIDATE_INVALID.value,
            "EDGE_CANDIDATE_INVALID",
        )
        self.assertEqual(
            VideoErrorCode.EDGE_CANDIDATE_EXPIRED.value,
            "EDGE_CANDIDATE_EXPIRED",
        )


class VideoCrawlerModelTests(unittest.TestCase):
    def test_edge_capture_candidate_builds_safe_session_snapshot(self):
        candidate = EdgeCaptureCandidate(
            request_id="request-1",
            captured_at=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            page_url="https://example.test/watch/1",
            page_title="Example",
            media_url="https://cdn.example.test/master.m3u8",
            kind=MediaKind.HLS,
            content_type="application/vnd.apple.mpegurl",
            method="GET",
            headers={
                "Referer": "https://example.test/",
                "Origin": "https://example.test",
                "User-Agent": "Edge UA",
                "Accept-Language": "zh-CN",
            },
        )

        snapshot = candidate.to_session_snapshot()

        self.assertEqual(snapshot.user_agent, "Edge UA")
        self.assertEqual(snapshot.referer, "https://example.test/")
        self.assertEqual(snapshot.origin, "https://example.test")
        self.assertEqual(snapshot.cookies, ())
        self.assertEqual(snapshot.headers, {"Accept-Language": "zh-CN"})
        self.assertEqual(
            candidate.expires_at,
            datetime(2026, 8, 30, 12, 5, tzinfo=timezone.utc),
        )

    def test_diagnostic_report_navigation_is_complete_by_default(self):
        report = DiagnosticReport(source_url="https://site.example/watch")

        self.assertFalse(report.navigation_incomplete)

    def test_diagnostic_report_can_mark_navigation_incomplete(self):
        report = DiagnosticReport(
            source_url="https://site.example/watch",
            navigation_incomplete=True,
            warnings=["页面导航超时"],
        )

        self.assertTrue(report.navigation_incomplete)
        self.assertEqual(report.warnings, ["页面导航超时"])

    def test_diagnostic_report_summarizes_candidates(self):
        report = DiagnosticReport(
            source_url="https://example.test/watch",
            candidates=[
                MediaCandidate(
                    url="https://cdn.example.test/master.m3u8",
                    kind=MediaKind.HLS,
                    source="network",
                    score=80,
                    segment_count=120,
                )
            ],
            session=BrowserSessionSnapshot(user_agent="UA"),
        )

        self.assertTrue(report.has_downloadable_candidate)
        self.assertEqual(
            report.best_candidate.url,
            "https://cdn.example.test/master.m3u8",
        )
        self.assertIn("HLS", report.to_user_summary())


if __name__ == "__main__":
    unittest.main()
