# Video Crawler Access-Limited Sniffing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让视频爬虫在普通 headless 嗅探遇到 `403 - 访问受限` 或空会话无法触发播放器时，能给出准确错误，并提供可视化、持久化会话和增强媒体地址发现能力，提高用户已授权页面的成功嗅探率。

**Architecture:** 保持 `tools/video_downloader.py` 作为主界面入口，把访问受限诊断、浏览器启动选项、持久化会话和媒体响应提取集中在 `tools/video_crawler/sniffer.py` 与小型模型中。下载适配器不重写；一旦嗅探到 `.m3u8`、`.mp4` 或 `.mpd`，继续交给现有 HLS/MP4/DASH/yt-dlp 路径处理。所有新增 UI 控件和弹窗必须和主界面现有 PyQt6 + 动态 QSS 风格保持一致。

**Tech Stack:** Python 3.x, PyQt6, Playwright, requests, unittest, existing `tools.video_crawler` package.

---

## 0. 问题边界和安全边界

已复现的失败事实：

- 普通浏览器不开 VPN 可以打开目标页面。
- 程序化访问和当前 Playwright headless 空会话访问 `https://www.aowu.tv/w/BNCxTD01jh6N#s=5249&ep=16` 会进入 `403 - 访问受限` 页面。
- 403 页面里 `video` 数量为 0，`iframe` 数量为 0，没有 `.m3u8`、`.mp4`、`.mpd`、`video/mp4`、`mpegurl` 响应。
- 当前代码最后报 `NO_MEDIA_FOUND`，对用户不够准确；更准确的错误应为 `HTTP_FORBIDDEN` 或“页面访问受限，未进入播放器”。

不做的事：

- 不绕过 Widevine、FairPlay、PlayReady 或任何 DRM。
- 不自动破解验证码、人机校验、付费墙、账号权限或地区限制。
- 不模拟攻击性反检测策略；只提供可视化浏览器、用户人工登录/验证后的持久化会话复用，以及更完整的媒体地址发现。
- 不把主界面改成专业抓包面板；新增控件保持轻量。
- 不删除用户下载产物、浏览器会话目录、断点续传 manifest、未完成切片或计划文档。

每个阶段完成后运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

每个阶段完成并验证后清理完全无用的临时文件，例如 `tests/.tmp/tmp*`、`tests/tmp*`、`tests/__pycache__`、`tools/video_crawler/**/__pycache__`。清理前必须确认目标绝对路径位于 `D:\Study\Projects\PythonProject\FireflyTools` 内，且不是浏览器 profile、断点续传 manifest、未完成切片、用户下载产物、源码、测试源码或计划文档。

---

## 1. 目标文件结构

```text
tools/
├─ video_downloader.py
└─ video_crawler/
   ├─ models.py                    # 新增 SnifferOptions、PageAccessSnapshot
   ├─ sniffer.py                   # 主响应状态诊断、可视化/持久化浏览器、增强媒体提取
   ├─ spider.py                    # 将 SnifferOptions 传给 PageSniffer
   ├─ diagnostics.py               # 诊断模式使用同一套 sniffer options
   └─ errors.py                    # 复用 HTTP_FORBIDDEN、NO_MEDIA_FOUND 等错误码
tests/
├─ test_video_crawler_diagnostics.py
├─ test_video_crawler_sniffer_access.py
├─ test_video_downloader.py
└─ test_video_crawler_session.py
```

新增运行目录：

```text
browser_profiles/
└─ video_crawler/                  # Playwright 持久化会话目录；不得作为临时文件清理
```

---

## Phase A: 准确识别访问受限页面

**目标：** 当前页面主响应为 403、标题包含“访问受限”、或页面没有播放器且明确显示访问限制时，返回 `HTTP_FORBIDDEN`，不再误报 `NO_MEDIA_FOUND`。

**Files:**

- Modify: `tools/video_crawler/models.py`
- Modify: `tools/video_crawler/sniffer.py`
- Modify: `tools/video_crawler/spider.py`
- Create: `tests/test_video_crawler_sniffer_access.py`
- Modify: `tests/test_video_downloader.py`

### Task A1: 增加页面访问快照模型

- [ ] **Step 1: 写失败测试**

