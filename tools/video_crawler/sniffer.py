"""使用 Playwright 诊断网页访问状态并嗅探真实媒体请求。"""

import os
import re
import time
from urllib.parse import urljoin, urlparse

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.logging_utils import redact_for_display
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
ACCESS_LIMITED_TITLE_KEYWORDS = (
    "403",
    "访问受限",
    "accessdenied",
    "forbidden",
    "justamoment",
    "checkingyourbrowser",
    "正在进行安全验证",
    "安全验证",
)
TEXT_RESPONSE_TYPES = ("json", "javascript", "text/", "html")
MAX_RESPONSE_TEXT_BYTES = 1_000_000
MEDIA_URL_PATTERN = re.compile(
    (
        r"(?P<url>"
        r"(?:https?:)?//[^'\"<>\s]+?\.(?:m3u8|mp4|mpd)(?:\?[^'\"<>\s]*)?"
        r"|/[^'\"<>\s]+?\.(?:m3u8|mp4|mpd)(?:\?[^'\"<>\s]*)?"
        r")"
    ),
    re.IGNORECASE,
)
# 这里只识别带明确媒体后缀的 URL。过宽的“任意 URL”正则会把广告、
# API 地址和 JavaScript 正则模板一并加入候选，降低后续探测可靠性。


def should_read_response_text(
    content_type: str,
    content_length: str,
    max_bytes: int = MAX_RESPONSE_TEXT_BYTES,
) -> bool:
    """只允许已知文本类型且未明确超限的响应进入正文提取。"""
    if not any(
        token in (content_type or "").lower()
        for token in TEXT_RESPONSE_TYPES
    ):
        return False
    try:
        known_size = int(content_length)
    except (TypeError, ValueError):
        return True
    return 0 <= known_size <= max_bytes


def _normalize_escaped_url_text(text: str) -> str:
    """还原 JSON 风格 URL 转义，同时保留正则反斜杠供后续过滤。"""
    return (
        (text or "")
        .replace("\\/", "/")
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\u0026", "&")
    )


def _is_probable_media_url(raw_url: str, absolute_url: str) -> bool:
    """排除正则占位符并确认 URL 具有可识别媒体后缀。"""
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
    """从媒体请求中增量合并允许继承的请求头。"""
    merged = dict(current)
    merged.update(extract_download_request_headers(incoming))
    return merged


def classify_media_response(response_url: str, content_type: str = ""):
    """综合响应 URL 与 content-type 构造媒体候选。"""
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
    """从 JSON、HTML 或脚本文本提取并去重媒体绝对地址。"""
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
    """把响应正文中发现的 URL 转换为带来源的候选对象。

    正文候选比网络响应候选降低 5 分，因为页面脚本可能只是在配置或
    模板中提到媒体地址；它们需要后续 playlist 探测才能成为可靠流。
    """
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


def deduplicate_media_candidates(
    candidates: list[MediaCandidate],
) -> list[MediaCandidate]:
    """按 URL 去重候选，并在相同地址出现真实请求时升级证据来源。"""
    unique: list[MediaCandidate] = []
    positions: dict[str, int] = {}
    for candidate in candidates:
        position = positions.get(candidate.url)
        if position is None:
            positions[candidate.url] = len(unique)
            unique.append(candidate)
            continue

        current = unique[position]
        current_rank = (current.source == "network", current.score)
        incoming_rank = (candidate.source == "network", candidate.score)
        if incoming_rank > current_rank:
            # 保留首次发现位置，只替换该位置的证据强度和响应元数据。
            unique[position] = candidate
    return unique


def should_continue_waiting_for_media(
    candidate_count: int,
    elapsed_seconds: float,
    limit_seconds: int,
    visible: bool = False,
) -> bool:
    """根据候选是否出现和等待上限决定是否继续轮询。"""
    if elapsed_seconds >= limit_seconds:
        return False
    return candidate_count <= 0


def has_reliable_media_candidate(candidates: list[MediaCandidate]) -> bool:
    """仅把网络层 HLS 视为可提前结束等待的最高优先级证据。"""
    # MP4 即使来自真实网络响应也可能是前贴广告或推荐短片。只有正片
    # 优先级最高的网络 HLS 能提前收尾，其余候选必须等满观察窗口。
    return any(
        candidate.source == "network" and candidate.kind == MediaKind.HLS
        for candidate in candidates
    )


def detect_access_limited_page(
    snapshot: PageAccessSnapshot,
) -> VideoDownloadError | None:
    """依据状态码和页面标题识别访问受限并构造结构化异常。"""
    title = re.sub(
        r"[^a-z0-9\u4e00-\u9fff]+",
        "",
        (snapshot.title or "").lower(),
    )
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


