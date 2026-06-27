# Video Crawler Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分阶段补齐视频爬虫尚未实现的诊断模式、浏览器会话继承、HLS 边界、结构化错误、断点续传、适配器架构、DASH/MPD 和 yt-dlp 后备能力。

**Architecture:** 保持 `tools/video_downloader.py` 作为现有 UI 兼容入口，逐步把下载核心拆到 `tools/video_crawler/` 包中。先建立结构化模型、错误码和诊断报告，再把网页嗅探、HLS、DASH、外部引擎做成可测试的适配器，最终由统一编排器选择执行路径。

**Tech Stack:** Python 3.x, PyQt6, requests, aiohttp, m3u8, cryptography, Playwright, FFmpeg, unittest.

---

## 0. 计划范围

本计划来自 `plan/video-crawler-roadmap.md` 中列出的未实现功能。目标是给后续开发提供可直接执行的阶段方案，不在本文件中实施源码修改。

不做的事：

- 不绕过 Widevine、FairPlay、PlayReady 或任何 DRM。
- 不优先为 YouTube、B 站、抖音、快手等大型平台写硬编码解析器。
- 不把下载页改成复杂专业面板；UI 只增加诊断入口、报告展示、可选后备引擎和恢复入口。
- 所有新增按钮、弹窗、复选框、诊断报告展示和恢复入口，都必须和主界面现有 PyQt6 + 动态 QSS 风格保持一致，沿用无边框窗口、壁纸背景、磨砂面板、明暗主题和 `QMessageBox` 可读性适配。
- 不在 Git HEAD 异常时执行提交、重置、清理对象库等 Git 写操作。

每个阶段结束时都应运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

如果 `git status` 仍报 `fatal: bad object HEAD`，只记录变更文件，不执行提交。

每个阶段完成并完成验证后，都要清理已经确认完全无用的临时文件，例如测试残留目录、下载切片临时目录、FFmpeg concat 列表、诊断缓存和空的运行时目录。清理前必须确认目标路径位于项目工作区内，且不是待复用的断点续传 manifest、未完成切片、用户下载产物、源码、测试源码或计划文档。

## 1. 当前基线

现有核心文件：

- `tools/video_downloader.py`：`UniversalVideoSpider`、`VideoDownloaderTool`、队列处理、Playwright 嗅探、MP4/HLS 下载、FFmpeg 合并。
- `tools/theme_utils.py`：动态主题和 `QMessageBox` 可读性样式。
- `tests/test_video_downloader.py`：当前视频下载器、队列、失败容忍、批次结果测试。
- `tests/test_theme_utils.py`：主题样式测试。
- `plan/video-crawler-roadmap.md`：未实现功能清单和建议顺序。

当前 `UniversalVideoSpider` 承担了过多职责：URL 判断、网页嗅探、候选流选择、下载、解密、合并、临时文件清理和错误传播。后续拆分要保持外部兼容，避免一次性重写导致 UI 和测试同时失稳。

## 2. 目标文件结构

第一阶段开始后新增一个小包，逐步迁移核心逻辑：

```text
tools/
├─ video_downloader.py              # 保留 UI 和兼容外观，逐步转发到新核心
└─ video_crawler/
   ├─ __init__.py
   ├─ models.py                     # 候选流、诊断报告、会话快照、下载结果
   ├─ errors.py                     # VideoErrorCode、VideoDownloadError
   ├─ session.py                    # 浏览器会话快照、Header 合并、敏感信息脱敏
   ├─ diagnostics.py                # 诊断服务和诊断结果汇总
   ├─ sniffer.py                    # Playwright 网页嗅探，不直接下载
   ├─ reporting.py                  # 队列结果、诊断结果、用户可读摘要
   ├─ resume.py                     # 切片 manifest、断点续传校验
   └─ adapters/
      ├─ __init__.py
      ├─ base.py                    # Adapter 协议和选择接口
      ├─ direct_mp4.py              # 直链 MP4
      ├─ hls.py                     # M3U8/HLS
      ├─ dash.py                    # MPD/DASH
      └─ ytdlp.py                   # 可选 yt-dlp 后备
```

测试文件按功能拆分：

```text
tests/
├─ test_video_downloader.py         # 保留 UI 队列和兼容行为测试
├─ test_video_crawler_models.py
├─ test_video_crawler_diagnostics.py
├─ test_video_crawler_session.py
├─ test_video_crawler_hls.py
├─ test_video_crawler_resume.py
├─ test_video_crawler_adapters.py
├─ test_video_crawler_dash.py
└─ test_video_crawler_ytdlp.py
```

## 3. 阶段总览

| 阶段 | 名称 | 主要收益 | 是否影响 UI |
|---|---|---|---|
| 0 | 基线和安全门槛 | 确认当前测试、Git、依赖状态 | 否 |
| 1 | 结构化模型和错误码 | 让失败原因可分类、可展示、可测试 | 轻微 |
| 2 | 诊断模式 | 用户先知道能不能下、为什么失败 | 是 |
| 3 | 浏览器会话继承 | 降低“浏览器能播但下载 403”的概率 | 否 |
| 4 | HLS 边界补强 | 多 Key、IV、BYTERANGE、DISCONTINUITY 更稳 | 否 |
| 5 | 结构化报告和重试策略 | 队列弹窗更清楚，重试更有依据 | 是 |
| 6 | 断点续传和切片级恢复 | 避免重试整个任务浪费时间 | 是 |
| 7 | 适配器架构 | 降低核心文件复杂度，承接 DASH/站点适配 | 否 |
| 8 | DASH/MPD 支持 | 支持无 DRM 的静态 DASH | 轻微 |
| 9 | yt-dlp 可选后备 | 覆盖更多公开视频平台 | 是 |

---

## Phase 0: 基线和安全门槛

**目标：** 在任何源码改动前确认当前状态，避免在 Git 损坏或测试基线不明的情况下继续扩大变更。

**Files:**

- Read: `项目介绍.md`
- Read: `plan/video-crawler-roadmap.md`
- Read: `tools/video_downloader.py`
- Read: `tests/test_video_downloader.py`
- Modify: none

- [ ] **Step 1: 只读检查 Git HEAD**

Run:

```powershell
Get-Content .git\HEAD
Get-Content .git\refs\heads\main
git status
git log --oneline -5
```

Expected if repository is still damaged:

```text
fatal: bad object HEAD
```

Action:

- 若 Git 仍损坏，继续开发但不提交。
- 若 Git 正常，再在每个阶段末按计划提交。

