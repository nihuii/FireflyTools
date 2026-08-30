import unittest
from unittest.mock import patch

import tools.video_crawler.sniffer as sniffer_module
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
    deduplicate_media_candidates,
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

    def test_detects_normalized_security_verification_titles(self):
        for title in (
            "Just a moment...",
            "Justamoment",
            "Checking your browser",
            "正在进行安全验证",
        ):
            with self.subTest(title=title):
                error = detect_access_limited_page(
                    PageAccessSnapshot(status_code=200, title=title)
                )

                self.assertIsNotNone(error)
                self.assertEqual(error.code, VideoErrorCode.HTTP_FORBIDDEN)


class PageSnifferAccessFlowTests(unittest.TestCase):
    def test_headless_sniff_raises_immediately_for_initial_403(self):
        page = ChallengeTransitionPage(clears=False, emits_hls=False)
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            with self.assertRaises(VideoDownloadError) as raised:
                PageSniffer(
                    options=SnifferOptions(headless=True)
                ).sniff("https://www.aowu.tv/w/example")

        self.assertEqual(raised.exception.code, VideoErrorCode.HTTP_FORBIDDEN)
        self.assertEqual(page.wait_calls, 0)

    def test_visible_sniff_waits_for_challenge_then_captures_hls(self):
        page = ChallengeTransitionPage()
        logs = []
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            with patch(
                "tools.video_crawler.sniffer.time.monotonic",
                return_value=100.0,
            ):
                report = PageSniffer(
                    log_callback=logs.append,
                    options=SnifferOptions(
                        headless=False,
                        manual_wait_seconds=10,
                    ),
                ).sniff("https://site.example/watch")

        self.assertEqual(report.best_candidate.url, page.hls_url)
        self.assertGreaterEqual(page.wait_calls, 2)
        self.assertTrue(any("安全验证已通过" in message for message in logs))

    def test_visible_sniff_tolerates_transient_snapshot_error_during_redirect(self):
        page = ChallengeTransitionPage(transient_title_failures=1)
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            with patch(
                "tools.video_crawler.sniffer.time.monotonic",
                return_value=100.0,
            ):
                report = PageSniffer(
                    options=SnifferOptions(
                        headless=False,
                        manual_wait_seconds=10,
                    )
                ).sniff("https://site.example/watch")

        self.assertEqual(report.best_candidate.url, page.hls_url)

    def test_visible_sniff_returns_empty_report_after_challenge_clears(self):
        page = ChallengeTransitionPage(clears=True, emits_hls=False)
        monotonic_values = iter([100.0, 100.0, 100.4, 101.1])
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            with patch(
                "tools.video_crawler.sniffer.time.monotonic",
                side_effect=lambda: next(monotonic_values),
            ):
                report = PageSniffer(
                    options=SnifferOptions(
                        headless=False,
                        manual_wait_seconds=1,
                    )
                ).sniff("https://site.example/watch")

        self.assertEqual(report.candidates, [])

    def test_visible_sniff_raises_when_challenge_reaches_deadline(self):
        page = ChallengeTransitionPage(clears=False, emits_hls=False)
        monotonic_values = iter([100.0, 100.0, 100.4, 101.1])
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            with patch(
                "tools.video_crawler.sniffer.time.monotonic",
                side_effect=lambda: next(monotonic_values),
            ):
                with self.assertRaises(VideoDownloadError) as raised:
                    PageSniffer(
                        options=SnifferOptions(
                            headless=False,
                            manual_wait_seconds=1,
                        )
                    ).sniff("https://site.example/watch")

        self.assertEqual(raised.exception.code, VideoErrorCode.HTTP_FORBIDDEN)
        self.assertGreater(page.wait_calls, 0)

    def test_sniff_waits_past_network_mp4_for_hls_and_deduplicates_candidates(self):
        page = SequencedMediaPage()
        logs = []
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            report = PageSniffer(
                log_callback=logs.append,
                options=SnifferOptions(manual_wait_seconds=10)
            ).sniff("https://site.example/watch")

        mp4_candidates = [
            candidate
            for candidate in report.candidates
            if candidate.kind == MediaKind.DIRECT_MP4
        ]
        hls_candidates = [
            candidate
            for candidate in report.candidates
            if candidate.kind == MediaKind.HLS
        ]
        self.assertEqual(page.wait_calls, 2)
        self.assertEqual(len(mp4_candidates), 1)
        self.assertEqual(mp4_candidates[0].source, "network")
        self.assertEqual(len(hls_candidates), 1)
        self.assertEqual(hls_candidates[0].source, "network")
        self.assertTrue(any("捕获网络 HLS" in message for message in logs))


