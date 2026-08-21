# Video Crawler Navigation Timeout Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让无头、非持久会话的网页嗅探在 `page.goto()` 超时或早期页面诊断异常后仍继续观察媒体请求，并把不完整嗅探准确分类为可重试网络错误，避免漏掉延迟 HLS 或误选响应正文中的短 MP4。

**Architecture:** 保留 `domcontentloaded` 导航和 HLS 优先策略，但把“导航”“访问诊断”“播放器触发”“媒体观察”拆成彼此独立的阶段；导航异常只标记报告不完整，不能跳过后续观察。`DiagnosticReport` 提供明确的 `navigation_incomplete` 状态，Spider 根据该状态约束弱候选并产生结构化错误；响应正文提取在调用 `response.text()` 前按类型和已知大小限流。

**Tech Stack:** Python 3.10+、Playwright Sync API、PyQt6、requests、unittest、unittest.mock

---

## 已确认的故障链路

参考实现 `D:\Study\Projects\PythonProject\video_fetcher\video_fetcher.py` 在注册 `response` 监听后使用 `networkidle` 导航。即使 25 秒导航最终超时，监听器仍在等待期间收集 M3U8，因此目标页面可以成功捕获 553 片的 HLS。

当前 FireflyTools 在 `tools/video_crawler/sniffer.py` 中只有在 `page.goto(page_url, wait_until="domcontentloaded", timeout=25000)` 和页面诊断全部成功后才点击播放器并进入显式观察循环。导航或早期诊断异常被写入 `DiagnosticReport.warnings` 后直接进入清理；`tools/video_crawler/spider.py` 没有消费 warnings，候选为空时统一产生不可重试的 `NO_MEDIA_FOUND`。

现场只嗅探验证还出现过相同配置下仅发现 259,747 字节 response-body MP4、没有 HLS 的情况，说明必须同时修复导航时序和不完整嗅探下的弱候选降级。

## 范围与非目标

本计划修改：

- `tools/video_crawler/models.py`
- `tools/video_crawler/sniffer.py`
- `tools/video_crawler/spider.py`
- `tools/video_downloader.py`
- `tests/test_video_crawler_models.py`
- `tests/test_video_crawler_sniffer_access.py`
- `tests/test_video_downloader.py`

本计划不修改：

- HLS 切片下载、AES 解密、断点恢复和 FFmpeg 合并逻辑。
- 持久化浏览器 profile 的目录和 Cookie 继承规则。
- DRM、验证码、付费墙或访问控制边界。
- 现有候选类型优先级：已验证 HLS、已验证 MP4、DASH。
- 不恢复“最后一个未验证 M3U8 直接下载”的旧式 fallback。

## 工作区约束

- 开始前执行：

```powershell
git status --short --branch -uall
git log --oneline -8
```

- 当前工作区存在用户文档和中文注释相关改动；不得执行 `git reset --hard`、`git checkout -- <file>` 或等价还原。
- 修改任何目标文件前先执行 `git diff -- <path>`，在用户现有改动之上编辑。
- 不清理 `tests/tmpbs_9xr3j/`、`tests/tmpn0jwtglu/` 或来源不明的未跟踪文件。
- 每个提交步骤只有在用户明确要求提交时执行；暂存必须列出准确路径，不能使用 `git add .`。

---

### Task 1: 为诊断报告增加导航完整性状态

**Files:**

- Modify: `tools/video_crawler/models.py:86-119`
- Modify: `tests/test_video_crawler_models.py`
- Modify: `tests/test_video_crawler_sniffer_access.py:91-98`

- [ ] **Step 1: 写入导航状态和默认观察预算的失败测试**

在 `tests/test_video_crawler_models.py` 的诊断报告测试区域加入：

```python
def test_diagnostic_report_navigation_is_complete_by_default(self):
    report = DiagnosticReport(source_url="https://site.example/watch")

    self.assertFalse(report.navigation_incomplete)


def test_diagnostic_report_can_mark_navigation_incomplete(self):
    report = DiagnosticReport(
        source_url="https://site.example/watch",
        navigation_incomplete=True,
        warnings=["页面导航超时"],
    )

    self.assertTrue(report.navigation_incomplete)
    self.assertEqual(report.warnings, ["页面导航超时"])
```