- [ ] **Step 2: 运行基线测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected:

```text
Ran 17 tests
OK
```

如果测试数量因后续已存在变更不同，以实际输出为准；必须记录失败项后再进入 Phase 1。

---

## Phase 1: 结构化模型和错误码

**目标：** 先把“失败原因”从纯文本异常升级为结构化错误码，同时保持 `VideoDownloadError` 兼容现有测试。

**Files:**

- Create: `tools/video_crawler/__init__.py`
- Create: `tools/video_crawler/errors.py`
- Create: `tools/video_crawler/models.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_models.py`
- Modify: `tests/test_video_downloader.py`

### Task 1.1: 新增错误码和兼容异常

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_models.py`:

```python
import unittest

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode


class VideoCrawlerErrorTests(unittest.TestCase):
    def test_error_keeps_code_message_retryable_and_details(self):
        error = VideoDownloadError(
            VideoErrorCode.HTTP_FORBIDDEN,
            "服务器拒绝访问",
            details={"status": 403},
            retryable=False,
        )

        self.assertEqual(error.code, VideoErrorCode.HTTP_FORBIDDEN)
        self.assertEqual(str(error), "服务器拒绝访问")
        self.assertEqual(error.details["status"], 403)
        self.assertFalse(error.retryable)

    def test_error_accepts_legacy_string_message(self):
        error = VideoDownloadError("嗅探失败，未能找到视频流")

        self.assertEqual(error.code, VideoErrorCode.UNKNOWN)
        self.assertEqual(str(error), "嗅探失败，未能找到视频流")


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: 确认测试失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_models -v
```

Expected:

```text
ModuleNotFoundError: No module named 'tools.video_crawler'
```

- [x] **Step 3: 实现错误码**

Create `tools/video_crawler/errors.py`:

```python
from enum import Enum


class VideoErrorCode(str, Enum):
    UNKNOWN = "UNKNOWN"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    HTTP_FORBIDDEN = "HTTP_FORBIDDEN"
    HTTP_NOT_FOUND = "HTTP_NOT_FOUND"
    NO_MEDIA_FOUND = "NO_MEDIA_FOUND"
    UNSUPPORTED_DASH = "UNSUPPORTED_DASH"
    UNSUPPORTED_DRM = "UNSUPPORTED_DRM"
    M3U8_PARSE_FAILED = "M3U8_PARSE_FAILED"
    SEGMENT_FAILURE_RATE_EXCEEDED = "SEGMENT_FAILURE_RATE_EXCEEDED"
    FFMPEG_FAILED = "FFMPEG_FAILED"
    EMPTY_OUTPUT = "EMPTY_OUTPUT"


class VideoDownloadError(RuntimeError):
    """视频任务失败，带结构化错误码和用户可读消息。"""

    def __init__(self, code_or_message, message=None, *, details=None, retryable=False):
        if isinstance(code_or_message, VideoErrorCode):
            code = code_or_message
            final_message = message or code.value
        else:
            code = VideoErrorCode.UNKNOWN
            final_message = str(code_or_message)

        super().__init__(final_message)
        self.code = code
        self.details = details or {}
        self.retryable = retryable
```

Create `tools/video_crawler/__init__.py`:

```python
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode

__all__ = ["VideoDownloadError", "VideoErrorCode"]
```

- [x] **Step 4: 修改兼容导入**

Modify `tools/video_downloader.py`:

```python
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
```

Remove the old inline class:

```python
class VideoDownloadError(RuntimeError):
    """视频任务未能生成完整输出时抛出。"""
```

Update `_verify_output`:

```python
def _verify_output(self, output_path):
    if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
        raise VideoDownloadError(
            VideoErrorCode.EMPTY_OUTPUT,
            f"输出文件不存在或为空: {output_path}",
            details={"output_path": output_path},
            retryable=False,
        )
    return output_path
```

Update `_validate_segment_failures`:

```python
if total_count <= 0 or failed_count * 100 > total_count * 3:
    raise VideoDownloadError(
        VideoErrorCode.SEGMENT_FAILURE_RATE_EXCEEDED,
        f"有 {failed_count} 个切片下载失败（总计 {total_count} 个），超过允许的 3%",
        details={"failed_count": failed_count, "total_count": total_count},
        retryable=True,
    )
```

- [x] **Step 5: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_models tests.test_video_downloader -v
```

Expected:

```text
OK
```

### Task 1.2: 新增通用模型

- [x] **Step 1: 写失败测试**

Append to `tests/test_video_crawler_models.py`:

```python
from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    DiagnosticReport,
    MediaCandidate,
    MediaKind,
)


class VideoCrawlerModelTests(unittest.TestCase):
    def test_diagnostic_report_summarizes_candidates(self):
        report = DiagnosticReport(
            source_url="https://example.test/watch",
            candidates=[
                MediaCandidate(
                    url="https://cdn.example.test/master.m3u8",
                    kind=MediaKind.HLS,
                    source="network",
                    score=80,
                    segment_count=120,
                )
            ],
            session=BrowserSessionSnapshot(user_agent="UA"),
        )

        self.assertTrue(report.has_downloadable_candidate)
        self.assertEqual(report.best_candidate.url, "https://cdn.example.test/master.m3u8")
        self.assertIn("HLS", report.to_user_summary())
```

- [x] **Step 2: 实现模型**

Create `tools/video_crawler/models.py`:

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MediaKind(str, Enum):
    DIRECT_MP4 = "DIRECT_MP4"
    HLS = "HLS"
    DASH = "DASH"
    DRM = "DRM"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MediaCandidate:
    url: str
    kind: MediaKind
    source: str
    score: int = 0
    content_type: str = ""
    segment_count: int | None = None
    bandwidth: int | None = None
    requires_session: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BrowserSessionSnapshot:
    user_agent: str = ""
    referer: str = ""
    origin: str = ""
    cookies: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiagnosticReport:
    source_url: str
    candidates: list[MediaCandidate] = field(default_factory=list)
    session: BrowserSessionSnapshot = field(default_factory=BrowserSessionSnapshot)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def best_candidate(self) -> MediaCandidate | None:
        if not self.candidates:
            return None
        return sorted(self.candidates, key=lambda item: item.score, reverse=True)[0]

    @property
    def has_downloadable_candidate(self) -> bool:
        candidate = self.best_candidate
        return candidate is not None and candidate.kind in {
            MediaKind.DIRECT_MP4,
            MediaKind.HLS,
            MediaKind.DASH,
        }

    def to_user_summary(self) -> str:
        lines = [f"诊断 URL: {self.source_url}"]
        if self.candidates:
            lines.append(f"发现候选流: {len(self.candidates)} 个")
            for candidate in self.candidates:
                segment_text = (
                    f"，切片数 {candidate.segment_count}"
                    if candidate.segment_count is not None
                    else ""
                )
                lines.append(
                    f"- {candidate.kind.value}: {candidate.url}{segment_text}"
                )
        else:
            lines.append("未发现可下载的 MP4/M3U8/MPD 候选流。")
        lines.extend(f"警告: {warning}" for warning in self.warnings)
        lines.extend(f"错误: {error}" for error in self.errors)
        return "\n".join(lines)
