import unittest
from unittest.mock import patch

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import (
    MediaCandidate,
    MediaKind,
    PageAccessSnapshot,
    SnifferOptions,
)
from tools.video_crawler.sniffer import (
    PageSniffer,
    candidates_from_response_text,
    detect_access_limited_page,
    has_reliable_media_candidate,
    should_continue_waiting_for_media,
)


class PageAccessDiagnosticsTests(unittest.TestCase):
    def test_detects_http_403_as_forbidden(self):
        snapshot = PageAccessSnapshot(
            status_code=403,
            title="403 - 访问受限",
            final_url="https://www.aowu.tv/w/example",
            video_count=0,
            iframe_count=0,
        )

        error = detect_access_limited_page(snapshot)

        self.assertIsNotNone(error)
        self.assertEqual(error.code, VideoErrorCode.HTTP_FORBIDDEN)
        self.assertFalse(error.retryable)
        self.assertIn("访问受限", str(error))

    def test_allows_regular_empty_page_to_continue_no_media_flow(self):
        snapshot = PageAccessSnapshot(
            status_code=200,
            title="普通页面",
            final_url="https://example.test/watch",
            video_count=0,
            iframe_count=0,
        )

        self.assertIsNone(detect_access_limited_page(snapshot))


class PageSnifferAccessFlowTests(unittest.TestCase):
    def test_sniff_raises_forbidden_when_main_page_is_access_limited(self):
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright()),
        ):
            with self.assertRaises(VideoDownloadError) as raised:
                PageSniffer().sniff("https://www.aowu.tv/w/example")

        self.assertEqual(raised.exception.code, VideoErrorCode.HTTP_FORBIDDEN)


class SnifferOptionsTests(unittest.TestCase):
    def test_default_options_are_headless_and_non_persistent(self):
        options = SnifferOptions()

        self.assertTrue(options.headless)
        self.assertFalse(options.use_persistent_profile)
        self.assertEqual(options.manual_wait_seconds, 10)
        self.assertFalse(options.visible)

    def test_profile_dir_defaults_to_workspace_relative_path(self):
        options = SnifferOptions(use_persistent_profile=True)

        self.assertIn("browser_profiles", options.profile_dir)
        self.assertIn("video_crawler", options.profile_dir)

    def test_visible_is_inverse_of_headless(self):
        options = SnifferOptions(headless=False)

        self.assertTrue(options.visible)


class PageSnifferOptionsWiringTests(unittest.TestCase):
    def test_sniffer_keeps_options(self):
        options = SnifferOptions(headless=False, use_persistent_profile=True)
        sniffer = PageSniffer(options=options)

        self.assertIs(sniffer.options, options)


class ResponseTextCandidateTests(unittest.TestCase):
    def test_builds_candidates_from_json_response_text(self):
        candidates = candidates_from_response_text(
            base_url="https://site.example/api/play",
            content_type="application/json",
            text='{"play":"https://cdn.example.test/master.m3u8"}',
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, MediaKind.HLS)
        self.assertEqual(candidates[0].source, "response-body")


class MediaWaitPolicyTests(unittest.TestCase):
    def test_response_body_candidate_is_not_reliable_for_wait_stop(self):
        candidates = [
            MediaCandidate(
                url="https://cdn.example.test/index.m3u8",
                kind=MediaKind.HLS,
                source="response-body",
            )
        ]

        self.assertFalse(has_reliable_media_candidate(candidates))

    def test_network_candidate_is_reliable_for_wait_stop(self):
        candidates = [
            MediaCandidate(
                url="https://cdn.example.test/index.m3u8",
                kind=MediaKind.HLS,
                source="network",
            )
        ]

        self.assertTrue(has_reliable_media_candidate(candidates))

    def test_stops_waiting_when_candidate_exists(self):
        self.assertFalse(
            should_continue_waiting_for_media(
                candidate_count=1,
                elapsed_seconds=1,
                limit_seconds=30,
            )
        )

    def test_visible_mode_stops_after_reliable_candidate(self):
        self.assertFalse(
            should_continue_waiting_for_media(
                candidate_count=1,
                elapsed_seconds=1,
                limit_seconds=30,
                visible=True,
            )
        )

    def test_stops_waiting_after_limit(self):
        self.assertFalse(
            should_continue_waiting_for_media(
                candidate_count=0,
                elapsed_seconds=30,
                limit_seconds=30,
            )
        )

    def test_continues_waiting_before_limit_without_candidate(self):
        self.assertTrue(
            should_continue_waiting_for_media(
                candidate_count=0,
                elapsed_seconds=5,
                limit_seconds=30,
            )
        )


class FakePlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()


class FakeChromium:
    def launch(self, **kwargs):
        return FakeBrowser()


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()
        self.closed = False

    def new_context(self, **kwargs):
        return self.context

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.pages = []
        self.closed = False

    def new_page(self):
        return self.page

    def cookies(self):
        return ()

    def close(self):
        self.closed = True


class FakePage:
    url = "https://www.aowu.tv/w/example"
    viewport_size = {"width": 1280, "height": 720}

    def __init__(self):
        self.mouse = FakeMouse()

    def on(self, event_name, callback):
        return None

    def goto(self, page_url, **kwargs):
        return FakeMainResponse(status=403)

    def title(self):
        return "403 - 访问受限"

    def locator(self, selector):
        return FakeLocator()

    def wait_for_timeout(self, timeout):
        return None

    def evaluate(self, script):
        if "localStorage" in script:
            return {}
        if "navigator.userAgent" in script:
            return "Fake UA"
        return None


class FakeMainResponse:
    def __init__(self, status):
        self.status = status


class FakeLocator:
    @property
    def first(self):
        return self

    def count(self):
        return 0

    def click(self, **kwargs):
        return None


class FakeMouse:
    def click(self, x, y):
        return None


if __name__ == "__main__":
    unittest.main()