同时把 `tests/test_video_crawler_sniffer_access.py::SnifferOptionsTests.test_default_options_are_headless_and_non_persistent` 中的等待断言改为：

```python
self.assertEqual(options.manual_wait_seconds, 25)
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_models tests.test_video_crawler_sniffer_access.SnifferOptionsTests -v
```

Expected: `navigation_incomplete` 构造参数测试报错，默认等待仍为 10 秒的断言失败。

- [ ] **Step 3: 最小扩展模型**

在 `SnifferOptions` 中把默认观察预算改为 25 秒，并在 `DiagnosticReport` 的 `warnings` 前加入明确状态：

```python
@dataclass(frozen=True)
class SnifferOptions:
    """配置 Playwright 的可视化、持久会话和等待行为。"""

    headless: bool = True
    use_persistent_profile: bool = False
    profile_dir: str = "./browser_profiles/video_crawler"
    manual_wait_seconds: int = 25

    @property
    def visible(self) -> bool:
        """返回嗅探器是否应显示浏览器窗口。"""
        return not self.headless
```

`DiagnosticReport` 保持现有字段顺序兼容关键字构造，并加入：

```python
@dataclass(frozen=True)
class DiagnosticReport:
    """汇总候选媒体、浏览器会话、警告和错误。"""

    source_url: str
    candidates: list[MediaCandidate] = field(default_factory=list)
    session: BrowserSessionSnapshot = field(default_factory=BrowserSessionSnapshot)
    navigation_incomplete: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
```

保留该类现有的 `best_candidate` 和 `has_downloadable_candidate` 属性，不改其实现。

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_models tests.test_video_crawler_sniffer_access.SnifferOptionsTests -v
```

Expected: 命令退出码为 0，所有列出的测试显示 `ok`，结尾为 `OK`。

- [ ] **Step 5: 检查并按需提交**

```powershell
git diff --check -- tools/video_crawler/models.py tests/test_video_crawler_models.py tests/test_video_crawler_sniffer_access.py
git diff -- tools/video_crawler/models.py tests/test_video_crawler_models.py tests/test_video_crawler_sniffer_access.py
git add -- tools/video_crawler/models.py tests/test_video_crawler_models.py tests/test_video_crawler_sniffer_access.py
git commit -m "test: define incomplete sniffer navigation state"
```

Expected: diff 只包含模型字段、默认值和对应测试；没有删除现有中文注释。

---

### Task 2: 导航超时后仍触发播放器并执行一次完整观察

**Files:**

- Modify: `tools/video_crawler/sniffer.py:7-16`
- Modify: `tools/video_crawler/sniffer.py:289-476`
- Modify: `tests/test_video_crawler_sniffer_access.py:50-89`
- Modify: `tests/test_video_crawler_sniffer_access.py:233-417`

- [ ] **Step 1: 写导航超时后捕获延迟 HLS 的失败测试**

把以下 fake 和测试类追加到 `SequencedMediaPage` 定义之后、`if __name__ == "__main__"` 之前：

```python
class NavigationTimeoutMediaPage(SequencedMediaPage):
    def goto(self, page_url, **kwargs):
        raise TimeoutError("domcontentloaded timeout")


class PageSnifferNavigationRecoveryTests(unittest.TestCase):
    def test_navigation_timeout_still_waits_and_captures_delayed_hls(self):
        page = NavigationTimeoutMediaPage()
        logs = []
        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            report = PageSniffer(
                log_callback=logs.append,
                options=SnifferOptions(manual_wait_seconds=10),
            ).sniff("https://site.example/watch")

        hls_candidates = [
            candidate
            for candidate in report.candidates
            if candidate.kind == MediaKind.HLS
        ]
        self.assertTrue(report.navigation_incomplete)
        self.assertEqual(len(hls_candidates), 1)
        self.assertEqual(hls_candidates[0].source, "network")
        self.assertEqual(page.wait_calls, 2)
        self.assertTrue(any("继续观察媒体请求" in message for message in logs))