```

- [x] **Step 3: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_models -v
```

Expected:

```text
OK
```

---

## Phase 2: 诊断模式

**目标：** 用户输入 URL 后可先点击“诊断链接”，查看是否发现 MP4、M3U8、MPD、疑似 DRM、登录态需求和最终候选流。

**Files:**

- Create: `tools/video_crawler/diagnostics.py`
- Create: `tools/video_crawler/sniffer.py`
- Create: `tools/video_crawler/reporting.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_diagnostics.py`

### Task 2.1: URL 静态诊断

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_diagnostics.py`:

```python
import unittest

from tools.video_crawler.diagnostics import VideoDiagnosticService
from tools.video_crawler.models import MediaKind


class VideoDiagnosticServiceTests(unittest.TestCase):
    def test_direct_mp4_url_reports_mp4_candidate(self):
        service = VideoDiagnosticService(sniffer=None)

        report = service.analyze_static_url("https://cdn.example.test/video.mp4?token=1")

        self.assertEqual(report.best_candidate.kind, MediaKind.DIRECT_MP4)
        self.assertTrue(report.has_downloadable_candidate)

    def test_direct_m3u8_url_reports_hls_candidate(self):
        service = VideoDiagnosticService(sniffer=None)

        report = service.analyze_static_url("https://cdn.example.test/master.m3u8")

        self.assertEqual(report.best_candidate.kind, MediaKind.HLS)

    def test_direct_mpd_url_reports_dash_candidate_with_warning(self):
        service = VideoDiagnosticService(sniffer=None)

        report = service.analyze_static_url("https://cdn.example.test/manifest.mpd")

        self.assertEqual(report.best_candidate.kind, MediaKind.DASH)
        self.assertIn("DASH", report.warnings[0])
```

- [x] **Step 2: 实现静态诊断**

Create `tools/video_crawler/diagnostics.py`:

```python
from tools.video_crawler.models import DiagnosticReport, MediaCandidate, MediaKind


class VideoDiagnosticService:
    def __init__(self, sniffer=None):
        self.sniffer = sniffer

    def analyze_static_url(self, url: str) -> DiagnosticReport:
        lower_url = url.lower()
        if lower_url.endswith(".mp4") or ".mp4?" in lower_url:
            return DiagnosticReport(
                source_url=url,
                candidates=[
                    MediaCandidate(
                        url=url,
                        kind=MediaKind.DIRECT_MP4,
                        source="direct-url",
                        score=100,
                    )
                ],
            )
        if lower_url.endswith(".m3u8") or ".m3u8?" in lower_url:
            return DiagnosticReport(
                source_url=url,
                candidates=[
                    MediaCandidate(
                        url=url,
                        kind=MediaKind.HLS,
                        source="direct-url",
                        score=100,
                    )
                ],
            )
        if lower_url.endswith(".mpd") or ".mpd?" in lower_url:
            return DiagnosticReport(
                source_url=url,
                candidates=[
                    MediaCandidate(
                        url=url,
                        kind=MediaKind.DASH,
                        source="direct-url",
                        score=70,
                    )
                ],
                warnings=["发现 DASH/MPD；当前阶段只诊断，下载支持在 Phase 8 实现。"],
            )
        return DiagnosticReport(source_url=url)

    def analyze(self, url: str) -> DiagnosticReport:
        report = self.analyze_static_url(url)
        if report.candidates or self.sniffer is None:
            return report
        return self.sniffer.sniff(url)
```

- [x] **Step 3: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_diagnostics -v
```

Expected:

```text
OK
```

### Task 2.2: Playwright 嗅探返回诊断报告

- [x] **Step 1: 从现有 `_sniff_real_url` 抽出候选流模型**

Create `tools/video_crawler/sniffer.py` with this public surface:

```python
from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    DiagnosticReport,
    MediaCandidate,
    MediaKind,
)


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
        session = BrowserSessionSnapshot(referer=page_url)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--mute-audio"])
            context = browser.new_context(extra_http_headers=self.headers)
            page = context.new_page()

            def handle_response(response):
                response_url = response.url
                lower_url = response_url.lower()
                content_type = response.headers.get("content-type", "").lower()
                if any(text in lower_url for text in ["ad.", "/ad/", "adv", "blank", "test", "preview", "v.admaster"]):
                    return
                if ".mpd" in lower_url or "dash+xml" in content_type:
                    candidates.append(
                        MediaCandidate(
                            url=response_url,
                            kind=MediaKind.DASH,
                            source="network",
                            score=65,
                        )
                    )
                    warnings.append("发现 DASH/MPD 候选流。")
                elif ".m3u8" in lower_url or "mpegurl" in content_type:
                    candidates.append(
                        MediaCandidate(
                            url=response_url,
                            kind=MediaKind.HLS,
                            source="network",
                            score=80,
                        )
                    )
                elif ".mp4" in lower_url or "video/mp4" in content_type:
                    candidates.append(
                        MediaCandidate(
                            url=response_url,
                            kind=MediaKind.DIRECT_MP4,
                            source="network",
                            score=75,
                        )
                    )
                elif "widevine" in lower_url or "playready" in lower_url or "fairplay" in lower_url:
                    candidates.append(
                        MediaCandidate(
                            url=response_url,
                            kind=MediaKind.DRM,
                            source="network",
                            score=0,
                        )
                    )
                    warnings.append("发现疑似 DRM 请求；本工具不会绕过 DRM。")

            page.on("response", handle_response)
            try:
                page.goto(page_url, wait_until="networkidle", timeout=25000)
                try:
                    page.locator("video").first.click(timeout=3000)
                    page.wait_for_timeout(3000)
                except Exception:
                    viewport = page.viewport_size or {"width": 1280, "height": 720}
                    page.mouse.click(viewport["width"] / 2, viewport["height"] / 2)
                    page.wait_for_timeout(3000)
            except Exception as exc:
                warnings.append(f"页面加载异常或超时: {exc}")
            finally:
                cookies = tuple(context.cookies())
                session = BrowserSessionSnapshot(
                    referer=page_url,
                    cookies=cookies,
                    headers=self.headers,
                )
                browser.close()

        return DiagnosticReport(
            source_url=page_url,
            candidates=candidates,
            session=session,
            warnings=warnings,
        )
```

