# Video Crawler Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 `plan/video-crawler-roadmap.md` 与 `plan/video-crawler-phased-implementation-plan.md` 对照后曾确认的能力差距，让视频爬虫在敏感信息脱敏、浏览器会话继承、复杂 HLS、DASH 断点续传、结构化错误和适配器拆分上达到计划验收标准。

**Architecture:** 保持 `tools/video_downloader.py` 作为主界面入口，继续把下载、诊断、脱敏和适配器逻辑下沉到 `tools/video_crawler/`。每个待补项先补失败测试，再用小范围模块改动闭环，避免把主界面改成复杂专业面板。所有新增 UI 控件和弹窗仍需和主界面现有 PyQt6 + 动态 QSS 风格保持一致，沿用无边框窗口、壁纸背景、磨砂面板、明暗主题和 `QMessageBox` 可读性适配。

**Tech Stack:** Python 3.x, PyQt6, requests, aiohttp, m3u8, Playwright, FFmpeg, unittest.

---

## 0. 现状核查摘要

本计划阶段 A-G 已处理并验证以下事项：

1. 诊断报告、运行日志和队列详情统一经过敏感信息脱敏，避免展示 `token=...` 等原始敏感值。
2. 浏览器会话继承已补齐 LocalStorage 和媒体请求临时授权头捕获，并安全合并到下载请求。
3. HLS 已补齐默认多音轨、字幕和直播滚动 playlist 轻量录制。
4. DASH 已支持无 DRM 静态 SegmentTemplate 下载、mux 和 track 级断点续传。
5. HLS/DASH FFmpeg 失败路径已收口到结构化 `FFMPEG_FAILED`。
6. `tools/video_downloader.py` 当前 379 行，已低于 Phase 7 中“迁移后约 450 行以下”的目标。
7. 阶段计划的复选框状态已根据代码核查和完整测试结果同步。

不做的事：

- 不绕过 DRM，不实现 Widevine、FairPlay、PlayReady 解密。
- 不新增大型平台硬编码解析器。
- 不把下载页改成专业抓包面板；必要 UI 只增加轻量开关或数值输入。
- 不删除用户下载产物、断点续传 manifest、未完成切片或计划文档。

每个阶段完成后运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

每个阶段完成并验证后清理完全无用的临时文件，例如 `tests/.tmp/tmp*`、`tests/tmp*`、`tests/__pycache__`、`tools/video_crawler/**/__pycache__`。清理前必须确认目标绝对路径位于 `D:\Study\Projects\PythonProject\FireflyTools` 内，且不是断点续传 manifest、未完成切片、用户下载产物、源码、测试源码或计划文档。

---

## 1. 目标文件结构

计划新增或调整这些文件：

```text
tools/
├─ video_downloader.py                      # 保留 PyQt 主界面和队列入口，继续瘦身
└─ video_crawler/
   ├─ logging_utils.py                      # 新增：统一日志/报告脱敏入口
   ├─ session.py                            # 扩展：LocalStorage 和请求头继承
   ├─ sniffer.py                            # 扩展：捕获媒体请求 headers 与 LocalStorage
   ├─ reporting.py                          # 修改：诊断报告统一脱敏
   ├─ models.py                             # 扩展：BrowserSessionSnapshot.local_storage
   ├─ spider.py                             # 新增：承接 UniversalVideoSpider 核心入口
   └─ adapters/
      ├─ hls.py                             # 扩展：多音轨、字幕、直播滚动 playlist、结构化 FFmpeg 错误
      └─ dash.py                            # 扩展：DASH track 级断点续传
tests/
├─ test_video_crawler_redaction.py
├─ test_video_crawler_session.py
├─ test_video_crawler_hls_renditions.py
├─ test_video_crawler_hls_live.py
├─ test_video_crawler_dash_resume.py
├─ test_video_crawler_structured_errors.py
└─ test_video_downloader.py
```

---

## Phase A: 全链路敏感信息脱敏

**目标：** 所有诊断报告、运行日志、异常详情和队列弹窗在展示前统一脱敏，避免明文输出 Cookie、Authorization、Token、access_token、auth、signature、sig 等敏感信息。

**Files:**

- Create: `tools/video_crawler/logging_utils.py`
- Create: `tests/test_video_crawler_redaction.py`
- Modify: `tools/video_crawler/session.py`
- Modify: `tools/video_crawler/reporting.py`
- Modify: `tools/video_crawler/models.py`
- Modify: `tools/video_downloader.py`
- Modify: `tests/test_video_crawler_session.py`
- Modify: `tests/test_video_downloader.py`

### Task A1: 建立统一脱敏入口

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_redaction.py`:

```python
import unittest

from tools.video_crawler.logging_utils import redact_for_display