def trigger_playback(page) -> str:
    """优先点击主页面或 iframe 中的 video，最后点击视口中心。"""
    frames = list(getattr(page, "frames", ()) or ())
    if not frames:
        frames = [page]

    for frame in frames:
        try:
            video = frame.locator("video")
            if video.count() > 0:
                video.first.click(timeout=3000)
                return "frame-video"
        except Exception:
            # 跨域 frame 或已销毁 frame 可能无法查询 DOM，继续尝试下一层。
            continue

    try:
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        page.mouse.click(viewport["width"] / 2, viewport["height"] / 2)
        return "viewport-center"
    except Exception:
        return "none"


class PageSniffer:
    """驱动 Playwright 页面并收集媒体候选与浏览器会话。"""
    def __init__(
        self,
        headers=None,
        log_callback=None,
        options: SnifferOptions | None = None,
    ):
        """保存初始请求头、日志回调和不可变的浏览器启动选项。"""
        self.headers = headers or {}
        self.log_callback = log_callback
        self.options = options or SnifferOptions()

    def log(self, message: str) -> None:
        """将嗅探状态转发给调用方提供的日志回调。"""
        if self.log_callback:
            self.log_callback(message)

    def _launch_context(self, playwright):
        """按配置创建普通浏览器上下文或持久化 Chromium 上下文。

        Returns:
            `(browser, context)`。持久化上下文自身拥有浏览器生命周期，
            因而该模式返回的 `browser` 为 `None`，关闭时不能重复处理。
        """
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
        """打开目标页面，收集媒体候选与会话，并返回结构化诊断报告。

        Args:
            page_url: 需要进入播放器并监听网络响应的网页地址。

        Returns:
            包含去重前候选列表、浏览器会话和非致命警告的诊断报告。

        Raises:
            VideoDownloadError: 无界面模式确认访问受限，或可视化模式等待人工
                验证到期后仍未进入播放器时抛出。
        """
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
            navigation_incomplete = False

            def handle_response(response):
                """把单个 Playwright 响应转换为网络候选或正文候选。"""
                nonlocal captured_headers
                try:
                    content_type = response.headers.get("content-type", "")
                    candidate, candidate_warnings = classify_media_response(
                        response.url,
                        content_type,
                    )
                    if candidate is None:
                        resource_type = response.request.resource_type
                        content_length = response.headers.get(
                            "content-length",
                            "",
                        )
                        if (
                            resource_type in {"xhr", "fetch", "document", "script"}
                            and should_read_response_text(
                                content_type,
                                content_length,
                            )
                        ):
                            try:
                                body_text = response.text()
                            except Exception as exc:
                                warning = (
                                    "响应正文读取失败: "
                                    f"{type(exc).__name__}"
                                )
                                if warning not in warnings and len(warnings) < 10:
                                    warnings.append(warning)
                                    self.log(f"[!] {warning}")
                                body_text = ""
                            # 限制正文读取规模，避免超大脚本/接口响应在工作线程
                            # 中制造不受控内存占用。
                            for body_candidate in candidates_from_response_text(
                                response.url,
                                content_type,
                                body_text[:MAX_RESPONSE_TEXT_BYTES],
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
                    # Playwright 响应回调不能把单个跨域/已释放响应异常传播到
                    # 整个页面事件循环；失败响应由后续候选继续补偿。
                    pass

            def capture_access_snapshot(status_code=None):
                """重新读取当前页面状态，避免沿用首次导航的过期 403。"""
                return PageAccessSnapshot(
                    status_code=status_code,
                    title=page.title(),
                    final_url=page.url,
                    video_count=page.locator("video").count(),
                    iframe_count=page.locator("iframe").count(),
                )

            def log_access_snapshot(prefix, snapshot):
                """记录一次可供用户核对的页面访问快照。"""
                self.log(
                    f"[*] {prefix}: "
                    f"状态码={snapshot.status_code}, "
                    f"标题={snapshot.title or '未知'}, "
                    f"video={snapshot.video_count}, "
                    f"iframe={snapshot.iframe_count}"
                )

            def wait_for_candidates(deadline):
                """在共享截止时间内按可靠候选策略等待媒体请求。"""
                # 使用真实截止时间，把同步响应回调的耗时也计入观察预算。
                while not has_reliable_media_candidate(candidates):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    page.wait_for_timeout(
                        max(1, min(1000, int(remaining * 1000)))
                    )

                normalized = deduplicate_media_candidates(candidates)
                counts: dict[str, int] = {}
                for candidate in normalized:
                    key = f"{candidate.kind.value}/{candidate.source}"
                    counts[key] = counts.get(key, 0) + 1
                count_text = ", ".join(
                    f"{key}={value}" for key, value in sorted(counts.items())
                ) or "无候选"
                if has_reliable_media_candidate(candidates):
                    self.log(
                        f"[+] 嗅探观察结束: 已捕获网络 HLS；候选统计: {count_text}"
                    )
                else:
                    self.log(
                        f"[*] 嗅探观察结束: 已达到 {self.options.manual_wait_seconds} 秒"
                        f"等待上限；候选统计: {count_text}"
                    )

            page.on("response", handle_response)
            main_response = None
            try:
                if self.options.visible:
                    self.log(
                        "[*] 已启用可视化嗅探；如页面需要人工验证，"
                        "请在弹出的浏览器中完成后点击播放。"
                    )

                try:
                    main_response = page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=25000,
                    )
                except Exception as exc:
                    navigation_incomplete = True
                    safe_error = redact_for_display(str(exc))
                    warning = f"页面加载异常或超时: {safe_error}"
                    warnings.append(warning)
                    self.log(f"[!] {warning}；继续观察媒体请求。")

                deadline = time.monotonic() + self.options.manual_wait_seconds
                if self.options.visible:
                    self.log(
                        f"[*] 可视化嗅探等待 {self.options.manual_wait_seconds} 秒；"
                        "请在浏览器中完成允许的人工操作并点击播放。"
                    )

                access_snapshot = None
                access_error = None
                try:
                    access_snapshot = capture_access_snapshot(
                        main_response.status if main_response else None
                    )
                    log_access_snapshot("页面诊断", access_snapshot)
                    access_error = detect_access_limited_page(access_snapshot)
                except Exception as exc:
                    navigation_incomplete = True
                    safe_error = redact_for_display(str(exc))
                    warning = f"页面诊断异常: {safe_error}"
                    warnings.append(warning)
                    self.log(f"[!] {warning}；继续观察媒体请求。")

                if access_error and not self.options.visible:
                    raise access_error

                if access_error:
                    self.log(
                        "[*] 检测到安全验证页；请在浏览器中完成站点允许的"
                        "人工验证，程序会在验证通过后继续嗅探。"
                    )
                    access_cleared = False
                    current_snapshot = access_snapshot
                    while not has_reliable_media_candidate(candidates):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        page.wait_for_timeout(
                            max(1, min(1000, int(remaining * 1000)))
                        )
                        try:
                            current_snapshot = capture_access_snapshot()
                        except Exception as exc:
                            safe_error = redact_for_display(str(exc))
                            warning = (
                                "安全验证页面切换中，暂时无法读取页面状态: "
                                f"{safe_error}"
                            )
                            if warning not in warnings and len(warnings) < 10:
                                warnings.append(warning)
                                self.log(f"[!] {warning}；继续等待页面稳定。")
                            continue
                        current_error = detect_access_limited_page(
                            current_snapshot
                        )
                        has_current_page = bool(
                            current_snapshot.title.strip()
                            or current_snapshot.video_count
                            or current_snapshot.iframe_count
                        )
                        if current_error is None and has_current_page:
                            access_cleared = True
                            self.log(
                                "[+] 安全验证已通过；正在进入真实播放器。"
                            )
                            log_access_snapshot(
                                "验证后页面诊断",
                                current_snapshot,
                            )
                            break

                    if (
                        not access_cleared
                        and not has_reliable_media_candidate(candidates)
                    ):
                        try:
                            final_snapshot = capture_access_snapshot()
                        except Exception as exc:
                            safe_error = redact_for_display(str(exc))
                            self.log(
                                "[!] 安全验证等待结束时仍无法读取页面状态: "
                                f"{safe_error}"
                            )
                            raise access_error
                        final_error = detect_access_limited_page(final_snapshot)
                        if final_error:
                            final_snapshot = PageAccessSnapshot(
                                status_code=access_snapshot.status_code,
                                title=final_snapshot.title,
                                final_url=final_snapshot.final_url,
                                video_count=final_snapshot.video_count,
                                iframe_count=final_snapshot.iframe_count,
                            )
                            log_access_snapshot(
                                "安全验证等待结束",
                                final_snapshot,
                            )
                            raise (
                                detect_access_limited_page(final_snapshot)
                                or access_error
                            )
                        self.log(
                            "[+] 安全验证已通过；正在进入真实播放器。"
                        )

                if has_reliable_media_candidate(candidates):
                    wait_for_candidates(deadline)
                else:
                    self.log("[*] 正在尝试触发播放器以产生真实数据流...")
                    trigger_result = trigger_playback(page)
                    self.log(f"[*] 播放器触发方式: {trigger_result}")
                    wait_for_candidates(deadline)
            finally:
                # 必须在 context/page 关闭前提取 Cookie、LocalStorage 和 UA；
                # 下载适配器随后使用这些数据访问要求同一会话的 CDN。
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
            candidates=deduplicate_media_candidates(candidates),
            session=session,
            navigation_incomplete=navigation_incomplete,
            warnings=warnings,
        )