- [x] **Step 2: 保持 `UniversalVideoSpider` 兼容**

Modify `_sniff_real_url` in `tools/video_downloader.py` to call `PageSniffer`, then choose `best_candidate.url`:

```python
from tools.video_crawler.models import MediaKind
from tools.video_crawler.sniffer import PageSniffer


def _sniff_real_url(self, page_url: str) -> str:
    report = PageSniffer(headers=self.headers, log_callback=self.log).sniff(page_url)
    hls_urls = [
        candidate.url
        for candidate in report.candidates
        if candidate.kind == MediaKind.HLS
    ]
    if hls_urls:
        best_url, seg_count = self._select_best_m3u8(hls_urls)
        return best_url or hls_urls[-1]
    for candidate in report.candidates:
        if candidate.kind == MediaKind.DIRECT_MP4:
            return candidate.url
    return None
```

- [x] **Step 3: 添加 UI 诊断按钮**

Modify `VideoDownloaderTool.__init__` in `tools/video_downloader.py` near the queue buttons:

```python
self.diagnose_btn = QPushButton("诊断链接")
self.diagnose_btn.clicked.connect(self.diagnose_current_url)
btn_layout.addWidget(self.diagnose_btn)
```

Add methods:

```python
def diagnose_current_url(self):
    url = self.url_entry.text().strip()
    if not url:
        QMessageBox.warning(self, "提示", "请先输入目标网址。")
        return
    threading.Thread(target=self._diagnose_task, args=(url,), daemon=True).start()

def _diagnose_task(self, url):
    try:
        from tools.video_crawler.diagnostics import VideoDiagnosticService
        from tools.video_crawler.sniffer import PageSniffer

        service = VideoDiagnosticService(
            sniffer=PageSniffer(headers={}, log_callback=self.log_signal.emit)
        )
        report = service.analyze(url)
        self.log_signal.emit("\n" + report.to_user_summary())
    except Exception as exc:
        self.log_signal.emit(f"\n[X] 诊断失败: {exc}")
```

- [x] **Step 4: 运行测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_crawler_diagnostics tests.test_video_downloader -v
```

Expected:

```text
OK
```

---

## Phase 3: 浏览器会话继承

**目标：** 将 Playwright 捕获的 Cookie、Referer、Origin、User-Agent、Authorization 等安全传给下载阶段，降低 403。

**Files:**

- Create: `tools/video_crawler/session.py`
- Modify: `tools/video_crawler/sniffer.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_session.py`

### Task 3.1: Header 合并和敏感信息脱敏

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_session.py`:

```python
import unittest

from tools.video_crawler.models import BrowserSessionSnapshot
from tools.video_crawler.session import build_download_headers, redact_sensitive_text


class BrowserSessionTests(unittest.TestCase):
    def test_build_download_headers_merges_safe_session_values(self):
        snapshot = BrowserSessionSnapshot(
            user_agent="Browser UA",
            referer="https://example.test/watch",
            origin="https://example.test",
            cookies=({"name": "sid", "value": "abc", "domain": "example.test"},),
            headers={"Authorization": "Bearer secret", "Accept-Language": "zh-CN"},
        )

        headers = build_download_headers(
            base_headers={"User-Agent": "Base UA"},
            snapshot=snapshot,
            target_url="https://cdn.example.test/video.m3u8",
        )

        self.assertEqual(headers["User-Agent"], "Browser UA")
        self.assertEqual(headers["Referer"], "https://example.test/watch")
        self.assertEqual(headers["Origin"], "https://example.test")
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["Cookie"], "sid=abc")

    def test_redact_sensitive_text_hides_tokens(self):
        redacted = redact_sensitive_text("Cookie: sid=abc Authorization: Bearer secret token=xyz")

        self.assertIn("Cookie: <redacted>", redacted)
        self.assertIn("Authorization: <redacted>", redacted)
        self.assertIn("token=<redacted>", redacted)
```

- [x] **Step 2: 实现 session 工具**

Create `tools/video_crawler/session.py`:

```python
from urllib.parse import urlparse

from tools.video_crawler.models import BrowserSessionSnapshot


SENSITIVE_HEADER_NAMES = {"cookie", "authorization", "x-token", "x-auth-token"}


def _cookie_matches_target(cookie: dict, target_host: str) -> bool:
    domain = str(cookie.get("domain", "")).lstrip(".").lower()
    return bool(domain) and (target_host == domain or target_host.endswith("." + domain))


def _cookie_header(cookies: tuple[dict, ...], target_url: str) -> str:
    target_host = urlparse(target_url).hostname or ""
    pairs = []
    for cookie in cookies:
        if _cookie_matches_target(cookie, target_host):
            pairs.append(f"{cookie.get('name')}={cookie.get('value')}")
    return "; ".join(pairs)


def build_download_headers(
    base_headers: dict[str, str],
    snapshot: BrowserSessionSnapshot,
    target_url: str,
) -> dict[str, str]:
    headers = dict(base_headers)
    if snapshot.user_agent:
        headers["User-Agent"] = snapshot.user_agent
    if snapshot.referer:
        headers["Referer"] = snapshot.referer
    if snapshot.origin:
        headers["Origin"] = snapshot.origin

    for name in ("Authorization", "Accept", "Accept-Language", "Range"):
        value = snapshot.headers.get(name)
        if value:
            headers[name] = value

    cookie_value = _cookie_header(snapshot.cookies, target_url)
    if cookie_value:
        headers["Cookie"] = cookie_value
    return headers


def redact_sensitive_text(text: str) -> str:
    redacted = text
    redacted = redacted.replace("Cookie:", "Cookie: <redacted>")
    redacted = redacted.replace("Authorization:", "Authorization: <redacted>")
    for key in ("token=", "access_token=", "auth="):
        if key in redacted:
            prefix, _, suffix = redacted.partition(key)
            tail = suffix.split(" ", 1)
            rest = "" if len(tail) == 1 else " " + tail[1]
            redacted = prefix + key + "<redacted>" + rest
    return redacted
```