class RedactionTests(unittest.TestCase):
    def test_redacts_sensitive_query_parameters_case_insensitively(self):
        text = (
            "https://cdn.example.test/video.mp4?"
            "token=secret&Authorization=BearerSecret&sig=abc"
        )

        redacted = redact_for_display(text)

        self.assertIn("token=<redacted>", redacted)
        self.assertIn("Authorization=<redacted>", redacted)
        self.assertIn("sig=<redacted>", redacted)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("BearerSecret", redacted)
        self.assertNotIn("sig=abc", redacted)

    def test_redacts_cookie_and_authorization_headers(self):
        text = "Cookie: sid=abc Authorization: Bearer secret X-Token: xyz"

        redacted = redact_for_display(text)

        self.assertIn("Cookie: <redacted>", redacted)
        self.assertIn("Authorization: <redacted>", redacted)
        self.assertIn("X-Token: <redacted>", redacted)
        self.assertNotIn("sid=abc", redacted)
        self.assertNotIn("Bearer secret", redacted)
        self.assertNotIn("xyz", redacted)
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_redaction -v
```

Expected:

```text
ImportError: No module named 'tools.video_crawler.logging_utils'
```

- [x] **Step 3: 实现脱敏工具**

Create `tools/video_crawler/logging_utils.py`:

```python
import re


SENSITIVE_QUERY_KEYS = (
    "token",
    "access_token",
    "auth",
    "authorization",
    "signature",
    "sig",
    "key",
)

SENSITIVE_HEADER_NAMES = (
    "cookie",
    "authorization",
    "x-token",
    "x-auth-token",
)


def redact_for_display(text: object) -> str:
    value = "" if text is None else str(text)
    for header in SENSITIVE_HEADER_NAMES:
        value = re.sub(
            rf"(?i)\b{re.escape(header)}:\s*.*?(?=\s+[A-Za-z-]+:|\s+\w+=|$)",
            lambda match: match.group(0).split(":", 1)[0] + ": <redacted>",
            value,
        )
    for key in SENSITIVE_QUERY_KEYS:
        value = re.sub(
            rf"(?i)([?&;\s]{re.escape(key)}=)[^\s&#;]+",
            lambda match: match.group(1) + "<redacted>",
            value,
        )
    return value
```

- [x] **Step 4: 兼容旧导入**

Modify `tools/video_crawler/session.py`:

```python
from tools.video_crawler.logging_utils import redact_for_display


def redact_sensitive_text(text: str) -> str:
    return redact_for_display(text)
```

- [x] **Step 5: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_redaction tests.test_video_crawler_session -v
```

Expected:

```text
OK
```

### Task A2: 诊断报告和日志输出前脱敏

- [x] **Step 1: 写失败测试**

Append to `tests/test_video_crawler_redaction.py`:

```python
from tools.video_crawler.models import DiagnosticReport, MediaCandidate, MediaKind
from tools.video_crawler.reporting import format_diagnostic_report


class DiagnosticReportRedactionTests(unittest.TestCase):
    def test_format_diagnostic_report_redacts_candidate_urls(self):
        report = DiagnosticReport(
            source_url="https://page.example.test/watch?token=page-secret",
            candidates=[
                MediaCandidate(
                    url="https://cdn.example.test/video.mp4?token=media-secret",
                    kind=MediaKind.DIRECT_MP4,
                    source="test",
                    score=100,
                )
            ],
            warnings=["Authorization: Bearer hidden"],
            errors=["sig=hidden-signature"],
        )

        summary = format_diagnostic_report(report)

        self.assertIn("token=<redacted>", summary)
        self.assertIn("Authorization: <redacted>", summary)
        self.assertIn("sig=<redacted>", summary)
        self.assertNotIn("page-secret", summary)
        self.assertNotIn("media-secret", summary)
        self.assertNotIn("Bearer hidden", summary)
        self.assertNotIn("hidden-signature", summary)
```

Append to `tests/test_video_downloader.py`:

```python
    def test_append_log_redacts_sensitive_text(self):
        self.tool.append_log(
            "https://cdn.example.test/video.mp4?token=secret Authorization: Bearer hidden"
        )

        text = self.tool.log_text.toPlainText()

        self.assertIn("token=<redacted>", text)
        self.assertIn("Authorization: <redacted>", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("Bearer hidden", text)
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_crawler_redaction tests.test_video_downloader.VideoDownloaderToolTests.test_append_log_redacts_sensitive_text -v
```

Expected:

```text
FAIL
```

- [x] **Step 3: 修改报告格式化**

Modify `tools/video_crawler/reporting.py`:

```python
from tools.video_crawler.logging_utils import redact_for_display
from tools.video_crawler.models import DiagnosticReport


def format_diagnostic_report(report: DiagnosticReport) -> str:
    return redact_for_display(report.to_user_summary())
```

- [x] **Step 4: 修改 UI 日志入口**

Modify `tools/video_downloader.py` imports:

```python
from tools.video_crawler.logging_utils import redact_for_display
```

Modify `VideoDownloaderTool.append_log`:

```python
    def append_log(self, message):
        self.log_text.append(redact_for_display(message))
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
```

Modify `UniversalVideoSpider.log`:

```python
    def log(self, message):
        safe_message = redact_for_display(message)
        if self.log_callback:
            self.log_callback(safe_message)
        else:
            print(safe_message)
```