class SnifferOptionsTests(unittest.TestCase):
    def test_default_options_are_headless_and_non_persistent(self):
        options = SnifferOptions()

        self.assertTrue(options.headless)
        self.assertFalse(options.use_persistent_profile)
        self.assertEqual(options.manual_wait_seconds, 25)
        self.assertFalse(options.visible)

    def test_profile_dir_defaults_to_workspace_relative_path(self):
        options = SnifferOptions(use_persistent_profile=True)

        self.assertIn("browser_profiles", options.profile_dir)
        self.assertIn("video_crawler", options.profile_dir)

    def test_default_options_use_bundled_chromium_profile(self):
        options = SnifferOptions()

        self.assertFalse(options.use_system_chrome)
        self.assertIsNone(options.browser_channel)
        self.assertEqual(options.active_profile_dir, options.profile_dir)

    def test_system_chrome_uses_channel_and_independent_profile(self):
        options = SnifferOptions(use_system_chrome=True)

        self.assertEqual(options.browser_channel, "chrome")
        self.assertIn("video_crawler_chrome", options.active_profile_dir)
        self.assertNotEqual(options.active_profile_dir, options.profile_dir)

    def test_visible_is_inverse_of_headless(self):
        options = SnifferOptions(headless=False)

        self.assertTrue(options.visible)


class PageSnifferOptionsWiringTests(unittest.TestCase):
    def test_sniffer_keeps_options(self):
        options = SnifferOptions(headless=False, use_persistent_profile=True)
        sniffer = PageSniffer(options=options)

        self.assertIs(sniffer.options, options)


class PageSnifferBrowserLaunchTests(unittest.TestCase):
    def test_default_browser_omits_channel_from_launch(self):
        playwright = RecordingLaunchPlaywright()

        browser, context = PageSniffer()._launch_context(playwright)

        self.assertNotIn("channel", playwright.chromium.launch_kwargs)
        self.assertIsNotNone(browser)
        self.assertIsNotNone(context)

    def test_nonpersistent_system_chrome_passes_channel_to_launch(self):
        playwright = RecordingLaunchPlaywright()
        sniffer = PageSniffer(options=SnifferOptions(use_system_chrome=True))

        browser, context = sniffer._launch_context(playwright)

        self.assertEqual(playwright.chromium.launch_kwargs["channel"], "chrome")
        self.assertIsNotNone(browser)
        self.assertIsNotNone(context)

    def test_persistent_system_chrome_uses_independent_profile(self):
        playwright = RecordingLaunchPlaywright()
        options = SnifferOptions(
            use_persistent_profile=True,
            use_system_chrome=True,
            system_chrome_profile_dir="./test-system-chrome-profile",
        )

        with patch("tools.video_crawler.sniffer.os.makedirs") as makedirs:
            browser, context = PageSniffer(options=options)._launch_context(playwright)

        makedirs.assert_called_once_with(options.active_profile_dir, exist_ok=True)
        self.assertIsNone(browser)
        self.assertIsNotNone(context)
        self.assertEqual(
            playwright.chromium.persistent_profile_dir,
            options.active_profile_dir,
        )
        self.assertEqual(
            playwright.chromium.persistent_kwargs["channel"],
            "chrome",
        )

    def test_system_chrome_launch_failure_logs_without_fallback(self):
        playwright = RecordingLaunchPlaywright(
            launch_error=RuntimeError("chrome executable missing")
        )
        logs = []
        sniffer = PageSniffer(
            log_callback=logs.append,
            options=SnifferOptions(use_system_chrome=True),
        )

        with self.assertRaisesRegex(RuntimeError, "executable missing"):
            sniffer._launch_context(playwright)

        self.assertEqual(len(playwright.chromium.launch_calls), 1)
        self.assertEqual(
            playwright.chromium.launch_calls[0]["channel"],
            "chrome",
        )
        self.assertTrue(any("系统 Chrome 启动失败" in message for message in logs))


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


