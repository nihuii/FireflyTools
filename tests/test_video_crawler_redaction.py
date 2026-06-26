import unittest

from tools.video_crawler.logging_utils import redact_for_display
from tools.video_crawler.models import DiagnosticReport, MediaCandidate, MediaKind
from tools.video_crawler.reporting import format_diagnostic_report


class RedactionTests(unittest.TestCase):
    def test_redacts_sensitive_query_parameters_case_insensitively(self):
        text = (
            "https://cdn.example.test/video.mp4?"
            "token=secret&Authorization=BearerSecret&sig=abc"
        )

        redacted = redact_for_display(text)

        self.assertIn("token=<redacted>", redacted)
        self.assertIn("Authorization=<redacted>", redacted)
        self.assertIn("sig=<redacted>", redacted)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("BearerSecret", redacted)
        self.assertNotIn("sig=abc", redacted)

    def test_redacts_cookie_and_authorization_headers(self):
        text = "Cookie: sid=abc Authorization: Bearer secret X-Token: xyz"

        redacted = redact_for_display(text)

        self.assertIn("Cookie: <redacted>", redacted)
        self.assertIn("Authorization: <redacted>", redacted)
        self.assertIn("X-Token: <redacted>", redacted)
        self.assertNotIn("sid=abc", redacted)
        self.assertNotIn("Bearer secret", redacted)
        self.assertNotIn("xyz", redacted)


class DiagnosticReportRedactionTests(unittest.TestCase):
    def test_format_diagnostic_report_redacts_candidate_urls(self):
        report = DiagnosticReport(
            source_url="https://page.example.test/watch?token=page-secret",
            candidates=[
                MediaCandidate(
                    url="https://cdn.example.test/video.mp4?token=media-secret",
                    kind=MediaKind.DIRECT_MP4,
                    source="test",
                    score=100,
                )
            ],
            warnings=["Authorization: Bearer hidden"],
            errors=["sig=hidden-signature"],
        )

        summary = format_diagnostic_report(report)

        self.assertIn("token=<redacted>", summary)
        self.assertIn("Authorization: <redacted>", summary)
        self.assertIn("sig=<redacted>", summary)
        self.assertNotIn("page-secret", summary)
        self.assertNotIn("media-secret", summary)
        self.assertNotIn("Bearer hidden", summary)
        self.assertNotIn("hidden-signature", summary)


if __name__ == "__main__":
    unittest.main()