Create `tests/test_video_crawler_sniffer_access.py`:

```python
import unittest

from tools.video_crawler.models import PageAccessSnapshot
from tools.video_crawler.sniffer import detect_access_limited_page
from tools.video_crawler.errors import VideoErrorCode


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access -v
```

Expected:

```text
ImportError: cannot import name 'PageAccessSnapshot'
```

- [ ] **Step 3: 实现 `PageAccessSnapshot`**

Modify `tools/video_crawler/models.py`:

```python
@dataclass(frozen=True)
class PageAccessSnapshot:
    status_code: int | None = None
    title: str = ""
    final_url: str = ""
    video_count: int = 0
    iframe_count: int = 0
```

- [ ] **Step 4: 实现访问受限判定**

Modify `tools/video_crawler/sniffer.py` imports:

```python
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode
from tools.video_crawler.models import (
    BrowserSessionSnapshot,
    DiagnosticReport,
    MediaCandidate,
    MediaKind,
    PageAccessSnapshot,
)
```

Add pure helper near `classify_media_response`:

```python
ACCESS_LIMITED_TITLE_KEYWORDS = ("403", "访问受限", "access denied", "forbidden")


def detect_access_limited_page(snapshot: PageAccessSnapshot) -> VideoDownloadError | None:
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
```