```

`NavigationTimeoutMediaPage` 继承的 `wait_for_timeout()` 会在第二次轮询发出 network HLS，从而证明导航异常后没有跳过显式观察。

- [ ] **Step 2: 写 iframe 播放器触发和单次等待的失败测试**

先在 `tools.video_crawler.sniffer` 导入列表中加入计划新增的 `trigger_playback`，再在测试文件加入：

```python
class RecordingVideoLocator(FakeLocator):
    def __init__(self, count):
        self._count = count
        self.clicked = False

    def count(self):
        return self._count

    def click(self, **kwargs):
        self.clicked = True


class RecordingFrame:
    def __init__(self, video_count):
        self.video_locator = RecordingVideoLocator(video_count)

    def locator(self, selector):
        self.last_selector = selector
        return self.video_locator


class IframePlaybackPage:
    viewport_size = {"width": 1280, "height": 720}

    def __init__(self):
        self.top_frame = RecordingFrame(0)
        self.player_frame = RecordingFrame(1)
        self.frames = [self.top_frame, self.player_frame]
        self.mouse = FakeMouse()


class PlaybackTriggerTests(unittest.TestCase):
    def test_trigger_playback_clicks_video_inside_iframe(self):
        page = IframePlaybackPage()

        trigger = trigger_playback(page)

        self.assertEqual(trigger, "frame-video")
        self.assertTrue(page.player_frame.video_locator.clicked)
```

- [ ] **Step 3: 运行新测试确认 RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_sniffer_access.PageSnifferNavigationRecoveryTests tests.test_video_crawler_sniffer_access.PlaybackTriggerTests -v
```

Expected: 第一项因导航异常后没有等待或报告缺少状态而失败；第二项因 `trigger_playback` 尚不存在而导入失败。

- [ ] **Step 4: 增加可独立测试的播放器触发 helper**

在 `detect_access_limited_page()` 后、`PageSniffer` 前加入：

```python
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
            continue

    try:
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        page.mouse.click(viewport["width"] / 2, viewport["height"] / 2)
        return "viewport-center"
    except Exception:
        return "none"
```

- [ ] **Step 5: 把导航异常与观察阶段解耦**

在导入区加入：

```python
from tools.video_crawler.logging_utils import redact_for_display
```

在 `PageSniffer.sniff()` 中保留 `page.on("response", handle_response)` 的注册位置，把当前覆盖导航、诊断、点击和等待的单个大 `try` 替换为以下分段控制流。最外层 `try/finally` 必须完整保留会话提取和浏览器关闭，导航和页面诊断使用内部 `try/except`：

```python
navigation_incomplete = False
main_response = None

page.on("response", handle_response)
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

    try:
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
    except VideoDownloadError:
        raise
    except Exception as exc:
        navigation_incomplete = True
        safe_error = redact_for_display(str(exc))
        warning = f"页面诊断异常: {safe_error}"
        warnings.append(warning)
        self.log(f"[!] {warning}；继续观察媒体请求。")

    self.log("[*] 正在尝试触发播放器以产生真实数据流...")
    trigger_result = trigger_playback(page)
    self.log(f"[*] 播放器触发方式: {trigger_result}")
    wait_for_candidates()
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
```

删除旧的嵌套点击 `try/except`，确保 `wait_for_candidates()` 每次嗅探只调用一次，不因 video 点击异常重复计算观察预算。会话提取必须继续发生在 context/page 关闭之前。

返回报告时显式传递新状态：

```python
return DiagnosticReport(
    source_url=page_url,
    candidates=deduplicate_media_candidates(candidates),
    session=session,
    navigation_incomplete=navigation_incomplete,
    warnings=warnings,
)
```

