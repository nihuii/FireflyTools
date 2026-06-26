import unittest

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    DiagnosticReport,
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


class VideoCrawlerModelTests(unittest.TestCase):
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