class MediaCandidateDeduplicationTests(unittest.TestCase):
    def test_response_body_candidate_is_upgraded_by_matching_network_response(self):
        url = "https://cdn.example.test/ad.mp4"

        candidates = deduplicate_media_candidates(
            [
                MediaCandidate(
                    url=url,
                    kind=MediaKind.DIRECT_MP4,
                    source="response-body",
                    score=70,
                ),
                MediaCandidate(
                    url=url,
                    kind=MediaKind.DIRECT_MP4,
                    source="network",
                    score=75,
                    content_type="video/mp4",
                ),
            ]
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source, "network")
        self.assertEqual(candidates[0].score, 75)
        self.assertEqual(candidates[0].content_type, "video/mp4")


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

    def test_network_mp4_is_not_reliable_for_wait_stop(self):
        candidates = [
            MediaCandidate(
                url="https://cdn.example.test/ad.mp4",
                kind=MediaKind.DIRECT_MP4,
                source="network",
            )
        ]

        self.assertFalse(has_reliable_media_candidate(candidates))

    def test_network_hls_is_reliable_for_wait_stop(self):
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
    def __init__(self, page=None):
        self.chromium = FakeChromium(page)


class FakeChromium:
    def __init__(self, page=None):
        self.page = page

    def launch(self, **kwargs):
        return FakeBrowser(self.page)


class RecordingLaunchChromium:
    def __init__(self, launch_error=None):
        self.launch_kwargs = None
        self.launch_calls = []
        self.launch_error = launch_error
        self.persistent_profile_dir = None
        self.persistent_kwargs = None

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        self.launch_calls.append(kwargs)
        if self.launch_error:
            raise self.launch_error
        return FakeBrowser()

    def launch_persistent_context(self, profile_dir, **kwargs):
        self.persistent_profile_dir = profile_dir
        self.persistent_kwargs = kwargs
        return FakeContext()


class RecordingLaunchPlaywright:
    def __init__(self, launch_error=None):
        self.chromium = RecordingLaunchChromium(launch_error=launch_error)


class FakeBrowser:
    def __init__(self, page=None):
        self.context = FakeContext(page)
        self.closed = False

    def new_context(self, **kwargs):
        return self.context

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, page=None):
        self.page = page or FakePage()
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


class ChallengeTransitionPage(FakePage):
    url = "https://site.example/watch"

    def __init__(
        self,
        clears=True,
        emits_hls=True,
        transient_title_failures=0,
    ):
        super().__init__()
        self._title = "Just a moment..."
        self._status = 403
        self.clears = clears
        self.emits_hls = emits_hls
        self.wait_calls = 0
        self.response_callback = None
        self.hls_url = "https://cdn.example.test/main.m3u8"
        self.title_calls = 0
        self.transient_title_failures = transient_title_failures

    def on(self, event_name, callback):
        if event_name == "response":
            self.response_callback = callback

    def goto(self, page_url, **kwargs):
        return FakeMainResponse(status=403)

    def title(self):
        self.title_calls += 1
        if self.title_calls > 1 and self.transient_title_failures:
            self.transient_title_failures -= 1
            raise RuntimeError("execution context was destroyed")
        return self._title

    def locator(self, selector):
        if self._status == 200:
            return FakeMediaLocator()
        return FakeLocator()

    def wait_for_timeout(self, timeout):
        self.wait_calls += 1
        if self.clears and self.wait_calls == 1:
            self._status = 200
            self._title = "Regular Video Page"
        elif self.emits_hls and self._status == 200 and self.wait_calls == 2:
            self.response_callback(
                FakeObservedResponse(
                    self.hls_url,
                    "application/vnd.apple.mpegurl",
                    "media",
                )
            )


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


class FakeRequest:
    def __init__(self, resource_type):
        self.resource_type = resource_type
        self.headers = {}


class FakeObservedResponse:
    def __init__(self, url, content_type, resource_type, text=""):
        self.url = url
        self.headers = {"content-type": content_type}
        self.request = FakeRequest(resource_type)
        self._text = text

    def text(self):
        return self._text