- [x] **Step 3: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_session -v
```

Expected:

```text
OK
```

### Task 3.2: 嗅探后传递会话快照

- [x] **Step 1: 扩展 `UniversalVideoSpider` 构造参数**

Modify `UniversalVideoSpider.__init__`:

```python
def __init__(
    self,
    output_dir="./downloads",
    temp_dir="./temp",
    log_callback=None,
    is_high_speed=False,
    segment_concurrency=None,
    session_snapshot=None,
):
    ...
    self.session_snapshot = session_snapshot
```

- [x] **Step 2: 网页嗅探成功后合并 headers**

Modify the webpage branch in `UniversalVideoSpider.run`:

```python
from tools.video_crawler.session import build_download_headers

report = PageSniffer(headers=self.headers, log_callback=self.log).sniff(url)
candidate = report.best_candidate
if candidate:
    self.headers = build_download_headers(self.headers, report.session, candidate.url)
    self.log(f"[+] 嗅探成功，真实地址为: {candidate.url}")
    return self.run(candidate.url, output_filename)
raise VideoDownloadError(VideoErrorCode.NO_MEDIA_FOUND, "嗅探失败，未能找到视频流")
```

This replaces the older `Referer`/`Origin` only logic.

- [x] **Step 3: 验证敏感信息不出现在日志**

Add a test in `tests/test_video_crawler_session.py` that logs `report.to_user_summary()` from a report containing cookies and asserts cookie values are absent.

```python
def test_diagnostic_summary_does_not_include_cookie_values(self):
    snapshot = BrowserSessionSnapshot(
        cookies=({"name": "sid", "value": "secret-cookie", "domain": "example.test"},)
    )
    from tools.video_crawler.models import DiagnosticReport

    summary = DiagnosticReport(
        source_url="https://example.test/watch",
        session=snapshot,
    ).to_user_summary()

    self.assertNotIn("secret-cookie", summary)
```

- [x] **Step 4: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_session tests.test_video_downloader -v
```

Expected:

```text
OK
```

---

## Phase 4: HLS 边界补强

**目标：** 提升 HLS 兼容性，优先支持多 Key、每切片 IV、默认 IV、BYTERANGE、多 `EXT-X-MAP` 和 DISCONTINUITY。

**Files:**

- Create: `tools/video_crawler/adapters/__init__.py`
- Create: `tools/video_crawler/adapters/hls.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_hls.py`

### Task 4.1: 多 Key 和 IV 解析

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_hls.py`:

```python
import unittest

from tools.video_crawler.adapters.hls import derive_hls_iv


class HlsAdapterTests(unittest.TestCase):
    def test_explicit_iv_is_used(self):
        iv = derive_hls_iv("0x0000000000000000000000000000002a", media_sequence=7)

        self.assertEqual(iv, (42).to_bytes(16, "big"))

    def test_missing_iv_uses_media_sequence_number(self):
        iv = derive_hls_iv(None, media_sequence=7)

        self.assertEqual(iv, (7).to_bytes(16, "big"))
```

- [x] **Step 2: 实现 IV 工具**

Create `tools/video_crawler/adapters/hls.py`:

```python
def derive_hls_iv(iv_text: str | None, media_sequence: int) -> bytes:
    if iv_text:
        normalized = iv_text[2:] if iv_text.lower().startswith("0x") else iv_text
        return bytes.fromhex(normalized.zfill(32))
    return int(media_sequence).to_bytes(16, "big")
```

- [x] **Step 3: 把 `_download_m3u8` 中的单一 cipher 改为逐切片 cipher**

Implementation direction:

```python
def _build_segment_cipher(self, key, media_sequence):
    if not key:
        return None
    key_url = key.absolute_uri
    key_response = requests.get(key_url, headers=self.headers, timeout=15)
    key_response.raise_for_status()
    key_bytes = key_response.content
    iv = derive_hls_iv(key.iv, media_sequence)
    return Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
```

Change download items from `(segment.absolute_uri, save_path)` to:

```python
download_items.append({
    "url": segment.absolute_uri,
    "save_path": save_path,
    "cipher": self._build_segment_cipher(segment.key, playlist.media_sequence + i),
})
```

Change `_download_segments` so each item passes its own cipher:

```python
async def bounded_download(item):
    async with semaphore:
        return await self._download_ts(item["url"], item["save_path"], item["cipher"])
```

- [x] **Step 4: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_hls tests.test_video_downloader -v
```

Expected:

```text
OK
```

### Task 4.2: BYTERANGE 下载

- [x] **Step 1: 设计下载项结构**

Use this item shape:

```python
{
    "url": "https://cdn.example.test/file.ts",
    "save_path": "temp/video/00001.ts",
    "cipher": None,
    "range_header": "bytes=1000-1999",
}
```

- [x] **Step 2: 写 range 计算测试**

Append to `tests/test_video_crawler_hls.py`:

```python
from tools.video_crawler.adapters.hls import parse_hls_byterange


def test_byterange_with_offset(self):
    self.assertEqual(parse_hls_byterange("1000@500"), "bytes=500-1499")

def test_byterange_without_offset_uses_previous_end(self):
    self.assertEqual(parse_hls_byterange("1000", previous_end=499), "bytes=500-1499")
```

- [x] **Step 3: 实现 range 计算**

Append to `tools/video_crawler/adapters/hls.py`:

```python
def parse_hls_byterange(byterange: str | None, previous_end: int | None = None) -> str | None:
    if not byterange:
        return None
    if "@" in byterange:
        length_text, start_text = byterange.split("@", 1)
        start = int(start_text)
    else:
        if previous_end is None:
            start = 0
        else:
            start = previous_end + 1
        length_text = byterange
    length = int(length_text)
    end = start + length - 1
    return f"bytes={start}-{end}"
```

- [x] **Step 4: 下载切片时应用 Range**

Modify `_download_ts`:

```python
async def _download_ts(self, session, ts_url, save_path, cipher, extra_headers=None):
    ...
    async with session.get(ts_url, timeout=timeout, headers=extra_headers or {}) as response:
        ...
```

Ensure `aiohttp.ClientSession(headers=self.headers, trust_env=True)` remains the base headers and `extra_headers` only adds per-segment values.

