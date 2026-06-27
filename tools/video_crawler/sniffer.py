import os
import re
from urllib.parse import urljoin, urlparse

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    DiagnosticReport,
    MediaCandidate,
    MediaKind,
    PageAccessSnapshot,
    SnifferOptions,
)
from tools.video_crawler.session import extract_download_request_headers


AD_KEYWORDS = ("ad.", "/ad/", "adv", "blank", "preview", "v.admaster")
ACCESS_LIMITED_TITLE_KEYWORDS = ("403", "访问受限", "access denied", "forbidden")
TEXT_RESPONSE_TYPES = ("json", "javascript", "text/", "html")
MEDIA_URL_PATTERN = re.compile(
    (
        r"(?P<url>"
        r"(?:https?:)?//[^'\"<>\s]+?\.(?:m3u8|mp4|mpd)(?:\?[^'\"<>\s]*)?"
        r"|/[^'\"<>\s]+?\.(?:m3u8|mp4|mpd)(?:\?[^'\"<>\s]*)?"
        r")"
    ),
    re.IGNORECASE,
)


def _normalize_escaped_url_text(text: str) -> str:
    return (
        (text or "")
        .replace("\\/", "/")
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\u0026", "&")
    )


def _is_probable_media_url(raw_url: str, absolute_url: str) -> bool:
    if "\\." in raw_url:
        return False
    parsed = urlparse(absolute_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if "\\" in parsed.path:
        return False
    return parsed.path.lower().endswith((".m3u8", ".mp4", ".mpd"))


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


def extract_media_urls_from_text(base_url: str, text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    normalized_text = _normalize_escaped_url_text(text)
    for match in MEDIA_URL_PATTERN.finditer(normalized_text):
        raw_url = match.group("url")
        absolute_url = urljoin(base_url, raw_url)
        if not _is_probable_media_url(raw_url, absolute_url):
            continue
        if absolute_url not in seen:
            seen.add(absolute_url)
            urls.append(absolute_url)
    return urls


def candidates_from_response_text(
    base_url: str,
    content_type: str,
    text: str,
) -> list[MediaCandidate]:
    if not any(token in content_type.lower() for token in TEXT_RESPONSE_TYPES):
        return []
    candidates: list[MediaCandidate] = []
    for media_url in extract_media_urls_from_text(base_url, text):
        candidate, _ = classify_media_response(media_url, "")
        if candidate is not None:
            candidates.append(
                MediaCandidate(
                    url=candidate.url,
                    kind=candidate.kind,
                    source="response-body",
                    score=max(candidate.score - 5, 1),
                    content_type=content_type,
                )
            )
    return candidates


def should_continue_waiting_for_media(
    candidate_count: int,
    elapsed_seconds: float,
    limit_seconds: int,
    visible: bool = False,
) -> bool:
    if elapsed_seconds >= limit_seconds:
        return False
    return candidate_count <= 0


def has_reliable_media_candidate(candidates: list[MediaCandidate]) -> bool:
    return any(
        candidate.source != "response-body"
        and candidate.kind in {MediaKind.HLS, MediaKind.DIRECT_MP4, MediaKind.DASH}
        for candidate in candidates
    )


def detect_access_limited_page(
    snapshot: PageAccessSnapshot,
) -> VideoDownloadError | None:
    title = snapshot.title.lower()
    if snapshot.status_code == 403 or any(
        keyword in title for keyword in ACCESS_LIMITED_TITLE_KEYWORDS
    ):
        return VideoDownloadError(
            VideoErrorCode.HTTP_FORBIDDEN,
            (
                "页面访问受限，Playwright 未进入真实播放器；"
                f"状态码={snapshot.status_code}, 标题={snapshot.title or '未知'}"
            ),
            details={
                "status_code": snapshot.status_code,
                "title": snapshot.title,
                "final_url": snapshot.final_url,
                "video_count": snapshot.video_count,
                "iframe_count": snapshot.iframe_count,
            },
            retryable=False,
        )
    return None


class PageSniffer:
    def __init__(
        self,
        headers=None,
        log_callback=None,
        options: SnifferOptions | None = None,
    ):
        self.headers = headers or {}
        self.log_callback = log_callback
        self.options = options or SnifferOptions()

    def log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def _launch_context(self, playwright):
        launch_args = ["--mute-audio"]
        context_kwargs = {
            "extra_http_headers": self.headers,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1365, "height": 768},
        }
        if self.options.use_persistent_profile:
            os.makedirs(self.options.profile_dir, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                self.options.profile_dir,
                headless=self.options.headless,
                args=launch_args,
                **context_kwargs,
            )
            return None, context

        browser = playwright.chromium.launch(
            headless=self.options.headless,
            args=launch_args,
        )
        context = browser.new_context(**context_kwargs)
        return browser, context

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
            browser, context = self._launch_context(p)
            page = context.pages[0] if context.pages else context.new_page()
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
                        resource_type = response.request.resource_type
                        if resource_type in {"xhr", "fetch", "document", "script"}:
                            try:
                                body_text = response.text()
                            except Exception:
                                body_text = ""
                            for body_candidate in candidates_from_response_text(
                                response.url,
                                content_type,
                                body_text[:1_000_000],
                            ):
                                candidates.append(body_candidate)
                                self.log(
                                    "[*] 响应正文中发现媒体候选: "
                                    f"{body_candidate.url[:60]}..."
                                )
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

            def wait_for_candidates():
                if self.options.visible:
                    self.log(
                        f"[*] 可视化嗅探等待 {self.options.manual_wait_seconds} 秒；"
                        "请在浏览器中完成允许的人工操作并点击播放。"
                    )
                waited = 0.0
                while should_continue_waiting_for_media(
                    candidate_count=1 if has_reliable_media_candidate(candidates) else 0,
                    elapsed_seconds=waited,
                    limit_seconds=self.options.manual_wait_seconds,
                    visible=self.options.visible,
                ):
                    page.wait_for_timeout(1000)
                    waited += 1.0

            page.on("response", handle_response)
            try:
                if self.options.visible:
                    self.log(
                        "[*] 已启用可视化嗅探；如页面需要人工验证，"
                        "请在弹出的浏览器中完成后点击播放。"
                    )
                main_response = page.goto(
                    page_url,
                    wait_until="domcontentloaded",
                    timeout=25000,
                )
                access_snapshot = PageAccessSnapshot(
                    status_code=main_response.status if main_response else None,
                    title=page.title(),
                    final_url=page.url,
                    video_count=page.locator("video").count(),
                    iframe_count=page.locator("iframe").count(),
                )
                self.log(
                    "[*] 页面诊断: "
                    f"状态码={access_snapshot.status_code}, "
                    f"标题={access_snapshot.title or '未知'}, "
                    f"video={access_snapshot.video_count}, "
                    f"iframe={access_snapshot.iframe_count}"
                )
                access_error = detect_access_limited_page(access_snapshot)
                if access_error:
                    raise access_error
                self.log("[*] 正在尝试模拟点击播放器以触发真实数据流...")
                try:
                    page.locator("video").first.click(timeout=3000)
                    wait_for_candidates()
                except Exception:
                    try:
                        viewport = page.viewport_size or {"width": 1280, "height": 720}
                        page.mouse.click(viewport["width"] / 2, viewport["height"] / 2)
                        wait_for_candidates()
                    except Exception:
                        pass
            except VideoDownloadError:
                raise
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
                context.close()
                if browser is not None:
                    browser.close()

        return DiagnosticReport(
            source_url=page_url,
            candidates=candidates,
            session=session,
            warnings=warnings,
        )