- [x] **Step 5: 运行测试确认通过**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_crawler_redaction tests.test_video_downloader.VideoDownloaderToolTests.test_append_log_redacts_sensitive_text -v
```

Expected:

```text
OK
```

---

## Phase B: 浏览器会话继承补全

**目标：** 在 Playwright 嗅探阶段捕获 LocalStorage 和媒体请求临时授权头，并安全传给下载阶段；展示时仍然脱敏。

**Files:**

- Modify: `tools/video_crawler/models.py`
- Modify: `tools/video_crawler/session.py`
- Modify: `tools/video_crawler/sniffer.py`
- Modify: `tests/test_video_crawler_session.py`
- Modify: `tests/test_video_crawler_diagnostics.py`
- Modify: `tests/test_video_downloader.py`

### Task B1: 扩展会话快照模型和 Header 提取

- [x] **Step 1: 写失败测试**

Append to `tests/test_video_crawler_session.py`:

```python
from tools.video_crawler.session import extract_download_request_headers


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
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_session.DownloadRequestHeaderTests -v
```

Expected:

```text
ImportError or TypeError
```

- [x] **Step 3: 扩展模型**

Modify `tools/video_crawler/models.py`:

```python
@dataclass(frozen=True)
class BrowserSessionSnapshot:
    user_agent: str = ""
    referer: str = ""
    origin: str = ""
    cookies: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    headers: dict[str, str] = field(default_factory=dict)
    local_storage: dict[str, str] = field(default_factory=dict)
```

- [x] **Step 4: 实现请求头提取**

Modify `tools/video_crawler/session.py`:

```python
DOWNLOAD_HEADER_ALLOWLIST = {
    "authorization": "Authorization",
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "range": "Range",
    "x-token": "X-Token",
    "x-auth-token": "X-Auth-Token",
}


def extract_download_request_headers(raw_headers: dict[str, str]) -> dict[str, str]:
    extracted = {}
    for name, value in raw_headers.items():
        canonical = DOWNLOAD_HEADER_ALLOWLIST.get(name.lower())
        if canonical and value:
            extracted[canonical] = value
    return extracted
```

- [x] **Step 5: 更新 Header 合并**

Modify `build_download_headers` in `tools/video_crawler/session.py`:

```python
    for name, value in snapshot.headers.items():
        canonical = DOWNLOAD_HEADER_ALLOWLIST.get(name.lower(), name)
        if canonical in DOWNLOAD_HEADER_ALLOWLIST.values() and value:
            headers[canonical] = value
```

Keep cookie domain filtering unchanged.

- [x] **Step 6: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_session -v
```

Expected:

```text
OK
```

### Task B2: Playwright 嗅探时捕获媒体请求 headers 和 LocalStorage

- [x] **Step 1: 写纯函数测试**

Append to `tests/test_video_crawler_diagnostics.py`:

```python
from tools.video_crawler.sniffer import merge_media_request_headers


class PageSnifferSessionCaptureTests(unittest.TestCase):
    def test_merge_media_request_headers_keeps_latest_allowlisted_values(self):
        current = {"Accept": "video/*"}
        incoming = {
            "authorization": "Bearer fresh",
            "x-token": "edge",
            "cookie": "sid=hidden",
        }

        result = merge_media_request_headers(current, incoming)

        self.assertEqual(result["Accept"], "video/*")
        self.assertEqual(result["Authorization"], "Bearer fresh")
        self.assertEqual(result["X-Token"], "edge")
        self.assertNotIn("Cookie", result)
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_diagnostics.PageSnifferSessionCaptureTests -v
```

Expected:

```text
ImportError
```

- [x] **Step 3: 实现 headers 合并函数**

Modify `tools/video_crawler/sniffer.py`:

```python
from tools.video_crawler.session import extract_download_request_headers


def merge_media_request_headers(
    current: dict[str, str],
    incoming: dict[str, str],
) -> dict[str, str]:
    merged = dict(current)
    merged.update(extract_download_request_headers(incoming))
    return merged
```

- [x] **Step 4: 在 PageSniffer.sniff 中捕获媒体请求 headers**

Inside `PageSniffer.sniff`, before `handle_response`:

```python
        captured_headers = dict(self.headers)
```

Inside `handle_response`, after confirming `candidate is not None`:

```python
                    nonlocal captured_headers
                    captured_headers = merge_media_request_headers(
                        captured_headers,
                        response.request.headers,
                    )
```

In the final `BrowserSessionSnapshot`, use:

```python
                    headers=captured_headers,
```

- [x] **Step 5: 捕获 LocalStorage**

In the `finally` block before building `BrowserSessionSnapshot`:

```python
                try:
                    local_storage = page.evaluate(
                        "() => Object.fromEntries(Object.entries(window.localStorage))"
                    )
                except Exception:
                    local_storage = {}
```

Then pass:

```python
                    local_storage=local_storage,
```