class SequencedMediaPage:
    url = "https://site.example/watch"
    viewport_size = {"width": 1280, "height": 720}

    def __init__(self):
        self.mouse = FakeMouse()
        self.response_callback = None
        self.wait_calls = 0
        self.mp4_url = "https://cdn.example.test/short.mp4"
        self.hls_url = "https://cdn.example.test/main.m3u8"

    def on(self, event_name, callback):
        if event_name == "response":
            self.response_callback = callback

    def goto(self, page_url, **kwargs):
        self.response_callback(
            FakeObservedResponse(
                page_url,
                "application/json",
                "document",
                text=f'{{"preview":"{self.mp4_url}"}}',
            )
        )
        return FakeMainResponse(status=200)

    def title(self):
        return "Regular Video Page"

    def locator(self, selector):
        return FakeMediaLocator()

    def wait_for_timeout(self, timeout):
        self.wait_calls += 1
        if self.wait_calls == 1:
            self.response_callback(
                FakeObservedResponse(
                    self.mp4_url,
                    "video/mp4",
                    "media",
                )
            )
        elif self.wait_calls == 2:
            self.response_callback(
                FakeObservedResponse(
                    self.hls_url,
                    "application/vnd.apple.mpegurl",
                    "media",
                )
            )

    def evaluate(self, script):
        if "localStorage" in script:
            return {}
        if "navigator.userAgent" in script:
            return "Fake UA"
        return None


class FakeMediaLocator(FakeLocator):
    def count(self):
        return 1


class NavigationTimeoutMediaPage(SequencedMediaPage):
    def goto(self, page_url, **kwargs):
        raise TimeoutError("domcontentloaded timeout")


class PageSnifferNavigationRecoveryTests(unittest.TestCase):
    def test_navigation_timeout_still_waits_and_captures_delayed_hls(self):
        page = NavigationTimeoutMediaPage()
        logs = []
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            report = PageSniffer(
                log_callback=logs.append,
                options=SnifferOptions(manual_wait_seconds=10),
            ).sniff("https://site.example/watch")

        hls_candidates = [
            candidate
            for candidate in report.candidates
            if candidate.kind == MediaKind.HLS
        ]
        self.assertTrue(report.navigation_incomplete)
        self.assertEqual(len(hls_candidates), 1)
        self.assertEqual(hls_candidates[0].source, "network")
        self.assertEqual(page.wait_calls, 2)
        self.assertTrue(any("继续观察媒体请求" in message for message in logs))


class RecordingVideoLocator(FakeLocator):
    def __init__(self, count):
        self._count = count
        self.clicked = False

    def count(self):
        return self._count

    def click(self, **kwargs):
        self.clicked = True


class RecordingFrame:
    def __init__(self, video_count):
        self.video_locator = RecordingVideoLocator(video_count)

    def locator(self, selector):
        self.last_selector = selector
        return self.video_locator


class IframePlaybackPage:
    viewport_size = {"width": 1280, "height": 720}

    def __init__(self):
        self.top_frame = RecordingFrame(0)
        self.player_frame = RecordingFrame(1)
        self.frames = [self.top_frame, self.player_frame]
        self.mouse = FakeMouse()


class PlaybackTriggerTests(unittest.TestCase):
    def test_trigger_playback_clicks_video_inside_iframe(self):
        page = IframePlaybackPage()

        trigger = sniffer_module.trigger_playback(page)

        self.assertEqual(trigger, "frame-video")
        self.assertTrue(page.player_frame.video_locator.clicked)


class ResponseBodyReadPolicyTests(unittest.TestCase):
    def test_reads_small_json_response(self):
        self.assertTrue(
            sniffer_module.should_read_response_text("application/json", "4096")
        )

    def test_skips_non_text_response_before_calling_response_text(self):
        self.assertFalse(
            sniffer_module.should_read_response_text(
                "application/octet-stream",
                "4096",
            )
        )

    def test_skips_known_response_larger_than_limit(self):
        self.assertFalse(
            sniffer_module.should_read_response_text(
                "text/javascript",
                "1000001",
            )
        )

    def test_allows_text_response_with_unknown_size(self):
        self.assertTrue(
            sniffer_module.should_read_response_text("text/html", "")
        )


class MediaObservationDeadlineTests(unittest.TestCase):
    def test_observation_uses_monotonic_deadline(self):
        page = SequencedMediaPage()
        monotonic_values = iter([100.0, 100.0, 100.4, 101.1])

        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            with patch(
                "tools.video_crawler.sniffer.time.monotonic",
                side_effect=lambda: next(monotonic_values),
            ):
                report = PageSniffer(
                    options=SnifferOptions(manual_wait_seconds=1)
                ).sniff("https://site.example/watch")

        self.assertLessEqual(page.wait_calls, 2)
        self.assertIsInstance(report.candidates, list)


if __name__ == "__main__":
    unittest.main()