- [x] **Step 5: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_hls tests.test_video_downloader -v
```

Expected:

```text
OK
```

### Task 4.3: 多 EXT-X-MAP 和 DISCONTINUITY

- [x] **Step 1: 为 fMP4 片段绑定 init map**

The planned item shape:

```python
{
    "url": segment.absolute_uri,
    "save_path": save_path,
    "cipher": segment_cipher,
    "range_header": range_header,
    "init_map_url": segment.init_section.absolute_uri if segment.init_section else None,
    "discontinuity": bool(segment.discontinuity),
}
```

- [x] **Step 2: 处理合并策略**

Rules:

- TS without init map: keep current FFmpeg concat flow.
- fMP4 with one init map: keep current binary init + fragments + FFmpeg repair flow.
- fMP4 with multiple init maps: build grouped raw files per map, then feed those intermediate MP4 files to FFmpeg concat.
- DISCONTINUITY in TS: preserve original order and let FFmpeg concat handle timeline breaks.
- DISCONTINUITY in fMP4: start a new group to avoid mixing incompatible init sections.

- [x] **Step 3: 验收测试**

Add tests that do not need real media:

- `test_multiple_init_maps_create_multiple_merge_groups`
- `test_discontinuity_starts_new_fmp4_group`
- `test_ts_discontinuity_keeps_original_order`

Use pure helper functions in `tools/video_crawler/adapters/hls.py` so tests do not invoke FFmpeg.

---

## Phase 5: 结构化报告和重试策略

**目标：** 队列弹窗不仅显示字符串错误，还显示错误码、可重试性、建议动作和候选流统计。

**Files:**

- Create: `tools/video_crawler/reporting.py`
- Modify: `tools/video_downloader.py`
- Modify: `tests/test_video_downloader.py`

### Task 5.1: 下载结果报告模型

- [x] **Step 1: 写失败测试**

Append to `tests/test_video_downloader.py`:

```python
def test_batch_summary_groups_errors_by_code(self):
    from tools.video_crawler.errors import VideoErrorCode

    summary, details = self.tool.format_batch_results([
        {
            "task": {"name": "blocked"},
            "success": False,
            "output_path": "",
            "error": "服务器拒绝访问",
            "error_code": VideoErrorCode.HTTP_FORBIDDEN.value,
            "retryable": False,
        }
    ])

    self.assertIn("失败 1 个", summary)
    self.assertIn("HTTP_FORBIDDEN", details)
    self.assertIn("服务器拒绝访问", details)
```

- [x] **Step 2: `_execute_task` 捕获结构化错误**

Modify exception handling:

```python
except VideoDownloadError as e:
    self.log_signal.emit(f"\n[X] 错误: {e}")
    return {
        "task": task,
        "success": False,
        "output_path": "",
        "error": str(e),
        "error_code": e.code.value,
        "retryable": e.retryable,
    }
```

Keep a generic branch:

```python
except Exception as e:
    self.log_signal.emit(f"\n[X] 错误: {e}")
    return {
        "task": task,
        "success": False,
        "output_path": "",
        "error": str(e),
        "error_code": "UNKNOWN",
        "retryable": False,
    }
```

- [x] **Step 3: 更新批次详情**

Modify `format_batch_results` failed task line:

```python
code = result.get("error_code", "UNKNOWN")
retry_text = "可重试" if result.get("retryable") else "不建议直接重试"
f"  ✗ {result['task']['name']} [{code} / {retry_text}]: {result['error']}"
```

- [x] **Step 4: 运行测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader -v
```

Expected:

```text
OK
```

### Task 5.2: 只重试可重试失败

- [x] **Step 1: 调整 UI 语义**

If any failed result has `retryable=True`, show button:

```python
"重试可恢复失败任务"
```

If all failures are non-retryable, keep only Close and include explanation in details.

- [x] **Step 2: 修改 `retry_failed_tasks`**

```python
def retry_failed_tasks(self, results):
    for result in results:
        if not result["success"] and result.get("retryable", True):
            self._enqueue_task(result["task"])
```

- [x] **Step 3: 添加测试**

```python
def test_retry_requeues_only_retryable_failures(self):
    retryable_task = {
        "url": "https://example.invalid/retry.m3u8",
        "name": "retry",
        "save_dir": "downloads",
        "is_high_speed": False,
        "segment_concurrency": 5,
    }
    blocked_task = {
        "url": "https://example.invalid/drm.mpd",
        "name": "blocked",
        "save_dir": "downloads",
        "is_high_speed": False,
        "segment_concurrency": 5,
    }
    self.tool.retry_failed_tasks([
        {"task": retryable_task, "success": False, "retryable": True},
        {"task": blocked_task, "success": False, "retryable": False},
    ])

    self.assertEqual(self.tool.task_queue.get_nowait(), retryable_task)
    self.assertTrue(self.tool.task_queue.empty())
    self.tool.task_queue.task_done()
```

---

## Phase 6: 断点续传和切片级恢复

**目标：** 任务失败后再次执行时复用已下载且有效的切片，只补失败部分。

**Files:**

- Create: `tools/video_crawler/resume.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_resume.py`

### Task 6.1: 切片 Manifest

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_resume.py`:

```python
import os
import tempfile
import unittest

from tools.video_crawler.resume import SegmentManifest


class SegmentManifestTests(unittest.TestCase):
    def test_manifest_round_trips_segment_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "manifest.json")
            manifest = SegmentManifest(path)
            manifest.mark_downloaded("00001.ts", url="https://cdn/1.ts", size=12)
            manifest.save()

            loaded = SegmentManifest(path)
            loaded.load()

            self.assertTrue(loaded.is_downloaded("00001.ts", expected_size=12))
            self.assertFalse(loaded.is_downloaded("00002.ts", expected_size=12))
```

- [x] **Step 2: 实现 manifest**

Create `tools/video_crawler/resume.py`:

```python
import json
import os


class SegmentManifest:
    def __init__(self, path: str):
        self.path = path
        self.data = {"segments": {}}

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as manifest_file:
                self.data = json.load(manifest_file)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as manifest_file:
            json.dump(self.data, manifest_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)

    def mark_downloaded(self, filename: str, *, url: str, size: int) -> None:
        self.data["segments"][filename] = {"url": url, "size": size}

    def is_downloaded(self, filename: str, expected_size: int | None = None) -> bool:
        item = self.data["segments"].get(filename)
        if not item:
            return False
        if expected_size is not None and item.get("size") != expected_size:
            return False
        return True
```

- [x] **Step 3: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_resume -v
```

