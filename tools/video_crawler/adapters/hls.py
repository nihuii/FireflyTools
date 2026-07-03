"""下载和合并 HLS 点播、直播、加密、多轨及 fMP4 媒体流。"""

import asyncio
import inspect
import os
import random
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import URLError

import aiohttp
import m3u8
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import DiagnosticReport, MediaCandidate, MediaKind
from tools.video_crawler.resume import SegmentManifest


def is_timeout_like_error(exc: Exception) -> bool:
    """沿异常链判断错误是否代表网络超时或 Windows 10060。"""
    if isinstance(exc, (TimeoutError, requests.exceptions.Timeout)):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, TimeoutError):
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text or "10060" in text


@dataclass(frozen=True)
class HlsRenditionPlan:
    """保存 HLS 主清单选择出的默认音轨和字幕轨。"""
    audio_url: str | None = None
    subtitle_url: str | None = None


def _is_default_rendition(media) -> bool:
    """判断 HLS 扩展媒体条目是否标记为默认轨道。"""
    return str(getattr(media, "default", "")).upper() == "YES"


def build_hls_rendition_plan(playlist) -> HlsRenditionPlan:
    """从主清单选出默认音轨和字幕轨 URI。"""
    audio_url = None
    subtitle_url = None
    for media in getattr(playlist, "media", []) or []:
        media_type = str(getattr(media, "type", "")).upper()
        uri = getattr(media, "absolute_uri", "") or getattr(media, "uri", "")
        if not uri:
            continue
        if media_type == "AUDIO" and audio_url is None and _is_default_rendition(media):
            audio_url = uri
        if (
            media_type == "SUBTITLES"
            and subtitle_url is None
            and _is_default_rendition(media)
        ):
            subtitle_url = uri
    return HlsRenditionPlan(audio_url=audio_url, subtitle_url=subtitle_url)


def derive_hls_iv(iv_text: str | None, media_sequence: int) -> bytes:
    """优先使用显式 IV，否则按媒体序号生成 16 字节大端 IV。"""
    if iv_text:
        # HLS 清单通常使用 0x 前缀；AES-CBC 始终需要 16 字节，较短
        # 的十六进制值应在高位补零而不是在低位追加。
        normalized = iv_text[2:] if iv_text.lower().startswith("0x") else iv_text
        return bytes.fromhex(normalized.zfill(32))
    return int(media_sequence).to_bytes(16, "big")


def parse_hls_byterange(
    byterange: str | None,
    previous_end: int | None = None,
) -> str | None:
    """解析 HLS BYTERANGE 长度和可选起始偏移。"""
    if not byterange:
        return None
    if "@" in byterange:
        length_text, start_text = byterange.split("@", 1)
        start = int(start_text)
    else:
        # RFC 8216 规定省略 @offset 时紧接前一范围，因此调用方必须按
        # playlist 顺序维护 previous_end，不能为每个分片重新从 0 开始。
        start = 0 if previous_end is None else previous_end + 1
        length_text = byterange
    length = int(length_text)
    end = start + length - 1
    return f"bytes={start}-{end}"


def byterange_end(range_header: str | None) -> int | None:
    """计算当前字节区间的闭区间结束位置。"""
    if not range_header:
        return None
    _, _, end_text = range_header.rpartition("-")
    return int(end_text)


def group_fmp4_segments(items: list[dict]) -> list[dict]:
    """按 init map 和 discontinuity 把 fMP4 分片划分为合并组。"""
    groups = []
    current_group = None
    for item in items:
        init_map_url = item.get("init_map_url")
        # init map 改变意味着后续分片属于新的初始化上下文；即使 map
        # 相同，显式 discontinuity 也必须阻断二进制直连。
        starts_new_group = (
            current_group is None
            or current_group["init_map_url"] != init_map_url
            or bool(item.get("discontinuity"))
        )
        if starts_new_group:
            current_group = {"init_map_url": init_map_url, "segments": []}
            groups.append(current_group)
        current_group["segments"].append(item["save_path"])
    return groups