- [ ] **Step 6: 运行导航恢复和既有等待测试确认 GREEN**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_sniffer_access.PageSnifferNavigationRecoveryTests tests.test_video_crawler_sniffer_access.PlaybackTriggerTests tests.test_video_crawler_sniffer_access.PageSnifferAccessFlowTests tests.test_video_crawler_sniffer_access.MediaWaitPolicyTests -v
```

Expected: 命令退出码为 0；导航超时用例捕获 network HLS，iframe 用例点击子 frame 中的 video。

- [ ] **Step 7: 检查并按需提交**

```powershell
git diff --check -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
git diff -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
git add -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
git commit -m "fix: continue media sniffing after navigation timeout"
```

---

### Task 3: 使用真实时间预算并限制响应正文读取

**Files:**

- Modify: `tools/video_crawler/sniffer.py:3-5`
- Modify: `tools/video_crawler/sniffer.py:19-32`
- Modify: `tools/video_crawler/sniffer.py:319-395`
- Modify: `tests/test_video_crawler_sniffer_access.py`

- [ ] **Step 1: 写响应正文读取策略的失败测试**

在导入列表加入 `should_read_response_text`，并加入：

```python
class ResponseBodyReadPolicyTests(unittest.TestCase):
    def test_reads_small_json_response(self):
        self.assertTrue(
            should_read_response_text("application/json", "4096")
        )

    def test_skips_non_text_response_before_calling_response_text(self):
        self.assertFalse(
            should_read_response_text("application/octet-stream", "4096")
        )

    def test_skips_known_response_larger_than_limit(self):
        self.assertFalse(
            should_read_response_text("text/javascript", "1000001")
        )

    def test_allows_text_response_with_unknown_size(self):
        self.assertTrue(
            should_read_response_text("text/html", "")
        )
```

- [ ] **Step 2: 写真实时间截止线的失败测试**

把 `time.monotonic` 作为 `tools.video_crawler.sniffer.time.monotonic` 打补丁。使用已有 `SequencedMediaPage`，加入：

```python
class MediaObservationDeadlineTests(unittest.TestCase):
    def test_observation_uses_monotonic_deadline(self):
        page = SequencedMediaPage()
        monotonic_values = iter([100.0, 100.0, 100.4, 101.1])

        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=FakePlaywrightManager(FakePlaywright(page)),
        ):
            with patch(
                "tools.video_crawler.sniffer.time.monotonic",
                side_effect=lambda: next(monotonic_values),
            ):
                report = PageSniffer(
                    options=SnifferOptions(manual_wait_seconds=1)
                ).sniff("https://site.example/watch")

        self.assertLessEqual(page.wait_calls, 2)
        self.assertIsInstance(report.candidates, list)
```

该 fake 对应预期调用顺序：第一次建立 deadline，第二次计算首轮剩余时间，第三次计算第二轮剩余时间；第四个值作为防御性余量，不应被消费。

- [ ] **Step 3: 运行新测试确认 RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_sniffer_access.ResponseBodyReadPolicyTests tests.test_video_crawler_sniffer_access.MediaObservationDeadlineTests -v
```

Expected: `should_read_response_text` 尚不存在，且当前观察循环没有调用 `time.monotonic()`。

- [ ] **Step 4: 实现正文读取前置判断**

在 `sniffer.py` 常量区加入：

```python
MAX_RESPONSE_TEXT_BYTES = 1_000_000


def should_read_response_text(
    content_type: str,
    content_length: str,
    max_bytes: int = MAX_RESPONSE_TEXT_BYTES,
) -> bool:
    """只允许已知文本类型且未明确超限的响应进入正文提取。"""
    if not any(token in (content_type or "").lower() for token in TEXT_RESPONSE_TYPES):
        return False
    try:
        known_size = int(content_length)
    except (TypeError, ValueError):
        return True
    return 0 <= known_size <= max_bytes
```

在 `handle_response()` 的 `candidate is None` 分支中，先判断类型和大小，再读取正文：

```python
if candidate is None:
    resource_type = response.request.resource_type
    content_length = response.headers.get("content-length", "")
    if (
        resource_type in {"xhr", "fetch", "document", "script"}
        and should_read_response_text(content_type, content_length)
    ):
        try:
            body_text = response.text()
        except Exception as exc:
            warning = f"响应正文读取失败: {type(exc).__name__}"
            if warning not in warnings and len(warnings) < 10:
                warnings.append(warning)
                self.log(f"[!] {warning}")
            body_text = ""
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
```