- [x] **Step 6: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_diagnostics tests.test_video_crawler_session tests.test_video_downloader.UniversalVideoSpiderTests.test_webpage_run_inherits_sniffed_session_headers -v
```

Expected:

```text
OK
```

---

## Phase C: HLS 多音轨、字幕和直播滚动 playlist

**目标：** 支持主播放列表中的默认音轨和字幕；对直播滚动 playlist 提供轻量录制模式，避免无限等待。

**Files:**

- Modify: `tools/video_crawler/adapters/hls.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_hls_renditions.py`
- Create: `tests/test_video_crawler_hls_live.py`
- Modify: `tests/test_video_downloader.py`

### Task C1: 解析 HLS rendition 计划

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_hls_renditions.py`:

```python
import unittest
from types import SimpleNamespace

from tools.video_crawler.adapters.hls import build_hls_rendition_plan


class HlsRenditionPlanTests(unittest.TestCase):
    def test_selects_default_audio_and_subtitle_renditions(self):
        playlist = SimpleNamespace(
            media=[
                SimpleNamespace(
                    type="AUDIO",
                    default="YES",
                    uri="audio/main.m3u8",
                    absolute_uri="https://cdn.example.test/audio/main.m3u8",
                    name="Main",
                ),
                SimpleNamespace(
                    type="SUBTITLES",
                    default="YES",
                    uri="subs/zh.m3u8",
                    absolute_uri="https://cdn.example.test/subs/zh.m3u8",
                    name="Chinese",
                ),
            ]
        )

        plan = build_hls_rendition_plan(playlist)

        self.assertEqual(plan.audio_url, "https://cdn.example.test/audio/main.m3u8")
        self.assertEqual(plan.subtitle_url, "https://cdn.example.test/subs/zh.m3u8")
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_hls_renditions -v
```

Expected:

```text
ImportError
```

- [x] **Step 3: 实现 rendition 数据结构和选择函数**

Modify `tools/video_crawler/adapters/hls.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class HlsRenditionPlan:
    audio_url: str | None = None
    subtitle_url: str | None = None


def _is_default_rendition(media) -> bool:
    return str(getattr(media, "default", "")).upper() == "YES"


def build_hls_rendition_plan(playlist) -> HlsRenditionPlan:
    audio_url = None
    subtitle_url = None
    for media in getattr(playlist, "media", []) or []:
        media_type = str(getattr(media, "type", "")).upper()
        uri = getattr(media, "absolute_uri", "") or getattr(media, "uri", "")
        if not uri:
            continue
        if media_type == "AUDIO" and audio_url is None and _is_default_rendition(media):
            audio_url = uri
        if media_type == "SUBTITLES" and subtitle_url is None and _is_default_rendition(media):
            subtitle_url = uri
    return HlsRenditionPlan(audio_url=audio_url, subtitle_url=subtitle_url)
```

- [x] **Step 4: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_hls_renditions -v
```

Expected:

```text
OK
```

### Task C2: 下载默认音轨并 mux 到输出 MP4

- [x] **Step 1: 写失败测试**

Append to `tests/test_video_crawler_hls_renditions.py`:

```python
import os
import tempfile
from unittest.mock import patch

from tools.video_crawler.adapters.hls import HlsAdapter


class HlsAudioMuxTests(unittest.TestCase):
    def test_muxes_default_audio_track_when_present(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "downloads")
            os.makedirs(output_dir)
            adapter = HlsAdapter(
                output_dir=output_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
            )

            calls = []

            def fake_download_media_playlist(url, output_filename, suffix):
                path = os.path.join(temp_dir, f"{output_filename}.{suffix}.mp4")
                with open(path, "wb") as output:
                    output.write(suffix.encode("utf-8"))
                calls.append((url, suffix))
                return path

            with patch.object(
                adapter,
                "download_media_playlist",
                side_effect=fake_download_media_playlist,
            ), patch.object(adapter, "mux_renditions") as mux:
                mux.return_value = os.path.join(output_dir, "movie.mp4")

                result = adapter.download_master_with_renditions(
                    video_url="https://cdn.example.test/video.m3u8",
                    audio_url="https://cdn.example.test/audio.m3u8",
                    subtitle_url=None,
                    output_filename="movie",
                )

        self.assertEqual(result, os.path.join(output_dir, "movie.mp4"))
        self.assertEqual(calls[0], ("https://cdn.example.test/video.m3u8", "video"))
        self.assertEqual(calls[1], ("https://cdn.example.test/audio.m3u8", "audio"))
        mux.assert_called_once()
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_hls_renditions.HlsAudioMuxTests -v
```

Expected:

```text
AttributeError
```

- [x] **Step 3: 提取媒体 playlist 下载方法**

Modify `HlsAdapter.download_url` so the existing single-playlist logic moves into:

```python
    async def download_media_playlist(
        self,
        m3u8_url: str,
        output_filename: str,
        suffix: str,
    ) -> str:
        media_output_name = f"{output_filename}.{suffix}"
        return await self._download_playlist_to_mp4(m3u8_url, media_output_name)
```

Rename the current body of `download_url` to:

```python
    async def _download_playlist_to_mp4(self, m3u8_url: str, output_filename: str):
        ...
