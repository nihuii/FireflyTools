from dataclasses import dataclass
import math
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from urllib.parse import urljoin

import requests

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import DiagnosticReport, MediaCandidate, MediaKind
from tools.video_crawler.resume import SegmentManifest


@dataclass(frozen=True)
class MpdCapabilities:
    has_video: bool
    has_audio: bool
    has_drm: bool


@dataclass(frozen=True)
class DashTrackPlan:
    kind: str
    representation_id: str
    bandwidth: int
    urls: list[str]


@dataclass(frozen=True)
class DashDownloadPlan:
    video: DashTrackPlan
    audio: DashTrackPlan


def _strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _children_named(element, name: str):
    return [child for child in list(element) if _strip_namespace(child.tag) == name]


def _first_child_named(element, name: str):
    for child in list(element):
        if _strip_namespace(child.tag) == name:
            return child
    return None


def _base_url(parent_base: str, element) -> str:
    current = parent_base
    base = _first_child_named(element, "BaseURL")
    if base is not None and base.text:
        current = urljoin(current, base.text.strip())
    return current


def _parse_iso8601_duration_seconds(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?",
        value,
    )
    if not match:
        return None
    days = float(match.group("days") or 0)
    hours = float(match.group("hours") or 0)
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_mpd_capabilities(mpd_text: str) -> MpdCapabilities:
    root = ET.fromstring(mpd_text)
    has_video = False
    has_audio = False
    has_drm = False
    for element in root.iter():
        name = _strip_namespace(element.tag)
        if name == "ContentProtection":
            has_drm = True
        if name == "AdaptationSet":
            mime_type = element.attrib.get("mimeType", "")
            content_type = element.attrib.get("contentType", "")
            if mime_type.startswith("video/") or content_type == "video":
                has_video = True
            if mime_type.startswith("audio/") or content_type == "audio":
                has_audio = True
    return MpdCapabilities(
        has_video=has_video,
        has_audio=has_audio,
        has_drm=has_drm,
    )


def _adaptation_kind(adaptation_set) -> str | None:
    mime_type = adaptation_set.attrib.get("mimeType", "")
    content_type = adaptation_set.attrib.get("contentType", "")
    if mime_type.startswith("video/") or content_type == "video":
        return "video"
    if mime_type.startswith("audio/") or content_type == "audio":
        return "audio"
    return None


def _best_representation(adaptation_set):
    representations = _children_named(adaptation_set, "Representation")
    if not representations:
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "DASH AdaptationSet 缺少 Representation。",
            retryable=False,
        )
    return max(
        representations,
        key=lambda item: int(item.attrib.get("bandwidth", "0") or 0),
    )


def _segment_template_for(adaptation_set, representation):
    template = _first_child_named(representation, "SegmentTemplate")
    if template is None:
        template = _first_child_named(adaptation_set, "SegmentTemplate")
    if template is None:
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "当前仅支持 SegmentTemplate 形式的静态 MPD。",
            retryable=False,
        )
    if _first_child_named(template, "SegmentTimeline") is not None:
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "当前暂不支持 SegmentTimeline。",
            retryable=False,
        )
    return template


def _track_plan(
    *,
    kind: str,
    adaptation_set,
    period_base: str,
    total_seconds: float,
) -> DashTrackPlan:
    representation = _best_representation(adaptation_set)
    template = _segment_template_for(adaptation_set, representation)

    initialization = template.attrib.get("initialization")
    media = template.attrib.get("media")
    duration = int(template.attrib.get("duration", "0") or 0)
    timescale = int(template.attrib.get("timescale", "1") or 1)
    if not initialization or not media or duration <= 0 or timescale <= 0:
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "SegmentTemplate 缺少 initialization/media/duration/timescale。",
            retryable=False,
        )

    representation_id = representation.attrib.get("id", "")
    bandwidth = int(representation.attrib.get("bandwidth", "0") or 0)
    segment_seconds = duration / timescale
    segment_count = max(1, int(math.ceil(total_seconds / segment_seconds)))
    start_number = int(template.attrib.get("startNumber", "1") or 1)
    base = _base_url(_base_url(period_base, adaptation_set), representation)

    def expand(template_text: str, number: int | None = None) -> str:
        value = template_text.replace("$RepresentationID$", representation_id)
        if number is not None:
            value = value.replace("$Number$", str(number))
        return urljoin(base, value)

    urls = [expand(initialization)]
    urls.extend(
        expand(media, start_number + index)
        for index in range(segment_count)
    )
    return DashTrackPlan(
        kind=kind,
        representation_id=representation_id,
        bandwidth=bandwidth,
        urls=urls,
    )