保留网络媒体候选的 Header 合并、HLS 日志和候选追加逻辑。

- [ ] **Step 5: 把观察循环改为真实时间截止线**

在文件顶部加入：

```python
import time
```

把 `wait_for_candidates()` 中基于 `waited += 1.0` 的循环替换为：

```python
deadline = time.monotonic() + self.options.manual_wait_seconds
while not has_reliable_media_candidate(candidates):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        break
    page.wait_for_timeout(max(1, min(1000, int(remaining * 1000))))
```

循环后的候选统计和结束原因日志保持不变。该实现确保同步响应回调耗时也计入预算，不会让“10 秒”等待在慢正文解析时无限延长。

- [ ] **Step 6: 运行完整嗅探测试确认 GREEN**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_sniffer_access -v
```

Expected: 全部嗅探访问测试显示 `ok`，结尾为 `OK`。

- [ ] **Step 7: 检查并按需提交**

```powershell
git diff --check -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
git diff -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
git add -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
git commit -m "perf: bound media response body inspection"
```

---

### Task 4: Spider 消费诊断状态并修正错误语义

**Files:**

- Modify: `tools/video_crawler/spider.py:522-613`
- Modify: `tests/test_video_downloader.py:318-599`

- [ ] **Step 1: 写导航不完整且无候选时的失败测试**

在 `UniversalVideoSpiderTests` 中加入：

```python
def test_incomplete_navigation_without_candidate_is_retryable_timeout(self):
    spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
    report = DiagnosticReport(
        source_url="https://site.example/watch",
        navigation_incomplete=True,
        warnings=["页面加载异常或超时: domcontentloaded timeout"],
    )

    with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
        sniffer_class.return_value.sniff.return_value = report
        with self.assertRaises(VideoDownloadError) as raised:
            spider._sniff_real_url("https://site.example/watch")

    self.assertEqual(raised.exception.code, VideoErrorCode.NETWORK_TIMEOUT)
    self.assertTrue(raised.exception.retryable)
    self.assertIn("导航未完整结束", str(raised.exception))
```

- [ ] **Step 2: 写导航不完整时拒绝弱正文 MP4 的失败测试**

```python
def test_incomplete_navigation_does_not_select_response_body_only_mp4(self):
    spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
    report = DiagnosticReport(
        source_url="https://site.example/watch",
        candidates=[
            MediaCandidate(
                url="https://cdn.example.test/preview.mp4",
                kind=MediaKind.DIRECT_MP4,
                source="response-body",
                score=70,
            )
        ],
        navigation_incomplete=True,
        warnings=["页面加载异常或超时"],
    )

    with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
        sniffer_class.return_value.sniff.return_value = report
        with patch.object(spider, "_probe_mp4_size") as probe:
            with self.assertRaises(VideoDownloadError) as raised:
                spider._sniff_real_url("https://site.example/watch")

    self.assertEqual(raised.exception.code, VideoErrorCode.NETWORK_TIMEOUT)
    probe.assert_not_called()
```

- [ ] **Step 3: 写导航不完整但已捕获并验证 HLS 的保留测试**

```python
def test_incomplete_navigation_can_use_verified_hls(self):
    spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
    hls_url = "https://cdn.example.test/main.m3u8"
    report = DiagnosticReport(
        source_url="https://site.example/watch",
        candidates=[
            MediaCandidate(
                url=hls_url,
                kind=MediaKind.HLS,
                source="network",
                score=80,
            )
        ],
        navigation_incomplete=True,
        warnings=["页面加载异常或超时"],
    )

    with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
        sniffer_class.return_value.sniff.return_value = report
        with patch.object(
            spider,
            "_select_best_m3u8",
            return_value=(hls_url, 553),
        ):
            selected = spider._sniff_real_url("https://site.example/watch")

    self.assertEqual(selected, hls_url)
