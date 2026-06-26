import unittest

from tools.video_crawler.models import BrowserSessionSnapshot, DiagnosticReport
from tools.video_crawler.session import (
    build_download_headers,
    extract_download_request_headers,
    redact_sensitive_text,
)


class BrowserSessionTests(unittest.TestCase):
    def test_build_download_headers_merges_safe_session_values(self):
        snapshot = BrowserSessionSnapshot(
            user_agent="Browser UA",
            referer="https://example.test/watch",
            origin="https://example.test",
            cookies=({"name": "sid", "value": "abc", "domain": "example.test"},),
            headers={"Authorization": "Bearer secret", "Accept-Language": "zh-CN"},
        )

        headers = build_download_headers(
            base_headers={"User-Agent": "Base UA"},
            snapshot=snapshot,
            target_url="https://cdn.example.test/video.m3u8",
        )

        self.assertEqual(headers["User-Agent"], "Browser UA")
        self.assertEqual(headers["Referer"], "https://example.test/watch")
        self.assertEqual(headers["Origin"], "https://example.test")
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["Accept-Language"], "zh-CN")
        self.assertEqual(headers["Cookie"], "sid=abc")

    def test_redact_sensitive_text_hides_tokens(self):
        redacted = redact_sensitive_text(
            "Cookie: sid=abc Authorization: Bearer secret token=xyz"
        )

        self.assertIn("Cookie: <redacted>", redacted)
        self.assertIn("Authorization: <redacted>", redacted)
        self.assertIn("token=<redacted>", redacted)
        self.assertNotIn("sid=abc", redacted)
        self.assertNotIn("Bearer secret", redacted)
        self.assertNotIn("token=xyz", redacted)

    def test_diagnostic_summary_does_not_include_cookie_values(self):
        snapshot = BrowserSessionSnapshot(
            cookies=({"name": "sid", "value": "secret-cookie", "domain": "example.test"},)
        )

        summary = DiagnosticReport(
            source_url="https://example.test/watch",
            session=snapshot,
        ).to_user_summary()

        self.assertNotIn("secret-cookie", summary)


class DownloadRequestHeaderTests(unittest.TestCase):
    def test_extract_download_request_headers_keeps_allowlisted_auth_headers(self):
        raw_headers = {
            "authorization": "Bearer media-token",
            "x-token": "edge-token",
            "cookie": "sid=browser-cookie",
            "referer": "https://example.test/watch",
            "unrelated": "ignored",
        }

        headers = extract_download_request_headers(raw_headers)

        self.assertEqual(headers["Authorization"], "Bearer media-token")
        self.assertEqual(headers["X-Token"], "edge-token")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("unrelated", headers)

    def test_browser_session_snapshot_accepts_local_storage(self):
        snapshot = BrowserSessionSnapshot(
            local_storage={"player_token": "abc"},
            headers={"X-Token": "edge-token"},
        )

        self.assertEqual(snapshot.local_storage["player_token"], "abc")


if __name__ == "__main__":
    unittest.main()