def build_static_segment_template_plan(
    mpd_text: str,
    mpd_url: str,
) -> DashDownloadPlan:
    root = ET.fromstring(mpd_text)
    if root.attrib.get("type", "static") != "static":
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "当前暂不支持动态/直播 DASH。",
            retryable=False,
        )
    if parse_mpd_capabilities(mpd_text).has_drm:
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DRM,
            "发现 DRM 保护内容，本工具不会绕过 DRM。",
            retryable=False,
        )

    total_seconds = _parse_iso8601_duration_seconds(
        root.attrib.get("mediaPresentationDuration")
    )
    period = _first_child_named(root, "Period")
    if period is None:
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "MPD 缺少 Period。",
            retryable=False,
        )
    if total_seconds is None:
        total_seconds = _parse_iso8601_duration_seconds(period.attrib.get("duration"))
    if total_seconds is None or total_seconds <= 0:
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "静态 MPD 缺少可解析的媒体时长。",
            retryable=False,
        )

    root_base = _base_url(mpd_url, root)
    period_base = _base_url(root_base, period)
    plans = {}
    for adaptation_set in _children_named(period, "AdaptationSet"):
        kind = _adaptation_kind(adaptation_set)
        if kind in {"video", "audio"}:
            plans[kind] = _track_plan(
                kind=kind,
                adaptation_set=adaptation_set,
                period_base=period_base,
                total_seconds=total_seconds,
            )
    if "video" not in plans or "audio" not in plans:
        raise VideoDownloadError(
            VideoErrorCode.UNSUPPORTED_DASH,
            "DASH 第一版需要同时包含视频轨和音频轨。",
            retryable=False,
        )
    return DashDownloadPlan(video=plans["video"], audio=plans["audio"])


class DashAdapter:
    name = "dash"
    priority = 60

    def __init__(
        self,
        *,
        output_dir: str,
        temp_dir: str,
        headers_getter: Callable[[], dict[str, str]],
        log_callback: Callable[[str], None] | None = None,
        fetch_url: Callable[[str], bytes] | None = None,
        segment_concurrency: int = 5,
    ):
        self.output_dir = output_dir
        self.temp_dir = temp_dir
        self.headers_getter = headers_getter
        self.log_callback = log_callback
        self._fetch_url_hook = fetch_url
        self.segment_concurrency = max(1, int(segment_concurrency))

    def log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def can_handle(self, candidate: MediaCandidate) -> bool:
        return candidate.kind == MediaKind.DASH

    def diagnose(self, url: str) -> DiagnosticReport:
        return DiagnosticReport(
            source_url=url,
            candidates=[
                MediaCandidate(
                    url=url,
                    kind=MediaKind.DASH,
                    source=self.name,
                    score=70,
                )
            ],
        )

    def fetch_url(self, url: str) -> bytes:
        if self._fetch_url_hook:
            return self._fetch_url_hook(url)
        response = requests.get(url, headers=self.headers_getter(), timeout=20)
        response.raise_for_status()
        return response.content

    def download(self, candidate: MediaCandidate, output_filename: str) -> str:
        mpd_text = self.fetch_url(candidate.url).decode("utf-8", errors="replace")
        plan = build_static_segment_template_plan(mpd_text, candidate.url)
        output_path = os.path.join(self.output_dir, f"{output_filename}.mp4")
        work_dir = os.path.join(self.temp_dir, output_filename, "dash")
        os.makedirs(work_dir, exist_ok=True)
        cleanup_work_dir = False

        try:
            video_path = self._write_track(plan.video, work_dir)
            audio_path = self._write_track(plan.audio, work_dir)
            self._mux_tracks(video_path, audio_path, output_path)
            verified = self._verify_output(output_path)
            cleanup_work_dir = True
            return verified
        finally:
            if cleanup_work_dir:
                try:
                    if os.path.isdir(work_dir):
                        shutil.rmtree(work_dir)
                except Exception as cleanup_error:
                    self.log(f"[!] DASH 临时目录清理失败: {cleanup_error}")

    def _write_track(self, track: DashTrackPlan, work_dir: str) -> str:
        track_path = os.path.join(work_dir, f"{track.kind}.mp4")
        manifest_path = os.path.join(work_dir, f"{track.kind}.firefly-segments.json")
        manifest = SegmentManifest(manifest_path)
        manifest.load()
        segment_paths = []
        self.log(f"[*] 下载 DASH {track.kind} 轨: {track.representation_id}")
        for index, url in enumerate(track.urls):
            filename = f"{track.kind}-{index:05d}.m4s"
            segment_path = os.path.join(work_dir, filename)
            segment_paths.append(segment_path)
            if (
                os.path.exists(segment_path)
                and os.path.getsize(segment_path) > 0
                and manifest.is_downloaded(
                    filename,
                    expected_size=os.path.getsize(segment_path),
                )
            ):
                continue
            content = self.fetch_url(url)
            with open(segment_path, "wb") as segment_file:
                segment_file.write(content)
            manifest.mark_downloaded(filename, url=url, size=len(content))
            manifest.save()
        with open(track_path, "wb") as output_file:
            for segment_path in segment_paths:
                with open(segment_path, "rb") as segment_file:
                    output_file.write(segment_file.read())
        return track_path

    def _mux_tracks(self, video_path: str, audio_path: str, output_path: str) -> None:
        command = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c", "copy",
            output_path,
        ]
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            error = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else str(exc)
            raise VideoDownloadError(
                VideoErrorCode.FFMPEG_FAILED,
                f"FFmpeg mux 失败: {error}",
                retryable=False,
            ) from exc

    def _verify_output(self, output_path: str) -> str:
        if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
            raise VideoDownloadError(
                VideoErrorCode.EMPTY_OUTPUT,
                f"输出文件不存在或为空: {output_path}",
                details={"output_path": output_path},
                retryable=False,
            )
        return output_path