```

- [ ] **Step 4: 运行新测试确认 RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests.test_incomplete_navigation_without_candidate_is_retryable_timeout tests.test_video_downloader.UniversalVideoSpiderTests.test_incomplete_navigation_does_not_select_response_body_only_mp4 tests.test_video_downloader.UniversalVideoSpiderTests.test_incomplete_navigation_can_use_verified_hls -v
```

Expected: 前两项失败，因为当前实现忽略 `navigation_incomplete`；HLS 保留测试用于约束修复不能破坏已有成功路径。

- [ ] **Step 5: 记录 warnings 并过滤不完整导航中的弱候选**

在 `_sniff_real_url()` 取得 report 后立即加入：

```python
for warning in report.warnings:
    self.log(f"[!] 嗅探警告: {warning}")

self.session_snapshot = report.session
candidates = deduplicate_media_candidates(report.candidates)
```

保留现有 HLS 收集、探测和结构化失败逻辑。构造 MP4 候选时改为：

```python
mp4_candidates = [
    candidate
    for candidate in candidates
    if candidate.kind == MediaKind.DIRECT_MP4
    and (
        not report.navigation_incomplete
        or candidate.source == "network"
    )
]
```

构造 DASH 候选时使用同样的完整性约束：

```python
dash_candidates = [
    candidate
    for candidate in candidates
    if candidate.kind == MediaKind.DASH
    and (
        not report.navigation_incomplete
        or candidate.source == "network"
    )
]
```

HLS 候选仍允许 response-body 来源进入 playlist 验证，因为成功解析 `#EXTM3U` 和切片信息已经提供强证据。

- [ ] **Step 6: 在无可用候选时产生可重试网络错误**

在 `_sniff_real_url()` 的最终 `return None` 前加入：

```python
if report.navigation_incomplete:
    raise VideoDownloadError(
        VideoErrorCode.NETWORK_TIMEOUT,
        "页面导航未完整结束，且观察窗口内未捕获可验证的视频流。",
        details={
            "warnings": list(report.warnings),
            "candidate_count": len(candidates),
        },
        retryable=True,
    )
return None
```

这样完整导航后的空结果仍沿用 `_resolve_candidate()` 中的 `NO_MEDIA_FOUND/retryable=False`，只有不完整导航才转为 `NETWORK_TIMEOUT/retryable=True`。

- [ ] **Step 7: 运行 Spider 聚焦测试确认 GREEN**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests -v
```

Expected: 所有 Spider 测试显示 `ok`，结尾为 `OK`；既有 HLS 验证失败仍返回 `NETWORK_TIMEOUT` 或 `M3U8_PARSE_FAILED`。

- [ ] **Step 8: 检查并按需提交**

```powershell
git diff --check -- tools/video_crawler/spider.py tests/test_video_downloader.py
git diff -- tools/video_crawler/spider.py tests/test_video_downloader.py
git add -- tools/video_crawler/spider.py tests/test_video_downloader.py
git commit -m "fix: classify incomplete media sniffing as retryable"
```

---

### Task 5: 统一 UI、任务快照和核心默认等待为 25 秒

**Files:**

- Modify: `tools/video_downloader.py:146-161`
- Modify: `tools/video_downloader.py:313-342`
- Modify: `tests/test_video_downloader.py:866-1064`

- [ ] **Step 1: 写 UI 默认值和旧任务 fallback 的失败测试**

在 `VideoDownloaderToolTests` 中加入：

```python
def test_default_sniff_wait_is_25_seconds(self):
    self.assertEqual(self.tool.sniff_wait_spin.value(), 25)
```

在现有 worker 参数测试旁加入一个没有 `sniffer_manual_wait_seconds` 的任务：

```python
def test_worker_uses_25_second_wait_for_legacy_task_snapshot(self):
    tool = VideoDownloaderTool(
        start_worker=False,
        spider_factory=RecordingSpider,
    )
    task = {
        "url": "https://example.test/watch",
        "name": "video",
        "save_dir": "downloads",
        "is_high_speed": False,
        "segment_concurrency": 5,
        "resume_enabled": True,
        "live_record_seconds": 300,
    }

    try:
        result = tool._execute_task(task)
    finally:
        tool.close()

    self.assertTrue(result["success"])
    self.assertEqual(
        RecordingSpider.init_kwargs["sniffer_options"].manual_wait_seconds,
        25,
    )
