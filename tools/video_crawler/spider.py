"""编排网页嗅探、候选流选择和 MP4/HLS/DASH 下载适配器。"""

import os
import re
import subprocess

import m3u8
import requests
from playwright.sync_api import sync_playwright

from tools.video_crawler.adapters.base import VideoDownloadOrchestrator
from tools.video_crawler.adapters.dash import DashAdapter
from tools.video_crawler.adapters.direct_mp4 import DirectMp4Adapter
from tools.video_crawler.adapters.hls import HlsAdapter
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.logging_utils import redact_for_display
from tools.video_crawler.models import MediaCandidate, MediaKind, SnifferOptions
from tools.video_crawler.session import build_download_headers
from tools.video_crawler.sniffer import PageSniffer, deduplicate_media_candidates


URL_BANDWIDTH_PATTERN = re.compile(r"(?<!\d)(\d{3,5})k(?!\d)", re.IGNORECASE)
PLAYLIST_PROBE_TIMEOUT_SECONDS = 15


def is_timeout_like_error(exc: Exception) -> bool:
    """沿异常链判断错误是否代表网络超时或 Windows 10060。"""
    if isinstance(
        exc,
        (
            TimeoutError,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ),
    ):
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text or "10060" in text


def _resolution_tuple(value) -> tuple[int, int]:
    """把分辨率对象安全转换为可排序的宽高整数元组。"""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            return int(value[0] or 0), int(value[1] or 0)
        except (TypeError, ValueError):
            return 0, 0
    if isinstance(value, str) and "x" in value.lower():
        left, _, right = value.lower().partition("x")
        try:
            return int(left or 0), int(right or 0)
        except ValueError:
            return 0, 0
    return 0, 0


def _resolution_pixels(resolution: tuple[int, int]) -> int:
    """计算分辨率总像素，用于候选画质排序。"""
    return int(resolution[0] or 0) * int(resolution[1] or 0)


def _infer_bandwidth_from_url(url: str) -> int:
    """从 URL 中的 3000k 等片段推断近似带宽。"""
    match = URL_BANDWIDTH_PATTERN.search(url or "")
    if not match:
        return 0
    return int(match.group(1)) * 1000


def _best_variant_info(playlist) -> dict:
    """从 HLS 主清单选择带宽和分辨率最高的变体信息。"""
    best_info = {
        "url": "",
        "bandwidth": 0,
        "resolution": (0, 0),
    }
    for item in getattr(playlist, "playlists", []) or []:
        stream_info = getattr(item, "stream_info", None)
        bandwidth = int(getattr(stream_info, "bandwidth", 0) or 0)
        resolution = _resolution_tuple(getattr(stream_info, "resolution", None))
        current_key = (bandwidth, _resolution_pixels(resolution))
        best_key = (
            best_info["bandwidth"],
            _resolution_pixels(best_info["resolution"]),
        )
        if current_key > best_key:
            best_info = {
                "url": getattr(item, "absolute_uri", "") or getattr(item, "uri", ""),
                "bandwidth": bandwidth,
                "resolution": resolution,
            }
    return best_info


def _format_quality_metrics(bandwidth: int, resolution: tuple[int, int]) -> str:
    """把带宽和分辨率格式化为便于诊断的日志片段。"""
    parts = []
    if bandwidth:
        parts.append(f"最高码率 {round(bandwidth / 1000)}k")
    if _resolution_pixels(resolution):
        parts.append(f"分辨率 {resolution[0]}x{resolution[1]}")
    if not parts:
        parts.append("码率/分辨率未知")
    return " | ".join(parts)