- [ ] **Step 5: 测试通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access -v
```

Expected:

```text
OK
```

### Task A2: 在真实嗅探流程中使用页面访问快照

- [ ] **Step 1: 写失败测试**

Append to `tests/test_video_downloader.py` in `UniversalVideoSpiderTests`:

```python
    def test_webpage_forbidden_report_uses_http_forbidden_code(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode

            with patch.object(
                spider,
                "_sniff_real_url",
                side_effect=VideoDownloadError(
                    VideoErrorCode.HTTP_FORBIDDEN,
                    "页面访问受限",
                    retryable=False,
                ),
            ):
                with self.assertRaises(VideoDownloadError) as raised:
                    spider.run("https://example.test/watch", "video")

        self.assertEqual(raised.exception.code, VideoErrorCode.HTTP_FORBIDDEN)
```

- [ ] **Step 2: 运行测试确认当前传播行为**

Run:

```powershell
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests.test_webpage_forbidden_report_uses_http_forbidden_code -v
```

Expected:

```text
OK
```

This confirms `spider.run` will preserve a structured `HTTP_FORBIDDEN` once `PageSniffer` raises it.

- [ ] **Step 3: 修改 `PageSniffer.sniff` 采集主响应状态**

In `tools/video_crawler/sniffer.py`, inside `PageSniffer.sniff`, change the `page.goto` section:

```python
main_response = None
try:
    main_response = page.goto(page_url, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(3000)
    access_snapshot = PageAccessSnapshot(
        status_code=main_response.status if main_response else None,
        title=page.title(),
        final_url=page.url,
        video_count=page.locator("video").count(),
        iframe_count=page.locator("iframe").count(),
    )
    access_error = detect_access_limited_page(access_snapshot)
    if access_error:
        raise access_error
    self.log("[*] 正在尝试模拟点击播放器以触发真实数据流...")
    ...
```

Keep the existing click fallback after this block.

- [ ] **Step 4: 不吞掉结构化错误**

In the `except` block of `PageSniffer.sniff`, handle `VideoDownloadError` before generic exceptions:

```python
except VideoDownloadError:
    raise
except Exception as exc:
    warnings.append(f"页面加载异常或超时: {exc}")
```

- [ ] **Step 5: 增加日志信息**

After creating `access_snapshot`, add:

```python
self.log(
    "[*] 页面诊断: "
    f"状态码={access_snapshot.status_code}, "
    f"标题={access_snapshot.title or '未知'}, "
    f"video={access_snapshot.video_count}, "
    f"iframe={access_snapshot.iframe_count}"
)
```

- [ ] **Step 6: 运行测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_crawler_sniffer_access tests.test_video_downloader -v
```

Expected:

```text
OK
```

---

## Phase B: 增加可视化和持久化会话嗅探选项

**目标：** 用户可选择用可视化浏览器打开页面，必要时人工完成登录、地区确认或站点允许的访问检查；工具复用同一个 Playwright profile 捕获播放器发出的媒体请求。

**Files:**

- Modify: `tools/video_crawler/models.py`
- Modify: `tools/video_crawler/sniffer.py`
- Modify: `tools/video_crawler/spider.py`
- Modify: `tools/video_downloader.py`
- Modify: `tests/test_video_downloader.py`
- Create: `tests/test_video_crawler_sniffer_access.py` additions

### Task B1: 新增 `SnifferOptions`

- [ ] **Step 1: 写失败测试**

Append to `tests/test_video_crawler_sniffer_access.py`:

```python
from tools.video_crawler.models import SnifferOptions


class SnifferOptionsTests(unittest.TestCase):
    def test_default_options_are_headless_and_non_persistent(self):
        options = SnifferOptions()

        self.assertTrue(options.headless)
        self.assertFalse(options.use_persistent_profile)
        self.assertEqual(options.manual_wait_seconds, 10)

    def test_profile_dir_defaults_to_workspace_relative_path(self):
        options = SnifferOptions(use_persistent_profile=True)

        self.assertIn("browser_profiles", options.profile_dir)
        self.assertIn("video_crawler", options.profile_dir)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.SnifferOptionsTests -v
```

Expected:

```text
ImportError: cannot import name 'SnifferOptions'
```

- [ ] **Step 3: 实现模型**

Modify `tools/video_crawler/models.py`:

```python
@dataclass(frozen=True)
class SnifferOptions:
    headless: bool = True
    use_persistent_profile: bool = False
    profile_dir: str = "./browser_profiles/video_crawler"
    manual_wait_seconds: int = 10

    @property
    def visible(self) -> bool:
        return not self.headless
```

- [ ] **Step 4: 测试通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.SnifferOptionsTests -v
```

Expected:

```text
OK
```

### Task B2: `PageSniffer` 使用 `SnifferOptions`

- [ ] **Step 1: 写构造参数测试**

Append to `tests/test_video_crawler_sniffer_access.py`:

```python
from tools.video_crawler.sniffer import PageSniffer


class PageSnifferOptionsWiringTests(unittest.TestCase):
    def test_sniffer_keeps_options(self):
        options = SnifferOptions(headless=False, use_persistent_profile=True)
        sniffer = PageSniffer(options=options)

        self.assertIs(sniffer.options, options)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.PageSnifferOptionsWiringTests -v
```

Expected:

```text
TypeError: PageSniffer.__init__() got an unexpected keyword argument 'options'
```

- [ ] **Step 3: 修改构造函数**

Modify `tools/video_crawler/sniffer.py`:

```python
from tools.video_crawler.models import SnifferOptions


class PageSniffer:
    def __init__(self, headers=None, log_callback=None, options: SnifferOptions | None = None):
        self.headers = headers or {}
        self.log_callback = log_callback
        self.options = options or SnifferOptions()
```

- [ ] **Step 4: 抽出浏览器上下文创建方法**

Add methods to `PageSniffer`:

```python
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
```

Add `import os` at the top of `tools/video_crawler/sniffer.py`.

- [ ] **Step 5: 修改 `sniff` 使用新方法**

Replace:

```python
browser = p.chromium.launch(headless=True, args=["--mute-audio"])
context = browser.new_context(extra_http_headers=self.headers)
page = context.new_page()
```

with:

```python
browser, context = self._launch_context(p)
page = context.pages[0] if context.pages else context.new_page()
```

Replace final close:

```python
browser.close()
```

with:

```python
context.close()
if browser is not None:
    browser.close()
```

- [ ] **Step 6: 可视化模式日志**

Before `page.goto`:

```python
if self.options.visible:
    self.log(
        "[*] 已启用可视化嗅探；如页面需要人工验证，请在弹出的浏览器中完成后点击播放。"
    )
```

- [ ] **Step 7: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access tests.test_video_crawler_diagnostics -v
```

Expected:

```text
OK
```

### Task B3: UI 增加轻量开关并传递到爬虫

- [ ] **Step 1: 写 UI 快照测试**

Append to `tests/test_video_downloader.py` in `VideoDownloaderToolTests`:

```python
    def test_added_task_snapshots_sniffer_options(self):
        self.tool.url_entry.setText("https://example.invalid/watch")
        self.tool.name_entry.setText("example")
        self.tool.path_entry.setText("downloads")
        self.tool.visible_sniff_chk.setChecked(True)
        self.tool.persistent_profile_chk.setChecked(True)
        self.tool.sniff_wait_spin.setValue(25)

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertFalse(task["sniffer_headless"])
        self.assertTrue(task["sniffer_use_persistent_profile"])
        self.assertEqual(task["sniffer_manual_wait_seconds"], 25)
        self.tool.task_queue.task_done()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader.VideoDownloaderToolTests.test_added_task_snapshots_sniffer_options -v
```

Expected:

```text
AttributeError: 'VideoDownloaderTool' object has no attribute 'visible_sniff_chk'
```

- [ ] **Step 3: 添加 UI 控件**

Modify `tools/video_downloader.py` imports:

```python
from tools.video_crawler.models import SnifferOptions
```

In `VideoDownloaderTool.__init__`, after `self.live_seconds_spin` setup in `row4`, add:

```python
self.visible_sniff_chk = QCheckBox("可视化嗅探")
self.visible_sniff_chk.setChecked(False)
row4.addWidget(self.visible_sniff_chk)

self.persistent_profile_chk = QCheckBox("复用浏览器会话")
self.persistent_profile_chk.setChecked(False)
row4.addWidget(self.persistent_profile_chk)

self.sniff_wait_spin = QSpinBox()
self.sniff_wait_spin.setRange(5, 180)
self.sniff_wait_spin.setValue(10)
self.sniff_wait_spin.setSuffix(" 秒等待")
self.sniff_wait_spin.setFixedWidth(120)
row4.addWidget(self.sniff_wait_spin)
```

This keeps the controls in the existing compact options row and follows the current checkbox/spinbox pattern.

- [ ] **Step 4: 任务字段写入队列**

Modify `add_to_queue` task dict:

```python
"sniffer_headless": not self.visible_sniff_chk.isChecked(),
"sniffer_use_persistent_profile": self.persistent_profile_chk.isChecked(),
"sniffer_manual_wait_seconds": self.sniff_wait_spin.value(),
```

- [ ] **Step 5: 传给 spider**

Modify `_execute_task`:

```python
sniffer_options = SnifferOptions(
    headless=task.get("sniffer_headless", True),
    use_persistent_profile=task.get("sniffer_use_persistent_profile", False),
    manual_wait_seconds=task.get("sniffer_manual_wait_seconds", 10),
)
spider = self.spider_factory(
    output_dir=task["save_dir"],
    temp_dir="./temp",
    log_callback=self.log_signal.emit,
    is_high_speed=task["is_high_speed"],
    segment_concurrency=task["segment_concurrency"],
    resume_enabled=task.get("resume_enabled", True),
    live_record_seconds=task.get("live_record_seconds", 300),
    sniffer_options=sniffer_options,
)
```

- [ ] **Step 6: 更新 `UniversalVideoSpider.__init__`**

Modify `tools/video_crawler/spider.py`:

```python
from tools.video_crawler.models import MediaCandidate, MediaKind, SnifferOptions
```

Add constructor parameter:

```python
sniffer_options: SnifferOptions | None = None,
```

Store it:

```python
self.sniffer_options = sniffer_options or SnifferOptions()
```

Modify `_sniff_real_url`:

```python
report = PageSniffer(
    headers=self.headers,
    log_callback=self.log,
    options=self.sniffer_options,
).sniff(page_url)
```

- [ ] **Step 7: 更新 worker 参数测试**

Modify `tests/test_video_downloader.py::test_worker_passes_task_concurrency_to_spider` task:

```python
"sniffer_headless": False,
"sniffer_use_persistent_profile": True,
"sniffer_manual_wait_seconds": 33,
```

Add assertions:

```python
options = RecordingSpider.init_kwargs["sniffer_options"]
self.assertFalse(options.headless)
self.assertTrue(options.use_persistent_profile)
self.assertEqual(options.manual_wait_seconds, 33)
```

- [ ] **Step 8: 运行测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader -v
```

Expected:

```text
OK
```

---

## Phase C: 增强 JSON / 脚本 / 页面文本中的媒体地址发现

**目标：** 当站点不直接以 `.m3u8` 响应出现，而是在 XHR、JSON、脚本或 HTML 中返回播放地址时，嗅探器能提取候选 URL。

**Files:**

- Modify: `tools/video_crawler/sniffer.py`
- Modify: `tests/test_video_crawler_diagnostics.py`
- Create or modify: `tests/test_video_crawler_sniffer_access.py`

### Task C1: 写媒体 URL 提取纯函数

- [ ] **Step 1: 写失败测试**

Append to `tests/test_video_crawler_diagnostics.py`:

```python
from tools.video_crawler.sniffer import extract_media_urls_from_text


class MediaUrlExtractionTests(unittest.TestCase):
    def test_extracts_absolute_media_urls_from_json_text(self):
        text = '{"url":"https://cdn.example.test/path/master.m3u8?token=abc"}'

        urls = extract_media_urls_from_text("https://site.example/watch", text)

        self.assertEqual(urls, ["https://cdn.example.test/path/master.m3u8?token=abc"])

    def test_extracts_relative_media_urls(self):
        text = 'window.source = "/video/stream.m3u8?ep=16";'

        urls = extract_media_urls_from_text("https://site.example/watch/page", text)

        self.assertEqual(urls, ["https://site.example/video/stream.m3u8?ep=16"])

    def test_deduplicates_urls_preserving_order(self):
        text = "https://cdn/a.m3u8 https://cdn/a.m3u8 https://cdn/b.mp4"

        urls = extract_media_urls_from_text("https://site.example", text)

        self.assertEqual(urls, ["https://cdn/a.m3u8", "https://cdn/b.mp4"])
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_diagnostics.MediaUrlExtractionTests -v
```

Expected:

```text
ImportError: cannot import name 'extract_media_urls_from_text'
```

- [ ] **Step 3: 实现提取函数**

Modify `tools/video_crawler/sniffer.py` imports:

```python
import re
from urllib.parse import urljoin, urlparse
```

Add:

```python
MEDIA_URL_PATTERN = re.compile(
    r"""(?P<url>(?:https?:)?//[^'"<>\s]+?\.(?:m3u8|mp4|mpd)(?:\?[^'"<>\s]*)?|/[^'"<>\s]+?\.(?:m3u8|mp4|mpd)(?:\?[^'"<>\s]*)?)""",
    re.IGNORECASE,
)


def extract_media_urls_from_text(base_url: str, text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in MEDIA_URL_PATTERN.finditer(text or ""):
        raw_url = match.group("url")
        absolute_url = urljoin(base_url, raw_url)
        if absolute_url not in seen:
            seen.add(absolute_url)
            urls.append(absolute_url)
    return urls
```

- [ ] **Step 4: 测试通过**

Run:

```powershell
python -m unittest tests.test_video_crawler_diagnostics.MediaUrlExtractionTests -v
```

Expected:

```text
OK
```

### Task C2: 从 XHR/Fetch/JSON 响应体提取候选

- [ ] **Step 1: 写响应体处理测试**

Append to `tests/test_video_crawler_sniffer_access.py`:

```python
from tools.video_crawler.models import MediaKind
from tools.video_crawler.sniffer import candidates_from_response_text


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
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.ResponseTextCandidateTests -v
```

Expected:

```text
ImportError: cannot import name 'candidates_from_response_text'
```

- [ ] **Step 3: 实现纯函数**

Add to `tools/video_crawler/sniffer.py`:

```python
TEXT_RESPONSE_TYPES = ("json", "javascript", "text/", "html")


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
```

- [ ] **Step 4: 在 `handle_response` 中读取小型文本响应**

Inside `PageSniffer.sniff.handle_response`, after direct `classify_media_response` and before returning:

```python
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
    return
```

Keep `response.text()` inside `try` because Playwright cannot always read bodies.

- [ ] **Step 5: 日志记录候选来源**

When adding a candidate from response body:

```python
self.log(f"[*] 响应正文中发现媒体候选: {body_candidate.url[:60]}...")
```

- [ ] **Step 6: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_diagnostics tests.test_video_crawler_sniffer_access -v
```

Expected:

```text
OK
```

---

## Phase D: 条件等待和人工播放窗口

**目标：** 可视化模式下不要只固定等待 3 秒；给用户时间完成允许的人工操作，并在捕获到候选流后尽快继续。

**Files:**

- Modify: `tools/video_crawler/sniffer.py`
- Modify: `tests/test_video_crawler_sniffer_access.py`

### Task D1: 增加候选等待 helper

- [ ] **Step 1: 写纯函数测试**

Append to `tests/test_video_crawler_sniffer_access.py`:

```python
from tools.video_crawler.sniffer import should_continue_waiting_for_media


class MediaWaitPolicyTests(unittest.TestCase):
    def test_stops_waiting_when_candidate_exists(self):
        self.assertFalse(
            should_continue_waiting_for_media(candidate_count=1, elapsed_seconds=1, limit_seconds=30)
        )

    def test_stops_waiting_after_limit(self):
        self.assertFalse(
            should_continue_waiting_for_media(candidate_count=0, elapsed_seconds=30, limit_seconds=30)
        )

    def test_continues_waiting_before_limit_without_candidate(self):
        self.assertTrue(
            should_continue_waiting_for_media(candidate_count=0, elapsed_seconds=5, limit_seconds=30)
        )
```

- [ ] **Step 2: 实现 helper**

Add to `tools/video_crawler/sniffer.py`:

```python
def should_continue_waiting_for_media(
    candidate_count: int,
    elapsed_seconds: float,
    limit_seconds: int,
) -> bool:
    return candidate_count <= 0 and elapsed_seconds < limit_seconds
```

- [ ] **Step 3: 在 `sniff` 中使用条件等待**

Replace fixed wait after click:

```python
page.wait_for_timeout(3000)
```

with:

```python
waited = 0.0
while should_continue_waiting_for_media(
    candidate_count=len(candidates),
    elapsed_seconds=waited,
    limit_seconds=self.options.manual_wait_seconds,
):
    page.wait_for_timeout(1000)
    waited += 1.0
```

Keep this after both video click and center click attempts.

- [ ] **Step 4: 可视化模式提示**

Before the wait loop:

```python
if self.options.visible:
    self.log(
        f"[*] 可视化嗅探等待 {self.options.manual_wait_seconds} 秒；"
        "请在浏览器中完成允许的人工操作并点击播放。"
    )
```

- [ ] **Step 5: 运行测试**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access -v
```

Expected:

```text
OK
```

---

## Phase E: 诊断按钮和下载任务共用增强嗅探

**目标：** 用户点“诊断链接”和正式下载时都使用同一套可视化/持久化选项；诊断报告先告诉用户页面是否访问受限，再决定是否下载。

**Files:**

- Modify: `tools/video_downloader.py`
- Modify: `tests/test_video_downloader.py`

### Task E1: 诊断任务读取 UI 嗅探选项

- [ ] **Step 1: 写失败测试**

Append to `tests/test_video_downloader.py`:

```python
    def test_diagnose_task_uses_ui_sniffer_options(self):
        self.tool.visible_sniff_chk.setChecked(True)
        self.tool.persistent_profile_chk.setChecked(True)
        self.tool.sniff_wait_spin.setValue(22)

        with patch("tools.video_downloader.PageSniffer") as sniffer_class:
            sniffer_class.return_value.sniff.return_value = DiagnosticReport(
                source_url="https://example.test/watch"
            )
            self.tool._diagnose_task("https://example.test/watch")

        options = sniffer_class.call_args.kwargs["options"]
        self.assertFalse(options.headless)
        self.assertTrue(options.use_persistent_profile)
        self.assertEqual(options.manual_wait_seconds, 22)
```

- [ ] **Step 2: 抽出 UI option builder**

Modify `tools/video_downloader.py`:

```python
    def _build_sniffer_options(self):
        return SnifferOptions(
            headless=not self.visible_sniff_chk.isChecked(),
            use_persistent_profile=self.persistent_profile_chk.isChecked(),
            manual_wait_seconds=self.sniff_wait_spin.value(),
        )
```

- [ ] **Step 3: 诊断任务使用 builder**

Modify `_diagnose_task`:

```python
service = VideoDiagnosticService(
    sniffer=PageSniffer(
        headers={},
        log_callback=self.log_signal.emit,
        options=self._build_sniffer_options(),
    )
)
```

- [ ] **Step 4: 添加用户提示文案**

When `visible_sniff_chk` or `persistent_profile_chk` is checked, log:

```python
if not self._build_sniffer_options().headless:
    self.log_signal.emit("[*] 诊断将打开可视化浏览器窗口。")
if self._build_sniffer_options().use_persistent_profile:
    self.log_signal.emit("[*] 诊断将复用 browser_profiles/video_crawler 会话。")
```

- [ ] **Step 5: 运行测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader.VideoDownloaderToolTests.test_diagnose_task_uses_ui_sniffer_options -v
```

Expected:

```text
OK
```

### Task E2: 批次结果对 `HTTP_FORBIDDEN` 给出更准确建议

- [ ] **Step 1: 写失败测试**

Append to `tests/test_video_downloader.py`:

```python
    def test_batch_summary_suggests_visible_sniffing_for_http_forbidden(self):
        summary, details = self.tool.format_batch_results([
            {
                "task": {"name": "blocked"},
                "success": False,
                "output_path": "",
                "error": "页面访问受限",
                "error_code": VideoErrorCode.HTTP_FORBIDDEN.value,
                "retryable": False,
            }
        ])

        self.assertIn("失败 1 个", summary)
        self.assertIn("HTTP_FORBIDDEN", details)
        self.assertIn("可视化嗅探", details)
        self.assertIn("复用浏览器会话", details)
```

- [ ] **Step 2: 修改失败建议**

In `VideoDownloaderTool.format_batch_results`, after error statistics:

```python
if error_counts.get(VideoErrorCode.HTTP_FORBIDDEN.value):
    detail_lines.append(
        "  建议: 页面访问受限。请确认普通浏览器可播放；"
        "若可播放，尝试启用“可视化嗅探”和“复用浏览器会话”，"
        "在弹出的浏览器中完成允许的人工操作后再点击播放。"
    )
elif not VideoDownloaderTool.has_retryable_failures(results):
    detail_lines.append(
        "  建议: 这些失败不建议直接重试，请先检查链接、权限或资源类型。"
    )
```

- [ ] **Step 3: 运行测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader.VideoDownloaderToolTests.test_batch_summary_suggests_visible_sniffing_for_http_forbidden tests.test_video_downloader.VideoDownloaderToolTests.test_batch_summary_groups_errors_by_code -v
```

Expected:

```text
OK
```

---

## Phase F: 手工验证目标站点

**目标：** 验证新能力是否能让用户已授权可播放页面进入播放器并捕获真实媒体流。此阶段包含人工操作，不作为自动化测试的唯一验收依据。

**Files:**

- Modify: none required
- Manual verification target: `https://www.aowu.tv/w/BNCxTD01jh6N#s=5249&ep=16`

### Task F1: 基线诊断

- [ ] **Step 1: 运行不开可视化的诊断**

In UI:

```text
目标网址: https://www.aowu.tv/w/BNCxTD01jh6N#s=5249&ep=16
可视化嗅探: 关闭
复用浏览器会话: 关闭
点击: 诊断链接
```

Expected:

```text
日志显示页面诊断状态码=403，标题=403 - 访问受限
诊断失败或下载失败错误码为 HTTP_FORBIDDEN
```

- [ ] **Step 2: 运行可视化持久化诊断**

In UI:

```text
可视化嗅探: 开启
复用浏览器会话: 开启
等待时间: 60 秒
点击: 诊断链接
```

When the browser opens:

```text
1. 确认页面不是 VPN 环境。
2. 如站点要求人工操作，在浏览器中完成允许的操作。
3. 点击页面播放器播放按钮。
4. 等待日志出现 M3U8/MP4/MPD 候选流。
```

Expected if site permits the session:

```text
诊断报告出现 HLS、DIRECT_MP4 或 DASH 候选流。
日志显示 “嗅探到 M3U8 候选流” 或 “响应正文中发现媒体候选”。
```

Expected if site still blocks automation:

```text
仍返回 HTTP_FORBIDDEN，提示页面访问受限。
这种情况不继续尝试绕过；记录为站点策略限制。
```

### Task F2: 下载验证

- [ ] **Step 1: 使用同一选项下载**

In UI:

```text
可视化嗅探: 开启
复用浏览器会话: 开启
等待时间: 60 秒
保存名称: my_video_01
点击: 添加到下载队列
```

Expected:

```text
如果诊断阶段已经能抓到 HLS/MP4/MPD，下载任务进入现有适配器。
任务成功时输出 mp4 文件。
任务失败时保持结构化错误码，不再误报 NO_MEDIA_FOUND。
```

---

## Phase G: 全量回归和清理

**目标：** 确认新增会话嗅探能力不破坏现有 HLS/DASH/yt-dlp/队列行为，并清理阶段临时文件。

**Files:**

- Modify: none required

### Task G1: 自动化回归

- [ ] **Step 1: 运行完整测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 2: 检查新增目录不会被误清理**

Run:

```powershell
Test-Path -LiteralPath "browser_profiles\video_crawler"
```

Expected:

```text
True
```

Only if a profile was created during manual verification. If no manual verification ran, `False` is acceptable.

- [ ] **Step 3: 清理无用临时文件**

Use a PowerShell cleanup command that only targets:

```text
tests/.tmp/tmp*
tests/tmp*
tests/__pycache__
tools/video_crawler/**/__pycache__
```

Before deletion, verify each resolved absolute path starts with:

```text
D:\Study\Projects\PythonProject\FireflyTools\
```

Do not delete:

```text
browser_profiles/
downloads/
temp/ 中未完成下载任务目录
plan/
tests/*.py
tools/*.py
```

- [ ] **Step 4: 记录验收结果**

Append a short note to this plan after implementation:

```markdown
## Implementation Notes

- Full tests: `Ran <N> tests ... OK`
- Manual target URL result: `<HTTP_FORBIDDEN / candidate captured / download succeeded>`
- Browser profile used: `<yes/no>`
- Cleanup result: `<removed count, remaining count>`
```

---

## 最终验收矩阵

| 验收项 | 完成信号 |
|---|---|
| 访问受限诊断 | 主页面 403 或标题“访问受限”返回 `HTTP_FORBIDDEN`，日志含状态码、标题、video/iframe 数 |
| 可视化嗅探 | UI 可开启非 headless 浏览器，日志提示用户可人工完成允许的操作 |
| 持久化会话 | UI 可启用 `browser_profiles/video_crawler`，Cookie/LocalStorage 可跨任务复用 |
| 媒体地址增强发现 | JSON、脚本、HTML 中的 `.m3u8/.mp4/.mpd` URL 可转为候选流 |
| 条件等待 | 可视化模式按设置等待候选流，捕获到候选后提前继续 |
| 错误建议 | `HTTP_FORBIDDEN` 弹窗建议可视化嗅探和复用浏览器会话，不再建议盲目重试 |
| 下载兼容 | 既有 MP4/HLS/DASH/yt-dlp 测试全部通过 |
| 清理要求 | 无用测试临时目录和 `tools/video_crawler/**/__pycache__` 已清理，不删除浏览器 profile |

---

## 执行顺序建议

1. Phase A 先改错误诊断；这一步即使目标站点仍不能爬，也能让用户看到准确失败原因。
2. Phase B 加可视化与持久化会话；这是提高该站点成功率的关键。
3. Phase C 加响应正文媒体提取；解决“真实地址藏在 JSON/API/脚本里”的情况。
4. Phase D 加条件等待；让人工播放窗口不被 3 秒固定等待限制。
5. Phase E 同步 UI、诊断和弹窗建议。
6. Phase F 用目标 URL 做人工验证。
7. Phase G 全量测试和清理。

---

## 风险和回退

- 如果可视化持久化会话仍然得到 403，说明站点策略继续阻止 Playwright 环境；不要继续做自动化绕过，保留 `HTTP_FORBIDDEN` 诊断即可。
- 如果浏览器 profile 被污染，关闭程序后手动删除 `browser_profiles/video_crawler` 可重置会话。
- 如果新增 UI 控件导致主界面拥挤，保持功能不变，改为折叠到“高级嗅探选项”行；控件仍使用现有 QCheckBox/QSpinBox 风格。
- 如果 JSON 提取误判广告或预览流，继续使用 `_select_best_m3u8` 的切片数量探测筛选 HLS；必要时给 `source="response-body"` 的候选较低分。