```

Keep the current segment download, resume and cleanup behavior inside `_download_playlist_to_mp4`.

- [x] **Step 4: 实现 mux 方法**

Add to `HlsAdapter`:

```python
    def mux_renditions(
        self,
        video_path: str,
        audio_path: str | None,
        subtitle_path: str | None,
        output_path: str,
    ) -> str:
        command = ["ffmpeg", "-y", "-i", video_path]
        if audio_path:
            command.extend(["-i", audio_path])
        if subtitle_path:
            command.extend(["-i", subtitle_path])
        command.extend(["-c:v", "copy"])
        if audio_path:
            command.extend(["-c:a", "copy"])
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
                f"FFmpeg HLS 多轨 mux 失败: {error}",
                retryable=False,
            ) from exc
        return self._verify_output(output_path)
```

- [x] **Step 5: 实现多轨下载入口**

Add to `HlsAdapter`:

```python
    async def download_master_with_renditions(
        self,
        *,
        video_url: str,
        audio_url: str | None,
        subtitle_url: str | None,
        output_filename: str,
    ) -> str:
        video_path = await self.download_media_playlist(video_url, output_filename, "video")
        audio_path = None
        subtitle_path = None
        if audio_url:
            audio_path = await self.download_media_playlist(audio_url, output_filename, "audio")
        if subtitle_url:
            subtitle_path = await self.download_subtitle_playlist(
                subtitle_url,
                output_filename,
            )
        final_path = os.path.join(self.output_dir, f"{output_filename}.mp4")
        if audio_path or subtitle_path:
            return self.mux_renditions(video_path, audio_path, subtitle_path, final_path)
        if video_path != final_path:
            os.replace(video_path, final_path)
        return self._verify_output(final_path)
```

- [x] **Step 6: 在 master playlist 处理中调用多轨入口**

In `download_url`, when `playlist.is_variant` is true:

```python
            rendition_plan = build_hls_rendition_plan(playlist)
            playlists = list(playlist.playlists)
            playlists.sort(
                key=lambda item: item.stream_info.bandwidth
                if item.stream_info.bandwidth
                else 0,
                reverse=True,
            )
            video_url = playlists[0].absolute_uri
            if rendition_plan.audio_url or rendition_plan.subtitle_url:
                return await self.download_master_with_renditions(
                    video_url=video_url,
                    audio_url=rendition_plan.audio_url,
                    subtitle_url=rendition_plan.subtitle_url,
                    output_filename=output_filename,
                )
            m3u8_url = video_url
            playlist = m3u8.load(m3u8_url, headers=self.headers)
```

- [x] **Step 7: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_hls_renditions tests.test_video_downloader -v
```

Expected:

```text
OK
```

### Task C3: 支持默认字幕下载

- [x] **Step 1: 写失败测试**

Append to `tests/test_video_crawler_hls_renditions.py`:

```python
class HlsSubtitleTests(unittest.TestCase):
    def test_download_subtitle_playlist_concatenates_webvtt_segments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = HlsAdapter(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
            )
            playlist = SimpleNamespace(
                is_variant=False,
                segments=[
                    SimpleNamespace(absolute_uri="https://cdn/subs/1.vtt"),
                    SimpleNamespace(absolute_uri="https://cdn/subs/2.vtt"),
                ],
            )

            def fake_load(url, headers=None):
                return playlist

            def fake_get(url, headers=None, timeout=None):
                return SimpleNamespace(
                    content=f"WEBVTT\n\n{url}".encode("utf-8"),
                    raise_for_status=lambda: None,
                )

            with patch("tools.video_crawler.adapters.hls.m3u8.load", side_effect=fake_load), patch(
                "tools.video_crawler.adapters.hls.requests.get",
                side_effect=fake_get,
            ):
                path = adapter.download_subtitle_playlist(
                    "https://cdn/subs/index.m3u8",
                    "movie",
                )

        with open(path, "r", encoding="utf-8") as subtitle_file:
            content = subtitle_file.read()
        self.assertIn("https://cdn/subs/1.vtt", content)
        self.assertIn("https://cdn/subs/2.vtt", content)
```

- [x] **Step 2: 实现字幕下载**

Add to `HlsAdapter`:

```python
    def download_subtitle_playlist(self, subtitle_url: str, output_filename: str) -> str:
        playlist = m3u8.load(subtitle_url, headers=self.headers)
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
```

- [x] **Step 3: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_hls_renditions.HlsSubtitleTests -v
```

Expected:

```text
OK
```

### Task C4: 直播滚动 playlist 轻量录制模式

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_hls_live.py`:

```python
import unittest
from types import SimpleNamespace

from tools.video_crawler.adapters.hls import is_live_playlist


class HlsLiveTests(unittest.TestCase):
    def test_detects_playlist_without_endlist_as_live(self):
        playlist = SimpleNamespace(is_endlist=False, playlist_type=None)

        self.assertTrue(is_live_playlist(playlist))

    def test_detects_vod_playlist_as_not_live(self):
        playlist = SimpleNamespace(is_endlist=True, playlist_type="VOD")

        self.assertFalse(is_live_playlist(playlist))
```