```

该测试直接沿用文件现有的 `RecordingSpider`：其 `run()` 返回 `downloads/<name>.mp4` 字符串，因此 `_execute_task()` 会生成 `success=True` 的结果并留下可断言的 `init_kwargs`。

- [ ] **Step 2: 运行新测试确认 RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_downloader.VideoDownloaderToolTests.test_default_sniff_wait_is_25_seconds tests.test_video_downloader.VideoDownloaderToolTests.test_worker_uses_25_second_wait_for_legacy_task_snapshot -v
```

Expected: UI 默认值和缺省任务 fallback 仍为 10，两个断言失败。

- [ ] **Step 3: 更新 UI 与 worker fallback**

在 UI 初始化中改为：

```python
self.sniff_wait_spin.setRange(5, 180)
self.sniff_wait_spin.setValue(25)
self.sniff_wait_spin.setSuffix(" 秒等待")
```

在 `_execute_task()` 构造 `SnifferOptions` 时改为：

```python
sniffer_options = SnifferOptions(
    headless=task.get("sniffer_headless", True),
    use_persistent_profile=task.get(
        "sniffer_use_persistent_profile",
        False,
    ),
    manual_wait_seconds=task.get("sniffer_manual_wait_seconds", 25),
)
```

任务入队仍保存 spinner 的显式快照；用户把等待改回 10 秒时必须原样传给 Spider。

- [ ] **Step 4: 运行 UI 与任务快照测试确认 GREEN**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_downloader.VideoDownloaderToolTests -v
```

Expected: 所有 UI、队列快照和 worker 配置测试显示 `ok`，结尾为 `OK`。

- [ ] **Step 5: 检查并按需提交**

```powershell
git diff --check -- tools/video_downloader.py tests/test_video_downloader.py
git diff -- tools/video_downloader.py tests/test_video_downloader.py
git add -- tools/video_downloader.py tests/test_video_downloader.py
git commit -m "fix: increase default media sniff observation budget"
```

---

### Task 6: 聚焦回归、现场只嗅探验证和完整测试

**Files:**

- Verify: `tools/video_crawler/models.py`
- Verify: `tools/video_crawler/sniffer.py`
- Verify: `tools/video_crawler/spider.py`
- Verify: `tools/video_downloader.py`
- Verify: `tests/test_video_crawler_models.py`
- Verify: `tests/test_video_crawler_sniffer_access.py`
- Verify: `tests/test_video_downloader.py`

- [ ] **Step 1: 运行所有直接相关测试**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_models tests.test_video_crawler_diagnostics tests.test_video_crawler_sniffer_access tests.test_video_crawler_session tests.test_video_downloader -v
```

Expected: 命令退出码为 0，所有测试显示 `ok`，结尾为 `OK`。

- [ ] **Step 2: 运行完整测试套件**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected: 命令退出码为 0，结尾为 `OK`。若只出现已知 `tests/tmpbs_9xr3j/`、`tests/tmpn0jwtglu/` 枚举权限警告，单独记录，不修改业务代码或删除目录。

- [ ] **Step 3: 对 Taiav 目标执行只嗅探和 playlist 探测**