Expected:

```text
OK
```

### Task 6.2: 下载前跳过有效切片

- [x] **Step 1: 修改 `_download_m3u8`**

Before building `download_items`:

```python
from tools.video_crawler.resume import SegmentManifest

manifest_path = os.path.join(video_temp_dir, ".firefly-segments.json")
manifest = SegmentManifest(manifest_path)
manifest.load()
```

When creating each item:

```python
filename = f"{i:05d}.ts"
save_path = os.path.join(video_temp_dir, filename)
if os.path.exists(save_path) and os.path.getsize(save_path) > 0 and manifest.is_downloaded(filename):
    self.log(f"[*] 跳过已完成切片: {filename}")
    ts_files_list.append(save_path)
    continue
download_items.append({...})
```

After each successful download:

```python
manifest.mark_downloaded(os.path.basename(save_path), url=ts_url, size=os.path.getsize(save_path))
manifest.save()
```

- [x] **Step 2: 清理策略**

Rules:

- 下载成功并合并成功后，删除 manifest 和切片临时文件。
- 下载失败时保留 manifest 和已下载切片。
- 清理失败不能覆盖原始下载失败原因。

- [x] **Step 3: UI 入口**

Add a checkbox next to concurrency:

```python
self.resume_chk = QCheckBox("复用未完成切片")
self.resume_chk.setChecked(True)
```

Add task field:

```python
"resume_enabled": self.resume_chk.isChecked()
```

Pass into spider:

```python
resume_enabled=task.get("resume_enabled", True)
```

---

## Phase 7: 适配器架构

**目标：** 把直链 MP4、HLS、网页嗅探、DASH、yt-dlp 从 `UniversalVideoSpider` 中抽出，降低单文件复杂度。

**Files:**

- Create: `tools/video_crawler/adapters/base.py`
- Create: `tools/video_crawler/adapters/direct_mp4.py`
- Move into: `tools/video_crawler/adapters/hls.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_adapters.py`

### Task 7.1: Adapter 协议

- [x] **Step 1: 创建协议**

Create `tools/video_crawler/adapters/base.py`:

```python
from typing import Protocol

from tools.video_crawler.models import DiagnosticReport, MediaCandidate


class VideoAdapter(Protocol):
    name: str
    priority: int

    def can_handle(self, candidate: MediaCandidate) -> bool:
        ...

    def download(self, candidate: MediaCandidate, output_filename: str) -> str:
        ...

    def diagnose(self, url: str) -> DiagnosticReport:
        ...
```

- [x] **Step 2: 编排器选择规则**

Selection order:

1. Direct URL candidates.
2. Known site adapters, when introduced.
3. Page sniffer candidates.
4. HLS adapter.
5. DASH adapter.
6. yt-dlp adapter if enabled.
7. `NO_MEDIA_FOUND` diagnostic failure.

- [x] **Step 3: 兼容 `UniversalVideoSpider.run`**

Keep this method signature unchanged:

```python
def run(self, url: str, output_filename: str):
```

Internally call the orchestrator:

```python
result_path = self.orchestrator.download(url, output_filename)
return self._verify_output(result_path)
```

### Task 7.2: 渐进迁移策略

Migration order:

1. Move `_download_mp4` to `DirectMp4Adapter`.
2. Move `_download_m3u8`, `_download_ts`, `_download_segments`, `_merge_with_ffmpeg` to `HlsAdapter`.
3. Keep old methods on `UniversalVideoSpider` as thin wrappers for one release cycle.
4. Update tests to target adapter helpers directly.
5. Keep `VideoDownloaderTool` unchanged except constructor wiring.

Acceptance:

- Existing `tests/test_video_downloader.py` still passes.
- New adapter tests pass.
- `tools/video_downloader.py` drops below roughly 450 lines after migration.

---

## Phase 8: DASH / MPD 支持

**目标：** 支持无 DRM 的静态 MPD：解析视频轨和音频轨，下载分段，用 FFmpeg mux 成 MP4；遇到 DRM 明确报 `UNSUPPORTED_DRM`。

**Files:**

- Create: `tools/video_crawler/adapters/dash.py`
- Modify: `tools/video_crawler/errors.py`
- Modify: `tools/video_crawler/diagnostics.py`
- Create: `tests/test_video_crawler_dash.py`

### Task 8.1: MPD 解析和 DRM 检测

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_dash.py`:

```python
import unittest

from tools.video_crawler.adapters.dash import parse_mpd_capabilities


class DashAdapterTests(unittest.TestCase):
    def test_detects_content_protection_as_drm(self):
        mpd = """<?xml version="1.0"?>
        <MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
          <Period>
            <AdaptationSet mimeType="video/mp4">
              <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
            </AdaptationSet>
          </Period>
        </MPD>"""

        info = parse_mpd_capabilities(mpd)

        self.assertTrue(info.has_drm)

    def test_detects_video_and_audio_adaptation_sets(self):
        mpd = """<?xml version="1.0"?>
        <MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
          <Period>
            <AdaptationSet mimeType="video/mp4"><Representation bandwidth="1000"/></AdaptationSet>
            <AdaptationSet mimeType="audio/mp4"><Representation bandwidth="128"/></AdaptationSet>
          </Period>
        </MPD>"""

        info = parse_mpd_capabilities(mpd)

        self.assertTrue(info.has_video)
        self.assertTrue(info.has_audio)
```

- [x] **Step 2: 实现解析能力**

Create `tools/video_crawler/adapters/dash.py`:

```python
from dataclasses import dataclass
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class MpdCapabilities:
    has_video: bool
    has_audio: bool
    has_drm: bool


def _strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1]


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
    return MpdCapabilities(has_video=has_video, has_audio=has_audio, has_drm=has_drm)