- [x] **Step 2: 实现直播判断**

Add to `tools/video_crawler/adapters/hls.py`:

```python
def is_live_playlist(playlist) -> bool:
    if str(getattr(playlist, "playlist_type", "")).upper() == "VOD":
        return False
    return not bool(getattr(playlist, "is_endlist", True))
```

- [x] **Step 3: 增加 UI 录制时长控件**

Modify `VideoDownloaderTool.__init__` row4 in `tools/video_downloader.py`:

```python
self.live_seconds_spin = QSpinBox()
self.live_seconds_spin.setRange(30, 7200)
self.live_seconds_spin.setValue(300)
self.live_seconds_spin.setSuffix(" 秒直播录制")
self.live_seconds_spin.setFixedWidth(150)
row4.addWidget(self.live_seconds_spin)
```

This spin box must use the existing themed `QSpinBox` style and remain in the same compact control row.

- [x] **Step 4: 任务字段和 spider 参数**

Modify `add_to_queue`:

```python
"live_record_seconds": self.live_seconds_spin.value(),
```

Modify `UniversalVideoSpider.__init__`:

```python
                 resume_enabled=True, live_record_seconds=300):
...
        self.live_record_seconds = int(live_record_seconds)
```

Pass into `HlsAdapter` in `_build_orchestrator` and `_hls_adapter`:

```python
live_record_seconds=self.live_record_seconds,
```

Modify `HlsAdapter.__init__`:

```python
        live_record_seconds: int = 300,
...
        self.live_record_seconds = int(live_record_seconds)
```

- [x] **Step 5: 直播下载循环**

In `_download_playlist_to_mp4`, when `is_live_playlist(playlist)` is true:

```python
        if is_live_playlist(playlist):
            return await self.download_live_playlist(m3u8_url, output_filename)
```

Add:

```python
    async def download_live_playlist(self, m3u8_url: str, output_filename: str) -> str:
        deadline = asyncio.get_event_loop().time() + self.live_record_seconds
        seen_urls = set()
        captured_items = []
        while asyncio.get_event_loop().time() < deadline:
            playlist = m3u8.load(m3u8_url, headers=self.headers)
            media_sequence = getattr(playlist, "media_sequence", 0) or 0
            for index, segment in enumerate(playlist.segments):
                if segment.absolute_uri in seen_urls:
                    continue
                seen_urls.add(segment.absolute_uri)
                captured_items.append((media_sequence + index, segment))
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
```

Implement `download_collected_live_segments` by reusing the existing segment item construction, `download_segments`, and `merge_with_ffmpeg`.

- [x] **Step 6: 运行测试确认通过**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_crawler_hls_live tests.test_video_crawler_hls_renditions tests.test_video_downloader -v
```

Expected:

```text
OK
```

---

## Phase D: DASH 断点续传

**目标：** DASH 视频轨和音频轨分段下载复用 manifest，失败后保留已完成分段，重试只补缺失片段。

**Files:**

- Modify: `tools/video_crawler/adapters/dash.py`
- Modify: `tools/video_crawler/resume.py`
- Create: `tests/test_video_crawler_dash_resume.py`

### Task D1: Track 级 manifest 路径和跳过逻辑

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_dash_resume.py`:

```python
import os
import tempfile
import unittest

from tools.video_crawler.adapters.dash import DashAdapter, DashTrackPlan
from tools.video_crawler.resume import SegmentManifest


class DashResumeTests(unittest.TestCase):
    def test_write_track_skips_completed_segments_from_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = os.path.join(temp_dir, "dash")
            os.makedirs(work_dir)
            manifest_path = os.path.join(work_dir, "video.firefly-segments.json")
            first_path = os.path.join(work_dir, "video-00000.m4s")
            with open(first_path, "wb") as output:
                output.write(b"init")
            manifest = SegmentManifest(manifest_path)
            manifest.mark_downloaded(
                "video-00000.m4s",
                url="https://cdn/video/init.m4s",
                size=4,
            )
            manifest.save()

            fetched = []

            def fake_fetch(url):
                fetched.append(url)
                return b"new"

            adapter = DashAdapter(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
                fetch_url=fake_fetch,
            )
            track = DashTrackPlan(
                kind="video",
                representation_id="v1",
                bandwidth=1000,
                urls=[
                    "https://cdn/video/init.m4s",
                    "https://cdn/video/1.m4s",
                ],
            )

            output_path = adapter._write_track(track, work_dir)

        self.assertEqual(fetched, ["https://cdn/video/1.m4s"])
        self.assertTrue(os.path.getsize(output_path) > 0)
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_dash_resume -v
```

Expected:

```text
FAIL
```

- [x] **Step 3: 修改 `_write_track` 为分段落盘**

Modify `DashAdapter._write_track`:

```python
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
                and manifest.is_downloaded(filename, expected_size=os.path.getsize(segment_path))
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
```

- [x] **Step 4: 保留失败现场，成功后清理**

Modify `DashAdapter.download`:

```python
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
```