def ordered_ts_segments(items: list[dict]) -> list[str]:
    """保持原清单顺序返回传统 TS 分片列表。"""
    return [item["save_path"] for item in items]


def is_live_playlist(playlist) -> bool:
    """通过 ENDLIST 状态判断清单是否仍在滚动更新。"""
    if str(getattr(playlist, "playlist_type", "")).upper() == "VOD":
        return False
    return not bool(getattr(playlist, "is_endlist", True))


class HlsAdapter:
    """处理 HLS 清单解析、分片下载、解密、多轨和 FFmpeg 合并。"""
    name = "hls"
    priority = 80

    def __init__(
        self,
        *,
        output_dir: str,
        temp_dir: str,
        headers_getter: Callable[[], dict[str, str]],
        log_callback: Callable[[str], None] | None = None,
        is_high_speed: bool = False,
        segment_concurrency: int = 5,
        resume_enabled: bool = True,
        live_record_seconds: int = 300,
        verify_output: Callable[[str], str] | None = None,
        download_m3u8: Callable[[str, str], object] | None = None,
        download_ts: Callable[[object, str, str, object, dict | None], object] | None = None,
        merge_with_ffmpeg: Callable[[list, str, str | None], str] | None = None,
    ):
        """初始化 HLS 下载策略及可选兼容钩子。

        `headers_getter` 使用回调而非固定字典，使网页嗅探结束后新增的
        Cookie/Referer 能被后续密钥、清单和分片请求立即读取。三个 hook
        只用于旧接口兼容和测试注入，正常运行使用适配器自身实现。
        """
        self.output_dir = output_dir
        self.temp_dir = temp_dir
        self.headers_getter = headers_getter
        self.log_callback = log_callback
        self.is_high_speed = is_high_speed
        self.segment_concurrency = segment_concurrency
        self.resume_enabled = resume_enabled
        self.live_record_seconds = int(live_record_seconds)
        self.verify_output = verify_output
        self._download_m3u8_hook = download_m3u8
        self._download_ts_hook = download_ts
        self._merge_with_ffmpeg_hook = merge_with_ffmpeg
        self._hls_key_cache = {}

    @property
    def headers(self) -> dict[str, str]:
        """返回当前下载会话请求头的防御性副本。"""
        return self.headers_getter()

    def log(self, message: str) -> None:
        """将 HLS 下载状态转发给外部日志回调。"""
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def can_handle(self, candidate: MediaCandidate) -> bool:
        """仅接受分类为 HLS 的媒体候选。"""
        return candidate.kind == MediaKind.HLS

    def diagnose(self, url: str) -> DiagnosticReport:
        """生成 HLS 能力说明和候选诊断信息。"""
        return DiagnosticReport(
            source_url=url,
            candidates=[
                MediaCandidate(
                    url=url,
                    kind=MediaKind.HLS,
                    source=self.name,
                    score=100,
                )
            ],
        )

    def download(self, candidate: MediaCandidate, output_filename: str) -> str:
        """下载 HLS 点播或直播流，并校验合并后的输出文件。"""
        if self._download_m3u8_hook:
            result = self._download_m3u8_hook(candidate.url, output_filename)
        else:
            result = self.download_url(candidate.url, output_filename)
        if inspect.isawaitable(result):
            return self._run_async(result)
        return result

    def _run_async(self, awaitable):
        """在同步调用边界运行协程，并处理已有事件循环场景。"""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(awaitable)
        finally:
            loop.close()

    def _verify_output(self, output_path: str) -> str:
        """检查输出路径是否存在且文件大小大于零。"""
        if self.verify_output:
            return self.verify_output(output_path)
        if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
            raise VideoDownloadError(
                VideoErrorCode.EMPTY_OUTPUT,
                f"输出文件不存在或为空: {output_path}",
                details={"output_path": output_path},
                retryable=False,
            )
        return output_path

    def _load_playlist(self, playlist_url: str):
        """加载 HLS 清单，并将超时和解析失败映射为结构化错误。"""
        try:
            return m3u8.load(playlist_url, headers=self.headers)
        except Exception as exc:
            if is_timeout_like_error(exc):
                raise VideoDownloadError(
                    VideoErrorCode.NETWORK_TIMEOUT,
                    f"读取 M3U8 超时或网络不可达: {playlist_url}",
                    details={"url": playlist_url, "reason": str(exc)},
                    retryable=True,
                ) from exc
            if isinstance(exc, URLError):
                raise VideoDownloadError(
                    VideoErrorCode.M3U8_PARSE_FAILED,
                    f"读取 M3U8 失败: {playlist_url}",
                    details={"url": playlist_url, "reason": str(exc)},
                    retryable=False,
                ) from exc
            raise

    def _remove_temp_file(self, file_path):
        """尽力删除临时文件，清理失败只记录警告。"""
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception as cleanup_error:
            self.log(f"[!] 临时文件清理失败 ({file_path}): {cleanup_error}")

    def _validate_segment_failures(self, failed_count, total_count):
        """计算失败比例，超过容忍阈值时阻止不完整合并。"""
        if failed_count <= 0:
            return
        if total_count <= 0 or failed_count * 100 > total_count * 3:
            raise VideoDownloadError(
                VideoErrorCode.SEGMENT_FAILURE_RATE_EXCEEDED,
                f"有 {failed_count} 个切片下载失败（总计 {total_count} 个），"
                f"超过允许的 3%",
                details={"failed_count": failed_count, "total_count": total_count},
                retryable=True,
            )
        failed_percent = failed_count * 100 / total_count
        self.log(
            f"[!] 警告: 有 {failed_count}/{total_count} 个切片失败"
            f"({failed_percent:.2f}%)，未超过 3%，将继续合并。"
        )

    async def download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
        """带重试、退避、Range 和可选 AES 解密地下载单个分片。

        高速模式减少重试和随机等待；稳定模式通过抖动与递增退避降低
        瞬时请求峰值，遇到 429/503 时再额外延长等待。
        """
        retries = 3 if self.is_high_speed else 5
        for attempt in range(retries):
            wait_time = 1
            try:
                if not self.is_high_speed:
                    await asyncio.sleep(random.uniform(0.1, 0.6))

                timeout_val = 15 if self.is_high_speed else 20
                timeout = aiohttp.ClientTimeout(total=timeout_val)

                async with session.get(
                    ts_url,
                    timeout=timeout,
                    headers=extra_headers or {},
                ) as response:
                    response.raise_for_status()
                    content = await response.read()
                    if cipher:
                        # cryptography 的 Cipher 对象可复用配置，但 decryptor
                        # 带内部状态，必须为每个独立分片创建新实例。
                        decryptor = cipher.decryptor()
                        content = decryptor.update(content) + decryptor.finalize()
                    with open(save_path, "wb") as segment_file:
                        segment_file.write(content)
                    return True
            except Exception as exc:
                if attempt == retries - 1:
                    self.log(f"[!] 切片 {ts_url[-10:]} 彻底失败 (已重试{retries}次): {exc}")
                else:
                    if self.is_high_speed:
                        await asyncio.sleep(1)
                    else:
                        wait_time = (attempt + 1) * 2
                        if "503" in str(exc) or "429" in str(exc):
                            wait_time += 3
                await asyncio.sleep(wait_time)
        return False

    def normalize_download_item(self, item, fallback_cipher):
        """规范分片字典并解析绝对 URL、范围和媒体序号。"""
        if isinstance(item, dict):
            return {
                "url": item["url"],
                "save_path": item["save_path"],
                "cipher": item.get("cipher", fallback_cipher),
                "extra_headers": (
                    {"Range": item["range_header"]}
                    if item.get("range_header")
                    else item.get("extra_headers", {})
                ),
                "manifest": item.get("manifest"),
                "manifest_filename": item.get("manifest_filename"),
            }
        url, save_path = item
        return {
            "url": url,
            "save_path": save_path,
            "cipher": fallback_cipher,
            "extra_headers": {},
            "manifest": None,
            "manifest_filename": None,
        }

    async def download_segments(self, session, download_items, cipher):
        """使用信号量限制并发，下载分片并持续更新续传清单。

        所有协程可一次创建，但只有获得 semaphore 的任务会发起网络请求。
        下载全部结束后才按原顺序更新 manifest，避免并发写同一个 JSON。
        """
        semaphore = asyncio.Semaphore(self.segment_concurrency)
        normalized_items = [
            self.normalize_download_item(item, cipher)
            for item in download_items
        ]
        download_ts = self._download_ts_hook or self.download_ts

        async def bounded_download(item):
            """在信号量保护范围内下载单项并收集失败信息。"""
            async with semaphore:
                return await download_ts(
                    session,
                    item["url"],
                    item["save_path"],
                    item["cipher"],
                    item["extra_headers"],
                )

        results = await asyncio.gather(*(
            bounded_download(item) for item in normalized_items
        ))
        for item, result in zip(normalized_items, results):
            manifest = item.get("manifest")
            if not result or manifest is None:
                continue
            save_path = item["save_path"]
            if os.path.exists(save_path):
                manifest.mark_downloaded(
                    item.get("manifest_filename") or os.path.basename(save_path),
                    url=item["url"],
                    size=os.path.getsize(save_path),
                )
                # 当前 manifest 使用固定 .tmp + os.replace；Windows 文件占用
                # 可能导致 PermissionError，这是已知但尚未加固的恢复风险。
                manifest.save()
        failed_count = results.count(False)
        self._validate_segment_failures(failed_count, len(results))

    def build_segment_cipher(self, key, media_sequence):
        """读取 HLS 密钥并按分片序号创建 AES-CBC 解密器。"""
        if not key:
            return None
        key_url = key.absolute_uri
        # 同一密钥通常覆盖连续多个分片，缓存原始 key bytes 可避免每片
        # 都同步请求密钥服务器。
        if key_url not in self._hls_key_cache:
            key_response = requests.get(key_url, headers=self.headers, timeout=15)
            key_response.raise_for_status()
            self._hls_key_cache[key_url] = key_response.content
        iv = derive_hls_iv(getattr(key, "iv", None), media_sequence)
        return Cipher(
            algorithms.AES(self._hls_key_cache[key_url]),
            modes.CBC(iv),
            backend=default_backend(),
        )

    async def download_url(self, m3u8_url: str, output_filename: str):
        """解析 HLS 入口，选择最高码率变体并下载点播或直播内容。"""
        playlist = self._load_playlist(m3u8_url)
        if playlist.is_variant:
            rendition_plan = build_hls_rendition_plan(playlist)
            playlists = list(playlist.playlists)
            # HLS master 的 bandwidth 是播放器进行自适应选择的主要指标；
            # 这里固定选最高值，避免网络波动导致程序主动降画质。
            playlists.sort(
                key=lambda item: item.stream_info.bandwidth
                if item.stream_info.bandwidth
                else 0,
                reverse=True,
            )
            video_url = playlists[0].absolute_uri
            self.log("[*] 检测到多画质变体，已自动选择最高画质流...")
            if rendition_plan.audio_url or rendition_plan.subtitle_url:
                return await self.download_master_with_renditions(
                    video_url=video_url,
                    audio_url=rendition_plan.audio_url,
                    subtitle_url=rendition_plan.subtitle_url,
                    output_filename=output_filename,
                )
            m3u8_url = video_url
        return await self._download_playlist_to_mp4(m3u8_url, output_filename)

    async def download_media_playlist(
        self,
        m3u8_url: str,
        output_filename: str,
        suffix: str,
    ) -> str:
        """下载单个媒体清单并合并为临时 MP4。"""
        media_output_name = f"{output_filename}.{suffix}"
        return await self._download_playlist_to_mp4(m3u8_url, media_output_name)

    async def download_master_with_renditions(
        self,
        *,
        video_url: str,
        audio_url: str | None,
        subtitle_url: str | None,
        output_filename: str,
    ) -> str:
        """下载主视频流及默认音轨/字幕，再执行最终封装。"""
        video_path = await self.download_media_playlist(video_url, output_filename, "video")
        audio_path = None
        subtitle_path = None
        if audio_url:
            audio_path = await self.download_media_playlist(audio_url, output_filename, "audio")
        if subtitle_url:
            subtitle_path = self.download_subtitle_playlist(subtitle_url, output_filename)

        final_path = os.path.join(self.output_dir, f"{output_filename}.mp4")
        if audio_path or subtitle_path:
            return self.mux_renditions(video_path, audio_path, subtitle_path, final_path)
        if video_path != final_path:
            os.replace(video_path, final_path)
        return self._verify_output(final_path)

    def mux_renditions(
        self,
        video_path: str,
        audio_path: str | None,
        subtitle_path: str | None,
        output_path: str,
    ) -> str:
        """调用 FFmpeg 把视频、可选音频和字幕封装为目标 MP4。

        输入索引会随音轨/字幕是否存在而变化，因此 map 参数必须按四种
        组合显式构造，不能依赖 FFmpeg 的默认流选择。
        """
        command = ["ffmpeg", "-y", "-i", video_path]
        if audio_path:
            command.extend(["-i", audio_path])
        if subtitle_path:
            command.extend(["-i", subtitle_path])

        command.extend(["-map", "0:v:0"])
        if audio_path:
            command.extend(["-map", "1:a:0"])
        elif subtitle_path:
            command.extend(["-map", "0:a?"])
            command.extend(["-map", "1:s:0"])
        if audio_path and subtitle_path:
            command.extend(["-map", "2:s:0"])

        command.extend(["-c:v", "copy"])
        if audio_path:
            command.extend(["-c:a", "copy"])
        else:
            command.extend(["-c:a?", "copy"])
        if subtitle_path:
            command.extend(["-c:s", "mov_text"])
        command.append(output_path)

        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            error = exc.stderr.decode("utf-8", errors="ignore")
            raise VideoDownloadError(
                VideoErrorCode.FFMPEG_FAILED,
                f"FFmpeg 合并失败: {error}",
                retryable=False,
            ) from exc
            raise VideoDownloadError(
                VideoErrorCode.FFMPEG_FAILED,
                f"FFmpeg HLS 多轨 mux 失败: {error}",
                retryable=False,
            ) from exc
        return self._verify_output(output_path)

    def download_subtitle_playlist(self, subtitle_url: str, output_filename: str) -> str:
        """下载 WebVTT 字幕分片并拼接为单个字幕文件。"""
        playlist = self._load_playlist(subtitle_url)
        output_path = os.path.join(self.temp_dir, f"{output_filename}.vtt")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as output:
            output.write("WEBVTT\n\n")
            for segment in playlist.segments:
                response = requests.get(
                    segment.absolute_uri,
                    headers=self.headers,
                    timeout=15,
                )
                response.raise_for_status()
                text = response.content.decode("utf-8", errors="replace")
                text = text.replace("WEBVTT", "", 1).lstrip()
                output.write(text)
                output.write("\n")
        return output_path

    async def download_live_playlist(self, m3u8_url: str, output_filename: str) -> str:
        """在录制窗口内轮询直播清单并去重收集新分片。

        直播清单是滑动窗口，旧分片会被移除。使用绝对 URL 去重，并保存
        首次观察到的媒体序号，才能在录制结束后恢复播放顺序和默认 IV。
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.live_record_seconds
        seen_urls = set()
        captured_items = []
        while loop.time() < deadline:
            playlist = self._load_playlist(m3u8_url)
            media_sequence = getattr(playlist, "media_sequence", 0) or 0
            for index, segment in enumerate(playlist.segments):
                if segment.absolute_uri in seen_urls:
                    continue
                seen_urls.add(segment.absolute_uri)
                captured_items.append((media_sequence + index, segment))
            # 按 target duration 轮询可避免无意义的高频请求；最小 1 秒
            # 防止异常清单给出 0 导致忙循环。
            await asyncio.sleep(max(1, float(getattr(playlist, "target_duration", 2) or 2)))
        if not captured_items:
            raise VideoDownloadError(
                VideoErrorCode.NO_MEDIA_FOUND,
                "直播 playlist 在录制窗口内没有产生切片。",
                retryable=True,
            )
        return await self.download_collected_live_segments(
            captured_items,
            output_filename,
        )

    async def download_collected_live_segments(
        self,
        captured_items: list[tuple[int, object]],
        output_filename: str,
    ) -> str:
        """下载已收集的直播分片并合并输出。"""
        if not captured_items:
            raise VideoDownloadError(
                VideoErrorCode.NO_MEDIA_FOUND,
                "直播 playlist 在录制窗口内没有产生切片。",
                retryable=True,
            )

        video_temp_dir = os.path.join(self.temp_dir, output_filename)
        os.makedirs(video_temp_dir, exist_ok=True)
        ts_files_list = []
        download_items = []
        previous_range_end = None

        try:
            for sequence_number, segment in captured_items:
                filename = f"{sequence_number:05d}.ts"
                save_path = os.path.join(video_temp_dir, filename)
                ts_files_list.append(save_path)

                byterange = getattr(segment, "byterange", None)
                range_header = parse_hls_byterange(
                    str(byterange) if byterange else None,
                    previous_range_end,
                )
                if range_header:
                    previous_range_end = byterange_end(range_header)

                segment_cipher = self.build_segment_cipher(
                    getattr(segment, "key", None),
                    sequence_number,
                )
                download_items.append({
                    "url": segment.absolute_uri,
                    "save_path": save_path,
                    "cipher": segment_cipher,
                    "range_header": range_header,
                    "manifest": None,
                    "manifest_filename": filename,
                })

            async with aiohttp.ClientSession(headers=self.headers, trust_env=True) as session:
                await self.download_segments(session, download_items, None)

            final_mp4_path = os.path.join(self.output_dir, f"{output_filename}.mp4")
            merge = self._merge_with_ffmpeg_hook or self.merge_with_ffmpeg
            merge(ts_files_list, final_mp4_path, None)
            return self._verify_output(final_mp4_path)
        finally:
            for file_path in ts_files_list:
                self._remove_temp_file(file_path)
            try:
                if os.path.isdir(video_temp_dir):
                    os.rmdir(video_temp_dir)
            except Exception as cleanup_error:
                self.log(f"[!] 临时文件清理失败: {cleanup_error}")

    async def _download_playlist_to_mp4(self, m3u8_url: str, output_filename: str):
        """下载点播媒体清单、复用断点分片并合并为 MP4。

        当续传启用时，失败路径保留分片与 manifest；只有成功输出或明确
        禁用续传时才清理。这样清理异常不会掩盖原始下载错误。
        """
        playlist = self._load_playlist(m3u8_url)

        if is_live_playlist(playlist):
            return await self.download_live_playlist(m3u8_url, output_filename)

        if playlist.is_variant:
            playlists = list(playlist.playlists)
            playlists.sort(
                key=lambda item: item.stream_info.bandwidth
                if item.stream_info.bandwidth
                else 0,
                reverse=True,
            )
            m3u8_url = playlists[0].absolute_uri
            self.log("[*] 检测到多画质变体，已自动选择最高画质流...")
            playlist = self._load_playlist(m3u8_url)

        video_temp_dir = os.path.join(self.temp_dir, output_filename)
        os.makedirs(video_temp_dir, exist_ok=True)
        init_file_path = None
        ts_files_list = []
        manifest = None
        manifest_path = os.path.join(video_temp_dir, ".firefly-segments.json")
        # 成功前保持 False 可让 finally 区分“应保留以续传”与“可清理”。
        cleanup_temp_files = not self.resume_enabled
        if self.resume_enabled:
            manifest = SegmentManifest(manifest_path)
            manifest.load()

        try:
            if playlist.segment_map:
                self.log("[+] 检测到 fMP4 格式，正在下载 Init 初始化文件 (EXT-X-MAP)...")
                init_url = playlist.segment_map[0].absolute_uri
                init_file_path = os.path.join(video_temp_dir, "init.mp4")
                response = requests.get(init_url, headers=self.headers, timeout=15)
                response.raise_for_status()
                with open(init_file_path, "wb") as init_file:
                    init_file.write(response.content)

            self.log(f"[*] 共发现 {len(playlist.segments)} 个数据切片，准备下载...")
            download_items = []
            previous_range_end = None
            media_sequence = getattr(playlist, "media_sequence", 0) or 0
            for index, segment in enumerate(playlist.segments):
                filename = f"{index:05d}.ts"
                save_path = os.path.join(video_temp_dir, filename)
                ts_files_list.append(save_path)

                byterange = getattr(segment, "byterange", None)
                range_header = parse_hls_byterange(
                    str(byterange) if byterange else None,
                    previous_range_end,
                )
                if range_header:
                    previous_range_end = byterange_end(range_header)

                # manifest 记录还不够；必须同时验证本地文件存在、非空且
                # 大小一致，防止上次崩溃留下半个分片却被误判为完成。
                if (
                    manifest is not None
                    and os.path.exists(save_path)
                    and os.path.getsize(save_path) > 0
                    and manifest.is_downloaded(
                        filename,
                        expected_size=os.path.getsize(save_path),
                    )
                ):
                    self.log(f"[*] 跳过已完成切片: {filename}")
                    continue

                segment_key = getattr(segment, "key", None)
                if segment_key is None and playlist.keys:
                    segment_key = playlist.keys[0]
                segment_cipher = self.build_segment_cipher(
                    segment_key,
                    media_sequence + index,
                )
                if segment_cipher:
                    self.log("[+] 检测到加密，已加载 AES 引擎。")

                download_items.append({
                    "url": segment.absolute_uri,
                    "save_path": save_path,
                    "cipher": segment_cipher,
                    "range_header": range_header,
                    "init_map_url": (
                        segment.init_section.absolute_uri
                        if getattr(segment, "init_section", None)
                        else None
                    ),
                    "discontinuity": bool(getattr(segment, "discontinuity", False)),
                    "manifest": manifest,
                    "manifest_filename": filename,
                })

            async with aiohttp.ClientSession(headers=self.headers, trust_env=True) as session:
                self.log(f"[*] 当前并发数限制设为: {self.segment_concurrency}")
                await self.download_segments(session, download_items, None)

            self.log("[+] 切片处理完成，开始执行合并与转码...")
            final_mp4_path = os.path.join(self.output_dir, f"{output_filename}.mp4")
            merge = self._merge_with_ffmpeg_hook or self.merge_with_ffmpeg
            merge(ts_files_list, final_mp4_path, init_file_path)
            self.log("[+] 任务彻底完成！")
            verified_path = self._verify_output(final_mp4_path)
            cleanup_temp_files = True
            return verified_path
        except VideoDownloadError:
            raise
        except Exception as exc:
            raise VideoDownloadError(f"M3U8 下载失败: {exc}") from exc
        finally:
            if cleanup_temp_files:
                for file_path in ts_files_list:
                    self._remove_temp_file(file_path)
                self._remove_temp_file(init_file_path)
                self._remove_temp_file(manifest_path)
                try:
                    if os.path.isdir(video_temp_dir):
                        os.rmdir(video_temp_dir)
                    self.log("[+] 临时文件已清理。")
                except Exception as cleanup_error:
                    self.log(f"[!] 临时文件清理失败: {cleanup_error}")
            else:
                self.log("[*] 下载未完成，保留临时切片用于后续恢复。")

    def merge_with_ffmpeg(self, ts_files: list, output_mp4: str, init_file: str = None):
        """按 TS 或 fMP4 路径调用 FFmpeg 修复并生成最终容器。

        fMP4 分片需要先按 `init + fragments` 二进制拼接，再让 FFmpeg
        重建容器元数据；传统 TS 则使用 concat demuxer 保持分片顺序。
        两条路径都只做 stream copy，避免不必要的重新编码和画质损失。
        """
        valid_files = [path for path in ts_files if os.path.exists(path)]
        self._validate_segment_failures(len(ts_files) - len(valid_files), len(ts_files))
        if not valid_files:
            raise VideoDownloadError("没有任何有效切片，合并任务中止")

        if init_file and os.path.exists(init_file):
            # FFmpeg 不能直接把孤立的 m4s 片段当作完整输入，初始化段必须
            # 位于所有媒体 fragment 之前。
            raw_file_path = output_mp4 + ".raw.mp4"
            repaired_file_path = output_mp4 + ".repaired.mp4"
            try:
                with open(raw_file_path, "wb") as output_file:
                    with open(init_file, "rb") as init_input:
                        output_file.write(init_input.read())
                    for segment_path in valid_files:
                        with open(segment_path, "rb") as segment_input:
                            output_file.write(segment_input.read())

                command = [
                    "ffmpeg",
                    "-y",
                    "-i", raw_file_path,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    repaired_file_path,
                ]
                subprocess.run(
                    command,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                os.replace(repaired_file_path, output_mp4)
            except subprocess.CalledProcessError as exc:
                error = exc.stderr.decode("utf-8", errors="ignore")
                raise VideoDownloadError(
                    VideoErrorCode.FFMPEG_FAILED,
                    f"FFmpeg 修复容器失败: {error}",
                    retryable=False,
                ) from exc
                raise VideoDownloadError(f"FFmpeg 修复容器失败: {error}") from exc
            except Exception as exc:
                if isinstance(exc, VideoDownloadError):
                    raise
                raise VideoDownloadError(f"fMP4 合并失败: {exc}") from exc
            finally:
                self._remove_temp_file(raw_file_path)
                self._remove_temp_file(repaired_file_path)
            return self._verify_output(output_mp4)

        # concat 清单使用绝对 POSIX 风格路径，并配合 -safe 0 允许 Windows
        # 盘符；直接拼接 TS 字节可能在 discontinuity 处产生时间戳问题。
        concat_list_path = os.path.join(os.path.dirname(output_mp4), "concat_list.txt")
        try:
            with open(concat_list_path, "w", encoding="utf-8") as list_file:
                for segment_path in valid_files:
                    safe_path = os.path.abspath(segment_path).replace("\\", "/")
                    list_file.write(f"file '{safe_path}'\n")

            self.log("[*] 正在执行 FFmpeg 标准流合并，请稍候...")
            command = [
                "ffmpeg",
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                "-bsf:a", "aac_adtstoasc",
                output_mp4,
            ]
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            error = exc.stderr.decode("utf-8", errors="ignore")
            raise VideoDownloadError(
                VideoErrorCode.FFMPEG_FAILED,
                f"FFmpeg 合并失败: {error}",
                retryable=False,
            ) from exc
            raise VideoDownloadError(f"FFmpeg 合并失败: {error}") from exc
        except Exception as exc:
            if isinstance(exc, VideoDownloadError):
                raise
            raise VideoDownloadError(f"视频合并失败: {exc}") from exc
        finally:
            self._remove_temp_file(concat_list_path)

        self.log(f"[+] 视频成功合并并修复，保存至:\n {output_mp4}")
        return self._verify_output(output_mp4)