```

- [x] **Step 3: 诊断中识别 DRM**

If MPD contains `ContentProtection`, raise or report:

```python
VideoDownloadError(
    VideoErrorCode.UNSUPPORTED_DRM,
    "发现 DRM 保护内容，本工具不会绕过 DRM。",
    retryable=False,
)
```

### Task 8.2: 静态 SegmentTemplate 下载

Supported first version:

- Static MPD only.
- `SegmentTemplate` with `initialization` and `media`.
- `$Number$` replacement.
- Pick highest bandwidth video representation.
- Pick highest bandwidth audio representation.
- Download video and audio separately, then FFmpeg mux.

First version rejection cases:

- Dynamic/live MPD.
- DRM.
- SegmentTimeline with irregular durations.
- BaseURL inheritance that cannot be resolved.
- Widevine/PlayReady/FairPlay ContentProtection.

Implementation steps:

- [x] Parse MPD with `xml.etree.ElementTree`.
- [x] Resolve `BaseURL`.
- [x] Build segment URLs for chosen video and audio tracks.
- [x] Download each track into separate temp folders using existing segment concurrency.
- [x] Merge video fragments into `video.mp4`.
- [x] Merge audio fragments into `audio.mp4`.
- [x] Run FFmpeg:

```powershell
ffmpeg -y -i video.mp4 -i audio.mp4 -c copy output.mp4
```

- [x] Raise `FFMPEG_FAILED` on mux failure.
- [x] Add tests around parsing and command construction; use patched `subprocess.run` for mux tests.

---

## Phase 9: yt-dlp 可选后备

**目标：** 对公开平台页面提供可选外部后备引擎，同时保留自研 MP4/HLS/DASH 路径。

**Files:**

- Create: `tools/video_crawler/adapters/ytdlp.py`
- Modify: `tools/video_downloader.py`
- Create: `tests/test_video_crawler_ytdlp.py`

### Task 9.1: 外部引擎探测

- [x] **Step 1: 写失败测试**

Create `tests/test_video_crawler_ytdlp.py`:

```python
import unittest
from unittest.mock import patch

from tools.video_crawler.adapters.ytdlp import YtDlpAdapter


class YtDlpAdapterTests(unittest.TestCase):
    def test_adapter_disabled_when_executable_missing(self):
        with patch("tools.video_crawler.adapters.ytdlp.shutil.which", return_value=None):
            self.assertFalse(YtDlpAdapter(enabled=True).is_available())

    def test_adapter_requires_user_enabled_flag(self):
        with patch("tools.video_crawler.adapters.ytdlp.shutil.which", return_value="yt-dlp"):
            self.assertFalse(YtDlpAdapter(enabled=False).is_available())
```

- [x] **Step 2: 实现探测**

Create `tools/video_crawler/adapters/ytdlp.py`:

```python
import shutil


class YtDlpAdapter:
    name = "yt-dlp"
    priority = 10

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def is_available(self) -> bool:
        return self.enabled and shutil.which("yt-dlp") is not None
```

### Task 9.2: UI 控制和错误回收

- [x] Add checkbox:

```python
self.ytdlp_chk = QCheckBox("公开平台失败时尝试 yt-dlp")
self.ytdlp_chk.setChecked(False)
```

- [x] Add task field:

```python
"use_ytdlp_fallback": self.ytdlp_chk.isChecked()
```

- [x] If self-developed adapters return `NO_MEDIA_FOUND` or `UNSUPPORTED_DASH`, and fallback is enabled, call `YtDlpAdapter`.
- [x] Capture subprocess failure into `VideoDownloadError(VideoErrorCode.UNKNOWN, "...", retryable=False)`.
- [x] Never print cookies, authorization headers, or tokens in yt-dlp command logs.

Acceptance:

- Fallback is opt-in.
- Existing direct MP4/HLS paths do not call yt-dlp.
- Queue result dialog clearly labels external engine usage.

---

## 10. 验收矩阵

| Roadmap 功能 | 对应阶段 | 验收信号 |
|---|---|---|
| 诊断模式 | Phase 2 | 输入 MP4/M3U8/MPD/网页 URL 能输出诊断摘要 |
| 浏览器会话继承 | Phase 3 | Cookie、Referer、Origin、UA 可传给下载请求且日志脱敏 |
| HLS 多 Key / IV | Phase 4 | 每切片可使用独立 Key 和正确 IV |
| HLS BYTERANGE | Phase 4 | Range header 根据 `EXT-X-BYTERANGE` 正确生成 |
| HLS DISCONTINUITY / 多 MAP | Phase 4 | fMP4 分组策略有纯函数测试覆盖 |
| 错误分类 | Phase 1 / Phase 5 | 队列结果包含 error_code 和 retryable |
| 断点续传 | Phase 6 | 重试时跳过已完成切片，失败时保留 manifest |
| 适配器架构 | Phase 7 | `UniversalVideoSpider.run` 兼容，核心下载迁移到 adapters |
| DASH / MPD | Phase 8 | 无 DRM 静态 MPD 可解析、下载和 mux；DRM 明确拒绝 |
| yt-dlp 后备 | Phase 9 | 用户启用且本机安装时才调用外部引擎 |

## 11. 风险和处理

- **Git HEAD 已知异常：** 每个阶段的提交步骤必须以 `git status` 正常为前提。
- **Playwright 真实网站测试不稳定：** 单元测试用假 sniffer、假 response、patch subprocess；真实站点只作为人工验收。
- **FFmpeg 依赖路径：** 当前代码调用 `ffmpeg`，后续可增加本地 `tools/ffmpeg.exe` 探测，但不作为 Phase 1-4 的前置。
- **HLS 边界组合复杂：** 多 Key、BYTERANGE、多 MAP 分开做，避免一次性改完整 HLS 管线。
- **DASH 范围容易膨胀：** 第一版只支持无 DRM 静态 SegmentTemplate；复杂 MPD 用结构化错误拒绝。
- **敏感信息泄露：** 所有报告、日志、异常 details 输出前必须经过脱敏策略。

## 12. 推荐执行顺序

建议每次只执行一个 Phase，并在阶段结束后更新本文件或新增阶段总结：

1. Phase 0 确认基线。
2. Phase 1 先落结构化错误和模型。
3. Phase 2 增加诊断入口，让用户先看懂失败原因。
4. Phase 3 接入会话继承，解决常见 403。
5. Phase 4 分批补 HLS 边界。
6. Phase 5 优化队列报告和重试策略。
7. Phase 6 做断点续传。
8. Phase 7 在已有行为稳定后抽离适配器。
9. Phase 8 做 DASH。
10. Phase 9 做 yt-dlp 后备。

## 13. 自检结果

- 覆盖性：路线图中的 8 个未实现方向均映射到阶段和验收项。
- 范围控制：大平台站点适配不作为前置，只保留适配器扩展点和 yt-dlp 可选后备。
- 安全边界：DRM 只识别和提示，不绕过。
- 测试策略：每个阶段至少包含一个可自动化验证的单元测试方向。
- 兼容策略：`VideoDownloaderTool` 和 `UniversalVideoSpider.run(url, output_filename)` 在迁移期间保持外部接口不变。