该步骤只发现候选并读取 M3U8 文本，不进入切片下载：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -c "from urllib.parse import urlparse; from tools.video_crawler.models import SnifferOptions; from tools.video_crawler.spider import UniversalVideoSpider; url='https://taiav.com/cn/movie/6a4dc2f02606b60c7a2694b4'; spider=UniversalVideoSpider(log_callback=print,sniffer_options=SnifferOptions(headless=True,use_persistent_profile=False,manual_wait_seconds=25)); selected=spider._sniff_real_url(url); probe=getattr(spider,'_last_selected_m3u8_probe',{}); print('SELECTED_PATH',urlparse(selected).path); print('SEGMENT_COUNT',probe.get('segment_count')); print('BANDWIDTH',probe.get('bandwidth'))"
```

Expected:

- 日志包含页面诊断或“页面加载异常或超时；继续观察媒体请求”。
- 日志包含网络 HLS 候选和观察结束原因。
- `SELECTED_PATH` 以 `/index.m3u8` 结尾。
- `SEGMENT_COUNT` 大于 10；现场已知目标通常约为 553 片，但不把远端动态数量写成自动测试断言。
- 不生成最终 MP4，不下载 TS/fMP4 媒体切片。

- [ ] **Step 4: 验证错误语义的人工场景**

用测试 fake 而非失效外网地址验证以下矩阵已经由自动测试覆盖：

| 导航状态 | 候选 | 结果 |
|---|---|---|
| 完整 | 无 | `NO_MEDIA_FOUND`, `retryable=False` |
| 不完整 | 无 | `NETWORK_TIMEOUT`, `retryable=True` |
| 不完整 | response-body MP4 | 不探测该弱候选，返回 `NETWORK_TIMEOUT` |
| 不完整 | network MP4 | 完整观察预算后允许进入 MP4 元数据验证 |
| 不完整 | 可验证 HLS | 选择 HLS |
| 任意 | HLS 全部探测失败 | 保留 `NETWORK_TIMEOUT` 或 `M3U8_PARSE_FAILED` |

- [ ] **Step 5: 做最终 diff、注释保护和敏感信息检查**

```powershell
git diff --check
git diff --stat
git diff -- tools/video_crawler/models.py tools/video_crawler/sniffer.py tools/video_crawler/spider.py tools/video_downloader.py tests/test_video_crawler_models.py tests/test_video_crawler_sniffer_access.py tests/test_video_downloader.py
git status --short --branch -uall
```

检查项：

- 没有还原或覆盖既有中文 docstring/行内注释。
- 没有把 Cookie、Authorization、完整带 token 的媒体 URL 写入固定测试数据或普通日志。
- 没有下载产物、浏览器 profile、临时切片或缓存进入 Git 状态。
- 计划外文件没有被修改、移动或删除。
- `NO_MEDIA_FOUND`、`NETWORK_TIMEOUT` 和 HLS 验证错误的 retryable 语义符合矩阵。

- [ ] **Step 6: 按需创建最终提交**

仅当此前任务没有分步提交且用户明确要求提交时执行：

```powershell
git add -- tools/video_crawler/models.py tools/video_crawler/sniffer.py tools/video_crawler/spider.py tools/video_downloader.py tests/test_video_crawler_models.py tests/test_video_crawler_sniffer_access.py tests/test_video_downloader.py
git commit -m "fix: make webpage media sniffing resilient to navigation timeouts"
```

不要把 `docs/project-overview.md`、`docs/项目介绍.md`、`docs/项目介绍_2026-06-30.md` 或其他既有工作区改动混入功能提交。

---

## 验收标准

- `page.goto()` 超时不能阻止后续播放器触发和媒体观察。
- 导航异常必须在 UI 日志中可见，不能只留在未消费的 warnings 中。
- 无头、非持久会话模式对目标页面可以在配置预算内捕获并验证延迟 HLS。
- 不完整导航时，response-body-only MP4 不能成为最终 fallback。
- 网络 HLS 仍可提前结束观察；没有网络 HLS 时按真实时间等待到预算结束。
- 默认等待为 25 秒，UI 显式设置的其他值仍按任务快照传递。
- 已知导航异常无媒体返回可重试 `NETWORK_TIMEOUT`；完整观察后的确无媒体返回不可重试 `NO_MEDIA_FOUND`。
- 现有 HLS、MP4、DASH、会话继承、脱敏、队列和完整测试套件全部通过。

## 回滚边界

若现场验证仍间歇失败，先保存完整的导航 warning、观察结束原因和候选统计，再形成单一新假设。不要同时恢复 `networkidle`、延长超时、放宽未验证候选和启用持久 profile；一次只验证一个变量。连续三次独立修复仍失败时，停止叠加补丁，重新评估 PageSniffer 的同步事件处理架构。