- [x] **Step 5: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_dash_resume tests.test_video_crawler_dash -v
```

Expected:

```text
OK
```

---

## Phase E: 结构化错误收口

**目标：** HLS、DASH、yt-dlp 和队列层的可预期失败都携带准确 `VideoErrorCode`，尤其是 HLS FFmpeg 失败映射到 `FFMPEG_FAILED`。

**Files:**

- Modify: `tools/video_crawler/adapters/hls.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_structured_errors.py`
- Modify: `tests/test_video_downloader.py`

### Task E1: HLS FFmpeg 失败使用 FFMPEG_FAILED

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_structured_errors.py`:

```python
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.video_crawler.adapters.hls import HlsAdapter
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode


class HlsStructuredErrorTests(unittest.TestCase):
    def test_hls_ffmpeg_failure_uses_structured_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            segment_path = os.path.join(temp_dir, "00000.ts")
            output_path = os.path.join(temp_dir, "output.mp4")
            with open(segment_path, "wb") as segment:
                segment.write(b"segment")
            adapter = HlsAdapter(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
            )
            ffmpeg_error = subprocess.CalledProcessError(
                1,
                ["ffmpeg"],
                stderr=b"mux failed",
            )

            with patch(
                "tools.video_crawler.adapters.hls.subprocess.run",
                side_effect=ffmpeg_error,
            ):
                with self.assertRaises(VideoDownloadError) as raised:
                    adapter.merge_with_ffmpeg([segment_path], output_path)

        self.assertEqual(raised.exception.code, VideoErrorCode.FFMPEG_FAILED)
        self.assertFalse(raised.exception.retryable)
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_structured_errors -v
```

Expected:

```text
FAIL
```

- [x] **Step 3: 修改 HLS FFmpeg 异常**

Modify both FFmpeg `except subprocess.CalledProcessError` blocks in `tools/video_crawler/adapters/hls.py`:

```python
            raise VideoDownloadError(
                VideoErrorCode.FFMPEG_FAILED,
                f"FFmpeg 合并失败: {error}",
                retryable=False,
            ) from exc
```

For fMP4 repair:

```python
            raise VideoDownloadError(
                VideoErrorCode.FFMPEG_FAILED,
                f"FFmpeg 修复容器失败: {error}",
                retryable=False,
            ) from exc
```

- [x] **Step 4: 更新旧测试断言**

Modify `tests/test_video_downloader.py` cleanup failure test:

```python
                with self.assertRaisesRegex(VideoDownloadError, "FFmpeg 合并失败") as raised:
                    spider._merge_with_ffmpeg(
                        [segment_path],
                        os.path.join(temp_dir, "output.mp4"),
                    )
                self.assertEqual(raised.exception.code, VideoErrorCode.FFMPEG_FAILED)
```

- [x] **Step 5: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_structured_errors tests.test_video_downloader.UniversalVideoSpiderTests.test_cleanup_error_does_not_hide_ffmpeg_failure -v
```

Expected:

```text
OK
```

---

## Phase F: 适配器迁移收尾和主文件瘦身

**目标：** 将 `UniversalVideoSpider` 下载核心迁移出 `tools/video_downloader.py`，让主文件主要保留 PyQt UI、队列和信号逻辑，并把文件长度降到约 450 行以下。

**Files:**

- Create: `tools/video_crawler/spider.py`
- Modify: `tools/video_downloader.py`
- Modify: `tests/test_video_downloader.py`
- Modify: `tests/test_video_crawler_adapters.py`

### Task F1: 迁移 UniversalVideoSpider 到 spider.py

- [x] **Step 1: 写导入兼容测试**

Append to `tests/test_video_crawler_adapters.py`:

```python
class SpiderModuleTests(unittest.TestCase):
    def test_universal_video_spider_is_importable_from_core_package(self):
        from tools.video_crawler.spider import UniversalVideoSpider as CoreSpider
        from tools.video_downloader import UniversalVideoSpider as UiCompatSpider

        self.assertIs(CoreSpider, UiCompatSpider)
```

- [x] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_adapters.SpiderModuleTests -v
```

Expected:

```text
ImportError
```

- [x] **Step 3: 新建 spider.py**

Create `tools/video_crawler/spider.py` and move the current `UniversalVideoSpider` class from `tools/video_downloader.py` into it. Preserve these imports in `spider.py`:

```python
import os
import requests
import m3u8
import subprocess

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
```

- [x] **Step 4: 保留 UI 兼容导入**

In `tools/video_downloader.py`, remove the class body and add:

```python
from tools.video_crawler.spider import UniversalVideoSpider
```

Keep `VideoDownloaderTool` unchanged except imports that are no longer needed.

- [x] **Step 5: 删除主文件中不再使用的导入**

From `tools/video_downloader.py`, remove imports only used by the moved spider class:

```python
import requests
import m3u8
import subprocess
from playwright.sync_api import sync_playwright
from tools.video_crawler.adapters.base import VideoDownloadOrchestrator
from tools.video_crawler.adapters.dash import DashAdapter
from tools.video_crawler.adapters.direct_mp4 import DirectMp4Adapter
from tools.video_crawler.adapters.hls import HlsAdapter
from tools.video_crawler.models import MediaCandidate, MediaKind
from tools.video_crawler.session import build_download_headers
```

Keep `VideoDownloadError`, `VideoErrorCode`, `YtDlpAdapter`, `PageSniffer`, `redact_for_display`, PyQt imports, `os`, `threading`, and `queue`.

- [x] **Step 6: 运行测试和行数检查**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_crawler_adapters tests.test_video_downloader -v
(Get-Content tools\video_downloader.py).Count
```

Expected:

```text
OK
```

The line count should be below `450`.

---

## Phase G: 计划文档状态校准

**目标：** 让 `plan/video-crawler-phased-implementation-plan.md` 的复选框状态反映已通过代码和测试验证的事实，同时保留本补齐计划中的剩余阶段状态。

**Files:**

- Modify: `plan/video-crawler-phased-implementation-plan.md`
- Modify: `plan/video-crawler-gap-closure-implementation-plan.md`

### Task G1: 回填 Phase 1-9 已完成状态

- [x] **Step 1: 核对测试证据**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected:

```text
Ran 78 tests
OK
```

If earlier phases add more tests before this task执行, use the actual test count and require `OK`.

- [x] **Step 2: 更新原阶段计划复选框**

In `plan/video-crawler-phased-implementation-plan.md`, mark Phase 1-9 implemented checklist items as `[x]` only when a matching source file and test exists. Keep Phase 0 Git status items unchanged if Git HEAD is still abnormal or not rechecked in that turn.

Use this mapping:

```text
Phase 1 -> tests/test_video_crawler_models.py, tools/video_crawler/errors.py, tools/video_crawler/models.py
Phase 2 -> tests/test_video_crawler_diagnostics.py, tools/video_crawler/diagnostics.py, tools/video_crawler/sniffer.py, UI diagnose button
Phase 3 -> tests/test_video_crawler_session.py, tools/video_crawler/session.py, session header merge tests
Phase 4 -> tests/test_video_crawler_hls.py, HLS IV/BYTERANGE/MAP/DISCONTINUITY helpers
Phase 5 -> tests/test_video_downloader.py structured queue result tests
Phase 6 -> tests/test_video_crawler_resume.py and HLS resume tests
Phase 7 -> tests/test_video_crawler_adapters.py and adapter files
Phase 8 -> tests/test_video_crawler_dash.py and tools/video_crawler/adapters/dash.py
Phase 9 -> tests/test_video_crawler_ytdlp.py and tools/video_crawler/adapters/ytdlp.py
```

- [x] **Step 3: 更新本补齐计划状态**

When Phases A-F are implemented and verified, mark their checkboxes `[x]` in this file. Do not mark a phase complete unless its targeted tests and full suite pass.

- [x] **Step 4: 搜索遗留未完成项**

Run:

```powershell
Select-String -Path plan\*.md -Pattern '\[ \]|未完全|缺口|敏感信息|LocalStorage|多音轨|字幕|直播|DASH 断点|450'
```

Expected:

```text
Only future-scope or intentionally open items remain.
```

---

## 最终验收矩阵

| 验收项 | 完成信号 |
|---|---|
| 全链路敏感信息脱敏 | 诊断报告、日志、队列详情不出现原始 Cookie、Authorization、Token、sig 值 |
| 浏览器会话继承补全 | Sniffer 捕获媒体请求 allowlist headers 和 LocalStorage，下载请求可继承 |
| HLS 多音轨 | 默认音轨可下载并 mux 到 MP4 |
| HLS 字幕 | 默认字幕可下载并以 mov_text mux 到 MP4 |
| HLS 直播滚动 playlist | UI 可配置轻量录制时长，录制窗口内新增切片被收集下载 |
| DASH 断点续传 | 失败后保留 track manifest，重试跳过已完成 video/audio 分段 |
| 结构化错误收口 | HLS/DASH FFmpeg 失败均返回 `FFMPEG_FAILED` |
| 适配器迁移收尾 | `tools/video_downloader.py` 少于约 450 行，UI 行为测试仍通过 |
| 文档状态校准 | `plan` 中已完成复选框和测试证据一致 |

最终命令：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
$checks = [ordered]@{}
$checks['tests_tmp_children'] = @(Get-ChildItem -LiteralPath 'tests\.tmp' -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'tmp*' }).Count
$checks['tests_root_tmp_or_pycache'] = @(Get-ChildItem -LiteralPath 'tests' -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'tmp*' -or $_.Name -eq '__pycache__' }).Count
$checks['video_crawler_pycache_dirs'] = @(Get-ChildItem -LiteralPath 'tools\video_crawler' -Recurse -Force -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue).Count
$checks.GetEnumerator() | ForEach-Object { "{0}: {1}" -f $_.Key, $_.Value }
```

Expected:

```text
OK
tests_tmp_children: 0
tests_root_tmp_or_pycache: 0
video_crawler_pycache_dirs: 0
```
