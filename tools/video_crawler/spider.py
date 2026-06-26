import os
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
from tools.video_crawler.models import MediaCandidate, MediaKind
from tools.video_crawler.session import build_download_headers
from tools.video_crawler.sniffer import PageSniffer


class UniversalVideoSpider:
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
    ):
        self.output_dir = output_dir
        self.temp_dir = temp_dir
        self.log_callback = log_callback
        self.is_high_speed = is_high_speed
        self.session_snapshot = session_snapshot
        self.resume_enabled = resume_enabled
        self.live_record_seconds = int(live_record_seconds)
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
        safe_message = redact_for_display(message)
        if self.log_callback:
            self.log_callback(safe_message)
        else:
            print(safe_message)

    def _classify_direct_url(self, url: str) -> MediaCandidate | None:
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
        mode_text = "高速模式" if self.is_high_speed else "低速稳定模式"
        self.log(f"[*] 开始分析目标 URL ({mode_text}): {url}")
        result_path = self.orchestrator.download(url, output_filename)
        return self._verify_output(result_path)

    def _verify_output(self, output_path):
        if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
            raise VideoDownloadError(
                VideoErrorCode.EMPTY_OUTPUT,
                f"输出文件不存在或为空: {output_path}",
                details={"output_path": output_path},
                retryable=False,
            )
        return output_path

    def _select_best_m3u8(self, m3u8_urls):
        unique_urls = []
        for url in m3u8_urls:
            if url not in unique_urls:
                unique_urls.append(url)

        best_url = None
        max_segments = -1
        self.log(f"[*] 开始对 {len(unique_urls)} 个候选流进行切片数量探测...")

        for url in unique_urls:
            try:
                response = requests.get(url, headers=self.headers, timeout=5)
                response.raise_for_status()
                if "#EXTM3U" not in response.text:
                    continue
                playlist = m3u8.loads(response.text, uri=url)
                if playlist.is_variant:
                    child_url = playlist.playlists[0].absolute_uri
                    child_response = requests.get(
                        child_url,
                        headers=self.headers,
                        timeout=5,
                    )
                    child_playlist = m3u8.loads(
                        child_response.text,
                        uri=child_url,
                    )
                    segment_count = len(child_playlist.segments)
                else:
                    segment_count = len(playlist.segments)

                self.log(
                    f"  -> 探测完成 | 切片数 {segment_count:4d} | "
                    f"链接: {url[:60]}..."
                )
                if segment_count > max_segments:
                    max_segments = segment_count
                    best_url = url
            except Exception:
                continue

        return best_url, max_segments

    def _sniff_real_url(self, page_url: str) -> str | None:
        report = PageSniffer(headers=self.headers, log_callback=self.log).sniff(page_url)
        self.session_snapshot = report.session
        hls_urls = [
            candidate.url
            for candidate in report.candidates
            if candidate.kind == MediaKind.HLS
        ]
        if hls_urls:
            best_url, segment_count = self._select_best_m3u8(hls_urls)
            if best_url:
                if segment_count < 10:
                    self.log(
                        f"[!] 警告: 选出的 M3U8 切片数较少 "
                        f"({segment_count} 个)，可能是短视频或广告。"
                    )
                else:
                    self.log(
                        f"[+] 决策结果: 成功锁定正片流，"
                        f"切片数 {segment_count}。"
                    )
                return best_url
            final_url = hls_urls[-1]
            self.log("[+] 决策结果: 深度探测未果，降级使用最后捕获的 M3U8。")
            return final_url

        for candidate in report.candidates:
            if candidate.kind == MediaKind.DIRECT_MP4:
                self.log("[+] 决策结果: 未发现 M3U8，降级使用捕获到的 MP4。")
                return candidate.url
            if candidate.kind == MediaKind.DASH:
                self.log("[+] 决策结果: 未发现 M3U8/MP4，尝试使用捕获到的 MPD。")
                return candidate.url
        return None

    def _download_mp4(self, video_url: str, save_path: str):
        adapter = DirectMp4Adapter(
            output_dir=self.output_dir,
            headers_getter=lambda: self.headers,
            log_callback=self.log,
        )
        return self._verify_output(adapter.download_url(video_url, save_path))

    async def _download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
        return await self._hls_adapter().download_ts(
            session,
            ts_url,
            save_path,
            cipher,
            extra_headers,
        )

    def _normalize_download_item(self, item, fallback_cipher):
        return self._hls_adapter().normalize_download_item(item, fallback_cipher)

    async def _download_segments(self, session, download_items, cipher):
        return await self._hls_adapter(
            download_ts=self._download_ts
            if self.__class__._download_ts is not UniversalVideoSpider._download_ts
            else None,
        ).download_segments(session, download_items, cipher)

    def _build_segment_cipher(self, key, media_sequence):
        return self._hls_adapter().build_segment_cipher(key, media_sequence)

    async def _download_m3u8(self, m3u8_url: str, output_filename: str):
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
        return self._hls_adapter().merge_with_ffmpeg(
            ts_files,
            output_mp4,
            init_file,
        )
