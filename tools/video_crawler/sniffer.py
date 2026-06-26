from urllib.parse import urlparse

from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    DiagnosticReport,
    MediaCandidate,
    MediaKind,
)
from tools.video_crawler.session import extract_download_request_headers


AD_KEYWORDS = ("ad.", "/ad/", "adv", "blank", "preview", "v.admaster")


def merge_media_request_headers(
    current: dict[str, str],
    incoming: dict[str, str],
) -> dict[str, str]:
    merged = dict(current)
    merged.update(extract_download_request_headers(incoming))
    return merged


def classify_media_response(response_url: str, content_type: str = ""):
    lower_url = response_url.lower()
    lower_content_type = content_type.lower()
    if any(text in lower_url for text in AD_KEYWORDS):
        return None, []

    if ".mpd" in lower_url or "dash+xml" in lower_content_type:
        return (
            MediaCandidate(
                url=response_url,
                kind=MediaKind.DASH,
                source="network",
                score=65,
                content_type=content_type,
            ),
            ["发现 DASH/MPD 候选流。"],
        )
    if ".m3u8" in lower_url or "mpegurl" in lower_content_type:
        return (
            MediaCandidate(
                url=response_url,
                kind=MediaKind.HLS,
                source="network",
                score=80,
                content_type=content_type,
            ),
            [],
        )
    if ".mp4" in lower_url or "video/mp4" in lower_content_type:
        return (
            MediaCandidate(
                url=response_url,
                kind=MediaKind.DIRECT_MP4,
                source="network",
                score=75,
                content_type=content_type,
            ),
            [],
        )
    if (
        "widevine" in lower_url
        or "playready" in lower_url
        or "fairplay" in lower_url
    ):
        return (
            MediaCandidate(
                url=response_url,
                kind=MediaKind.DRM,
                source="network",
                score=0,
                content_type=content_type,
            ),
            ["发现疑似 DRM 请求；本工具不会绕过 DRM。"],
        )
    return None, []


class PageSniffer:
    def __init__(self, headers=None, log_callback=None):
        self.headers = headers or {}
        self.log_callback = log_callback

    def log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def sniff(self, page_url: str) -> DiagnosticReport:
        from playwright.sync_api import sync_playwright

        candidates: list[MediaCandidate] = []
        warnings: list[str] = []
        parsed_url = urlparse(page_url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}" if parsed_url.netloc else ""
        session = BrowserSessionSnapshot(
            user_agent=self.headers.get("User-Agent", ""),
            referer=page_url,
            origin=origin,
            headers=self.headers,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--mute-audio"])
            context = browser.new_context(extra_http_headers=self.headers)
            page = context.new_page()
            captured_headers = dict(self.headers)

            def handle_response(response):
                nonlocal captured_headers
                try:
                    content_type = response.headers.get("content-type", "")
                    candidate, candidate_warnings = classify_media_response(
                        response.url,
                        content_type,
                    )
                    if candidate is None:
                        return
                    captured_headers = merge_media_request_headers(
                        captured_headers,
                        response.request.headers,
                    )
                    if candidate.kind == MediaKind.HLS:
                        self.log(f"[*] 嗅探到 M3U8 候选流: {response.url[:60]}...")
                    candidates.append(candidate)
                    warnings.extend(candidate_warnings)
                except Exception:
                    pass

            page.on("response", handle_response)
            try:
                page.goto(page_url, wait_until="networkidle", timeout=25000)
                self.log("[*] 正在尝试模拟点击播放器以触发真实数据流...")
                try:
                    page.locator("video").first.click(timeout=3000)
                    page.wait_for_timeout(3000)
                except Exception:
                    try:
                        viewport = page.viewport_size or {"width": 1280, "height": 720}
                        page.mouse.click(viewport["width"] / 2, viewport["height"] / 2)
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
            except Exception as exc:
                warnings.append(f"页面加载异常或超时: {exc}")
            finally:
                cookies = tuple(context.cookies())
                try:
                    local_storage = page.evaluate(
                        "() => Object.fromEntries(Object.entries(window.localStorage))"
                    )
                except Exception:
                    local_storage = {}
                user_agent = self.headers.get("User-Agent", "")
                if not user_agent:
                    try:
                        user_agent = page.evaluate("navigator.userAgent")
                    except Exception:
                        user_agent = ""
                session = BrowserSessionSnapshot(
                    user_agent=user_agent,
                    referer=page_url,
                    origin=origin,
                    cookies=cookies,
                    headers=captured_headers,
                    local_storage=local_storage,
                )
                browser.close()

        return DiagnosticReport(
            source_url=page_url,
            candidates=candidates,
            session=session,
            warnings=warnings,
        )