def response_total_size(response) -> int | None:
    """从 HTTP 响应头提取资源总大小，优先解析 Content-Range。"""
    headers = getattr(response, "headers", {}) or {}
    content_range = headers.get("Content-Range") or headers.get("content-range", "")
    range_match = re.search(r"/(\d+)\s*$", str(content_range))
    if range_match:
        total_size = int(range_match.group(1))
        return total_size if total_size > 0 else None

    content_length = headers.get("Content-Length") or headers.get(
        "content-length", ""
    )
    try:
        total_size = int(content_length)
    except (TypeError, ValueError):
        return None
    return total_size if total_size > 0 else None


class UniversalVideoSpider:
    """协调候选解析、正片选择、适配器下载和输出校验。"""

    def __init__(
        self,
        output_dir="./downloads",
        temp_dir="./temp",
        log_callback=None,
        is_high_speed=False,
        segment_concurrency=None,
        session_snapshot=None,
        resume_enabled=True,
        live_record_seconds=300,
        sniffer_options: SnifferOptions | None = None,
    ):
        """初始化一次下载会话的目录、并发、续传和嗅探配置。

        `segment_concurrency=None` 才会使用模式默认值；显式传入的值必须
        原样保留，因为 UI 会为每个排队任务冻结独立并发设置。

        Raises:
            ValueError: 切片并发数不在 UI 与下载器共同支持的 1-100 范围内。
        """
        self.output_dir = output_dir
        self.temp_dir = temp_dir
        self.log_callback = log_callback
        self.is_high_speed = is_high_speed
        self.session_snapshot = session_snapshot
        self.resume_enabled = resume_enabled
        self.live_record_seconds = int(live_record_seconds)
        self.sniffer_options = sniffer_options or SnifferOptions()
        default_concurrency = 30 if is_high_speed else 5
        self.segment_concurrency = (
            default_concurrency
            if segment_concurrency is None
            else int(segment_concurrency)
        )
        if not 1 <= self.segment_concurrency <= 100:
            raise ValueError("切片并发数必须在 1 到 100 之间")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        self.orchestrator = self._build_orchestrator()

    def _build_orchestrator(self):
        """构造内置适配器并按优先级注册到下载编排器。"""
        # 注入 lambda 而非让适配器持有固定 Header 副本：网页嗅探完成后，
        # self.headers 会追加 Cookie/Referer 等会话信息，适配器应读取最新值。
        return VideoDownloadOrchestrator(
            adapters=[
                DirectMp4Adapter(
                    output_dir=self.output_dir,
                    headers_getter=lambda: self.headers,
                    log_callback=self.log,
                    download_url=lambda url, save_path: self._download_mp4(
                        url,
                        save_path,
                    ),
                ),
                HlsAdapter(
                    output_dir=self.output_dir,
                    temp_dir=self.temp_dir,
                    headers_getter=lambda: self.headers,
                    log_callback=self.log,
                    is_high_speed=self.is_high_speed,
                    segment_concurrency=self.segment_concurrency,
                    resume_enabled=self.resume_enabled,
                    live_record_seconds=self.live_record_seconds,
                    verify_output=self._verify_output,
                    download_m3u8=lambda url, output_filename: self._download_m3u8(
                        url,
                        output_filename,
                    ),
                ),
                DashAdapter(
                    output_dir=self.output_dir,
                    temp_dir=self.temp_dir,
                    headers_getter=lambda: self.headers,
                    log_callback=self.log,
                    segment_concurrency=self.segment_concurrency,
                ),
            ],
            candidate_resolver=self._resolve_candidate,
        )

    def _hls_adapter(self, *, download_ts=None, merge_with_ffmpeg=None):
        """创建继承当前并发、会话、续传和直播设置的 HLS 适配器。"""
        return HlsAdapter(
            output_dir=self.output_dir,
            temp_dir=self.temp_dir,
            headers_getter=lambda: self.headers,
            log_callback=self.log,
            is_high_speed=self.is_high_speed,
            segment_concurrency=self.segment_concurrency,
            resume_enabled=self.resume_enabled,
            live_record_seconds=self.live_record_seconds,
            verify_output=self._verify_output,
            download_ts=download_ts,
            merge_with_ffmpeg=merge_with_ffmpeg,
        )

    def log(self, message):
        """将爬虫状态转发给 UI 日志回调；未配置回调时保持静默。"""
        safe_message = redact_for_display(message)
        if self.log_callback:
            self.log_callback(safe_message)
        else:
            print(safe_message)

    def _classify_direct_url(self, url: str) -> MediaCandidate | None:
        """根据 URL 后缀快速构造 MP4、HLS 或 DASH 直链候选。"""
        lowered = url.lower()
        if lowered.endswith(".mp4") or ".mp4?" in lowered:
            return MediaCandidate(
                url=url,
                kind=MediaKind.DIRECT_MP4,
                source="direct",
                score=100,
            )
        if lowered.endswith(".m3u8") or ".m3u8?" in lowered:
            return MediaCandidate(
                url=url,
                kind=MediaKind.HLS,
                source="direct",
                score=100,
            )
        if lowered.endswith(".mpd") or ".mpd?" in lowered:
            return MediaCandidate(
                url=url,
                kind=MediaKind.DASH,
                source="direct",
                score=90,
            )
        return None

    def _resolve_candidate(self, url: str) -> MediaCandidate:
        """解析直链或网页，返回最终候选及嗅探到的浏览器会话。

        网页候选确定后才把浏览器会话合并进下载 Header，避免把某个站点
        的 Cookie 意外带到不相关的直链请求。
        """
        direct_candidate = self._classify_direct_url(url)
        if direct_candidate:
            if direct_candidate.kind == MediaKind.DIRECT_MP4:
                self.log("[*] 判定为直链 MP4，启动普通下载模块...")
            elif direct_candidate.kind == MediaKind.HLS:
                self.log("[*] 判定为 M3U8 流，启动异步切片下载模块...")
            elif direct_candidate.kind == MediaKind.DASH:
                self.log("[*] 判定为 DASH/MPD，启动 DASH 适配器...")
            return direct_candidate

        self.log("[*] 判定为网页，启动 Playwright 嗅探真实视频流...")
        real_url = self._sniff_real_url(url)
        if real_url:
            self.log(f"[+] 嗅探成功，真实地址为: {real_url}")
            if self.session_snapshot:
                self.headers = build_download_headers(
                    self.headers,
                    self.session_snapshot,
                    real_url,
                )
            sniffed_candidate = self._classify_direct_url(real_url)
            if sniffed_candidate:
                return sniffed_candidate
        raise VideoDownloadError(
            VideoErrorCode.NO_MEDIA_FOUND,
            "嗅探失败，未能找到视频流",
            retryable=False,
        )

    def run(self, url: str, output_filename: str):
        """执行一次完整下载，并在返回前验证输出文件非空。"""
        mode_text = "高速模式" if self.is_high_speed else "低速稳定模式"
        self.log(f"[*] 开始分析目标 URL ({mode_text}): {url}")
        result_path = self.orchestrator.download(url, output_filename)
        return self._verify_output(result_path)

    def _verify_output(self, output_path):
        """检查输出路径是否存在且文件大小大于零。"""
        if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
            raise VideoDownloadError(
                VideoErrorCode.EMPTY_OUTPUT,
                f"输出文件不存在或为空: {output_path}",
                details={"output_path": output_path},
                retryable=False,
            )
        return output_path

    def _select_best_m3u8(self, m3u8_urls):
        """探测候选切片与画质，并在正片容差内选择最佳 HLS 地址。

        Args:
            m3u8_urls: 按浏览器捕获顺序排列、可能重复的候选地址。

        Returns:
            `(url, segment_count)`；所有候选均无法验证时返回 `(None, -1)`，
            详细失败原因保存在 `_last_m3u8_probe_errors` 供上层分类。

        切片数用于排除广告/预览短流，带宽和分辨率才用于同长度候选的
        画质判断，因此不能简单地只取切片最多或最先捕获的地址。
        """
        unique_urls = []
        for url in m3u8_urls:
            if url not in unique_urls:
                unique_urls.append(url)

        probe_results = []
        self._last_m3u8_probe_errors = []
        self.log(f"[*] 开始对 {len(unique_urls)} 个候选流进行切片数量探测...")

        for url in unique_urls:
            try:
                probe_headers = dict(self.headers)
                if self.session_snapshot:
                    probe_headers = build_download_headers(
                        probe_headers,
                        self.session_snapshot,
                        url,
                    )
                response = requests.get(
                    url,
                    headers=probe_headers,
                    timeout=PLAYLIST_PROBE_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                # 某些页面会用 .m3u8 后缀返回拦截页；先检查清单标记，
                # 避免 m3u8 库把 HTML 解析为空 playlist 后参与排序。
                if "#EXTM3U" not in response.text:
                    self._last_m3u8_probe_errors.append(
                        {
                            "url": url,
                            "reason": "响应不是有效 M3U8 playlist",
                            "timeout": False,
                        }
                    )
                    continue
                playlist = m3u8.loads(response.text, uri=url)
                bandwidth = _infer_bandwidth_from_url(url)
                resolution = (0, 0)
                if playlist.is_variant:
                    # 主清单自身没有媒体分片。选择其最高质量子清单后再统计，
                    # 同时保留 master URL 作为最终下载入口，让 HLS 适配器
                    # 继续处理默认音轨和字幕轨。
                    variant_info = _best_variant_info(playlist)
                    bandwidth = variant_info["bandwidth"] or bandwidth
                    resolution = variant_info["resolution"]
                    child_url = variant_info["url"]
                    child_headers = dict(self.headers)
                    if self.session_snapshot:
                        child_headers = build_download_headers(
                            child_headers,
                            self.session_snapshot,
                            child_url,
                        )
                    child_response = requests.get(
                        child_url,
                        headers=child_headers,
                        timeout=PLAYLIST_PROBE_TIMEOUT_SECONDS,
                    )
                    child_response.raise_for_status()
                    child_playlist = m3u8.loads(
                        child_response.text,
                        uri=child_url,
                    )
                    segment_count = len(child_playlist.segments)
                else:
                    segment_count = len(playlist.segments)

                probe_result = {
                    "url": url,
                    "segment_count": segment_count,
                    "bandwidth": bandwidth,
                    "resolution": resolution,
                    "is_variant": bool(playlist.is_variant),
                }
                probe_results.append(probe_result)
                self.log(
                    f"  -> 探测完成 | 切片数 {segment_count:4d} | "
                    f"{_format_quality_metrics(bandwidth, resolution)} | "
                    f"链接: {url[:60]}..."
                )
            except Exception as exc:
                self._last_m3u8_probe_errors.append(
                    {
                        "url": url,
                        "reason": str(exc),
                        "timeout": is_timeout_like_error(exc),
                    }
                )
                continue

        self._last_m3u8_probe_results = probe_results
        if not probe_results:
            return None, -1

        max_segments = max(item["segment_count"] for item in probe_results)
        # CDN 镜像或不同码率可能在结尾相差少量分片。固定 3 片与 5% 比例
        # 取较大者，可容忍正常差异，同时排除只有几片的广告流。
        segment_tolerance = max(3, int(max_segments * 0.05))
        comparable_results = [
            item
            for item in probe_results
            if max_segments - item["segment_count"] <= segment_tolerance
        ]
        # 只在“长度像正片”的集合内比较画质，排序优先级明确为码率、
        # 分辨率、切片数；缺失的质量指标按 0 处理。
        best_result = max(
            comparable_results,
            key=lambda item: (
                item["bandwidth"],
                _resolution_pixels(item["resolution"]),
                item["segment_count"],
            ),
        )
        self._last_selected_m3u8_probe = best_result
        return best_result["url"], best_result["segment_count"]

    def _select_best_mp4(
        self,
        candidates: list[MediaCandidate],
    ) -> MediaCandidate | None:
        """验证 MP4 元数据，并按来源、大小、分数和发现顺序选择后备地址。"""
        ranked_candidates = []
        for index, candidate in enumerate(candidates):
            size = self._probe_mp4_size(candidate.url)
            if candidate.source == "response-body" and size is None:
                self.log(
                    "[!] 跳过无法验证的响应正文 MP4 候选: "
                    f"{candidate.url[:60]}..."
                )
                continue
            self.log(
                "[*] MP4 候选验证: "
                f"来源={candidate.source}, "
                f"大小={size if size is not None else '未知'}, "
                f"地址={candidate.url[:60]}..."
            )
            ranked_candidates.append(
                (
                    candidate,
                    (
                        candidate.source == "network",
                        size is not None,
                        size or 0,
                        candidate.score,
                        -index,
                    ),
                )
            )

        if not ranked_candidates:
            return None
        return max(ranked_candidates, key=lambda item: item[1])[0]

    def _probe_mp4_size(self, url: str) -> int | None:
        """用 HEAD 或单字节 Range 请求探测 MP4 总大小，避免下载正文。"""
        headers = dict(self.headers)
        if self.session_snapshot:
            headers = build_download_headers(headers, self.session_snapshot, url)

        try:
            with requests.head(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=5,
            ) as response:
                response.raise_for_status()
                total_size = response_total_size(response)
                if total_size is not None:
                    return total_size
        except Exception as exc:
            self.log(f"[!] MP4 HEAD 探测失败，尝试 Range 请求: {exc}")

        range_headers = dict(headers)
        range_headers["Range"] = "bytes=0-0"
        try:
            with requests.get(
                url,
                headers=range_headers,
                stream=True,
                allow_redirects=True,
                timeout=5,
            ) as response:
                response.raise_for_status()
                return response_total_size(response)
        except Exception as exc:
            self.log(f"[!] MP4 Range 探测失败: {exc}")
            return None

    def _sniff_real_url(self, page_url: str) -> str | None:
        """运行页面嗅探，并在多个候选之间完成正片决策。

        HLS 优先于 MP4/DASH，因为网页常同时请求预览 MP4 和正片 HLS。
        一旦页面提供 HLS 候选却全部无法验证，本方法抛出结构化错误，
        不再静默降级到未经确认的地址。
        """
        report = PageSniffer(
            headers=self.headers,
            log_callback=self.log,
            options=self.sniffer_options,
        ).sniff(page_url)
        # 会话快照稍后由 _resolve_candidate 按最终媒体域名筛选并合并。
        self.session_snapshot = report.session
        candidates = deduplicate_media_candidates(report.candidates)
        hls_urls = [
            candidate.url
            for candidate in candidates
            if candidate.kind == MediaKind.HLS
        ]
        if hls_urls:
            best_url, segment_count = self._select_best_m3u8(hls_urls)
            if best_url:
                selected_probe = getattr(self, "_last_selected_m3u8_probe", {})
                quality_text = _format_quality_metrics(
                    int(selected_probe.get("bandwidth", 0) or 0),
                    selected_probe.get("resolution", (0, 0)),
                )
                if segment_count < 10:
                    self.log(
                        f"[!] 警告: 选出的 M3U8 切片数较少 "
                        f"({segment_count} 个，{quality_text})，"
                        "可能是短视频或广告。"
                    )
                else:
                    self.log(
                        f"[+] 决策结果: 成功锁定正片流，"
                        f"切片数 {segment_count}，{quality_text}。"
                    )
                return best_url
            probe_errors = getattr(self, "_last_m3u8_probe_errors", [])
            # 只要任一候选呈现超时特征，就优先提示网络/连通性问题；
            # 否则视为 playlist 内容或访问方式不受支持。
            timeout_like = any(error.get("timeout") for error in probe_errors)
            code = (
                VideoErrorCode.NETWORK_TIMEOUT
                if timeout_like
                else VideoErrorCode.M3U8_PARSE_FAILED
            )
            message = (
                "候选 M3U8 无法在下载器中验证，"
                "可能网络超时、站点拒绝直连或 playlist 无效。"
            )
            self.log(f"[X] {message}")
            raise VideoDownloadError(
                code,
                message,
                details={"candidate_urls": hls_urls, "probe_errors": probe_errors},
                retryable=timeout_like,
            )

        mp4_candidates = [
            candidate
            for candidate in candidates
            if candidate.kind == MediaKind.DIRECT_MP4
        ]
        selected_mp4 = self._select_best_mp4(mp4_candidates)
        if selected_mp4 is not None:
            self.log(
                "[+] 决策结果: 未发现 M3U8，使用验证后的最佳 MP4 "
                f"(来源={selected_mp4.source})。"
            )
            return selected_mp4.url

        dash_candidates = [
            candidate for candidate in candidates if candidate.kind == MediaKind.DASH
        ]
        if dash_candidates:
            selected_dash = max(
                enumerate(dash_candidates),
                key=lambda item: (
                    item[1].source == "network",
                    item[1].score,
                    -item[0],
                ),
            )[1]
            self.log(
                "[+] 决策结果: 未发现可用 M3U8/MP4，尝试使用最佳 MPD "
                f"(来源={selected_dash.source})。"
            )
            return selected_dash.url
        return None

    def _download_mp4(self, video_url: str, save_path: str):
        """通过直接 MP4 适配器下载指定地址。"""
        adapter = DirectMp4Adapter(
            output_dir=self.output_dir,
            headers_getter=lambda: self.headers,
            log_callback=self.log,
        )
        return self._verify_output(adapter.download_url(video_url, save_path))

    async def _download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
        """兼容旧调用方，将单个 HLS 分片交给适配器下载。"""
        return await self._hls_adapter().download_ts(
            session,
            ts_url,
            save_path,
            cipher,
            extra_headers,
        )

    def _normalize_download_item(self, item, fallback_cipher):
        """把旧式 URL 字符串或分片字典规范为统一下载项。"""
        return self._hls_adapter().normalize_download_item(item, fallback_cipher)

    async def _download_segments(self, session, download_items, cipher):
        """兼容旧测试入口，委托 HLS 适配器并发下载分片。"""
        # 仅当子类/测试覆盖旧钩子时才注入，正常运行保持适配器原生实现。
        return await self._hls_adapter(
            download_ts=self._download_ts
            if self.__class__._download_ts is not UniversalVideoSpider._download_ts
            else None,
        ).download_segments(session, download_items, cipher)

    def _build_segment_cipher(self, key, media_sequence):
        """兼容旧入口，构造指定 HLS 分片使用的解密器。"""
        return self._hls_adapter().build_segment_cipher(key, media_sequence)

    async def _download_m3u8(self, m3u8_url: str, output_filename: str):
        """通过 HLS 适配器下载清单并返回最终 MP4 路径。"""
        return await self._hls_adapter(
            download_ts=self._download_ts
            if self.__class__._download_ts is not UniversalVideoSpider._download_ts
            else None,
            merge_with_ffmpeg=self._merge_with_ffmpeg
            if self.__class__._merge_with_ffmpeg
            is not UniversalVideoSpider._merge_with_ffmpeg
            else None,
        ).download_url(m3u8_url, output_filename)

    def _merge_with_ffmpeg(self, ts_files: list, output_mp4: str, init_file: str = None):
        """兼容旧入口，委托 HLS 适配器执行 FFmpeg 合并。"""
        return self._hls_adapter().merge_with_ffmpeg(
            ts_files,
            output_mp4,
            init_file,
        )
