# Video Crawler Manual Challenge Wait Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep visible Playwright sniffing alive while the user completes an allowed security verification, then trigger the real player and capture media without letting the initial 403 remain the final result.

**Architecture:** Add normalized verification-title recognition and a reusable page-snapshot helper in `sniffer.py`. Refactor the post-navigation path around one monotonic deadline: headless access errors remain immediate, while visible access errors are provisional until the current page clears or the deadline expires; media observation uses the remaining time.

**Tech Stack:** Python 3.10+, Playwright sync API, unittest, unittest.mock

---

## Working-Tree Constraint

The workspace already contains the unrelated untracked `pic_test/` directory.
Do not stage, commit, delete, or otherwise modify it. The user authorized
implementation but did not authorize a Git commit, so all changes in this plan
remain uncommitted and are verified through explicit diffs and tests.

### Task 1: Recognize verification titles consistently

**Files:**
- Modify: `tests/test_video_crawler_sniffer_access.py:22-49`
- Modify: `tools/video_crawler/sniffer.py:21-24`
- Modify: `tools/video_crawler/sniffer.py:234-258`

- [ ] **Step 1: Write failing title-recognition tests**

Add table-driven cases to `PageAccessDiagnosticsTests`:

```python
def test_detects_normalized_security_verification_titles(self):
    for title in (
        "Just a moment...",
        "Justamoment",
        "Checking your browser",
        "正在进行安全验证",
    ):
        with self.subTest(title=title):
            error = detect_access_limited_page(
                PageAccessSnapshot(status_code=200, title=title)
            )
            self.assertIsNotNone(error)
            self.assertEqual(error.code, VideoErrorCode.HTTP_FORBIDDEN)
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.PageAccessDiagnosticsTests.test_detects_normalized_security_verification_titles -v
```

Expected: FAIL for verification titles not present in the current keyword list.

- [ ] **Step 3: Implement normalized title matching**

Add a private normalizer and normalized keywords:

```python
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


def _normalize_access_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (title or "").lower())
```

Update `detect_access_limited_page` to compare every keyword against the
normalized title while preserving HTTP 403 recognition and the existing
structured error details.

- [ ] **Step 4: Run diagnostics tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.PageAccessDiagnosticsTests -v
```

Expected: all diagnostic tests pass.

### Task 2: Defer visible access failure until the shared deadline

**Files:**
- Modify: `tests/test_video_crawler_sniffer_access.py:51-90`
- Modify: `tests/test_video_crawler_sniffer_access.py:234-418`
- Modify: `tools/video_crawler/sniffer.py:283-548`

- [ ] **Step 1: Preserve the headless fast-failure test and add visible transition tests**

Rename the existing access-flow test to make its mode explicit, then add fakes
that start on a challenge and mutate during `wait_for_timeout`:

```python
class ChallengeTransitionPage(FakePage):
    def __init__(self, clears=True, emits_hls=True):
        super().__init__()
        self._title = "Just a moment..."
        self._status = 403
        self.clears = clears
        self.emits_hls = emits_hls
        self.wait_calls = 0
        self.response_callback = None
        self.hls_url = "https://cdn.example.test/main.m3u8"

    def on(self, event_name, callback):
        if event_name == "response":
            self.response_callback = callback

    def goto(self, page_url, **kwargs):
        return FakeMainResponse(status=403)

    def title(self):
        return self._title

    def locator(self, selector):
        return FakeMediaLocator() if self._status == 200 else FakeLocator()

    def wait_for_timeout(self, timeout):
        self.wait_calls += 1
        if self.clears and self.wait_calls == 1:
            self._status = 200
            self._title = "Regular Video Page"
        elif self.emits_hls and self._status == 200 and self.wait_calls == 2:
            self.response_callback(FakeObservedResponse(
                self.hls_url,
                "application/vnd.apple.mpegurl",
                "media",
            ))
```

Add these behavior tests:

```python
def test_headless_sniff_raises_immediately_for_initial_403(self):
    page = ChallengeTransitionPage(clears=False, emits_hls=False)
    with patch("playwright.sync_api.sync_playwright",
               return_value=FakePlaywrightManager(FakePlaywright(page))):
        with self.assertRaises(VideoDownloadError):
            PageSniffer(options=SnifferOptions(headless=True)).sniff(
                "https://site.example/watch"
            )
    self.assertEqual(page.wait_calls, 0)

def test_visible_sniff_waits_for_challenge_then_captures_hls(self):
    page = ChallengeTransitionPage()
    logs = []
    with patch("playwright.sync_api.sync_playwright",
               return_value=FakePlaywrightManager(FakePlaywright(page))):
        report = PageSniffer(
            log_callback=logs.append,
            options=SnifferOptions(headless=False, manual_wait_seconds=10),
        ).sniff("https://site.example/watch")
    self.assertEqual(report.best_candidate.url, page.hls_url)
    self.assertGreaterEqual(page.wait_calls, 2)
    self.assertTrue(any("安全验证已通过" in message for message in logs))

def test_visible_sniff_returns_empty_report_after_challenge_clears(self):
    page = ChallengeTransitionPage(clears=True, emits_hls=False)
    with patch("playwright.sync_api.sync_playwright",
               return_value=FakePlaywrightManager(FakePlaywright(page))):
        report = PageSniffer(
            options=SnifferOptions(headless=False, manual_wait_seconds=1)
        ).sniff("https://site.example/watch")
    self.assertEqual(report.candidates, [])

def test_visible_sniff_raises_forbidden_when_challenge_reaches_deadline(self):
    page = ChallengeTransitionPage(clears=False, emits_hls=False)
    with patch("playwright.sync_api.sync_playwright",
               return_value=FakePlaywrightManager(FakePlaywright(page))):
        with self.assertRaises(VideoDownloadError) as raised:
            PageSniffer(
                options=SnifferOptions(headless=False, manual_wait_seconds=1)
            ).sniff("https://site.example/watch")
    self.assertEqual(raised.exception.code, VideoErrorCode.HTTP_FORBIDDEN)
    self.assertGreater(page.wait_calls, 0)
```

Use controlled `time.monotonic` sequences where necessary so timeout tests do
not depend on wall-clock time.

- [ ] **Step 2: Run the four access-flow tests and verify RED**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.PageSnifferAccessFlowTests -v
```

Expected: visible transition tests fail because the current implementation
raises before its wait and closes the context.

- [ ] **Step 3: Extract safe page snapshot capture**

Add a local helper inside `sniff` so every access check reads current state and
turns DOM read failures into the existing navigation warning path:

```python
def capture_access_snapshot(status_code=None):
    return PageAccessSnapshot(
        status_code=status_code,
        title=page.title(),
        final_url=page.url,
        video_count=page.locator("video").count(),
        iframe_count=page.locator("iframe").count(),
    )
```

Keep the initial `main_response.status` only for the initial snapshot. Polling
snapshots use the current title/URL/DOM with `status_code=None`, preventing the
stale first 403 from permanently classifying the later player page.

- [ ] **Step 4: Refactor waiting around one deadline**

Create `deadline` immediately after initial navigation diagnostics. Change
`wait_for_candidates` to accept that deadline rather than creating a new one:

```python
def wait_for_candidates(deadline):
    while not has_reliable_media_candidate(candidates):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        page.wait_for_timeout(max(1, min(1000, int(remaining * 1000))))
```

When the initial snapshot is limited:

```python
if access_error and not self.options.visible:
    raise access_error
if access_error:
    self.log("[*] 检测到安全验证页；请在浏览器中完成人工验证。")
    while time.monotonic() < deadline:
        page.wait_for_timeout(...)
        current_snapshot = capture_access_snapshot()
        if detect_access_limited_page(current_snapshot) is None:
            self.log("[+] 安全验证已通过；正在进入真实播放器。")
            break
    else:
        raise detect_access_limited_page(capture_access_snapshot()) or access_error
```

After the challenge clears, call `trigger_playback(page)` exactly once and pass
the same deadline to media waiting. At deadline, re-snapshot the current page;
raise only if it remains limited and no reliable network HLS was captured.
Preserve the existing session extraction and context cleanup in `finally`.

- [ ] **Step 5: Run focused sniffer tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access -v
```

Expected: all sniffer access, timeout, iframe, response-body, and deadline tests
pass.

- [ ] **Step 6: Review the implementation diff**

Run:

```powershell
git diff --check -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
git diff -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
```

Expected: only the approved challenge-wait implementation and its tests.

### Task 3: Document behavior and run regression verification

**Files:**
- Modify: `docs/项目介绍.md:633-650`
- Verify: `tools/video_crawler/sniffer.py`
- Verify: `tests/test_video_crawler_sniffer_access.py`
- Verify: `tests/test_video_downloader.py`

- [ ] **Step 1: Clarify the security boundary and visible wait behavior**

Extend the existing paragraph after the security exclusions with:

```markdown
可视化模式遇到安全验证页时，会在同一浏览器上下文中保留配置的等待时间；
用户完成人工验证后，嗅探器重新读取当前页面状态、触发播放器并监听媒体请求。
无界面模式仍直接返回 `HTTP_FORBIDDEN`。首次导航的 403 只表示初始验证页，
不会覆盖人工验证后真实播放器的最终状态。
```

- [ ] **Step 2: Run the combined focused suite**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_sniffer_access tests.test_video_crawler_diagnostics tests.test_video_downloader -v
```

Expected: all focused tests pass.

- [ ] **Step 3: Run the full suite**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected: all tests pass. Report any pre-existing inaccessible temporary test
directories separately rather than hiding them.

- [ ] **Step 4: Perform final safety review**

Run:

```powershell
git diff --check
git status --short --branch -uall
```

Confirm that no browser profile, downloaded media, cache file, or unrelated
workspace content was modified. Leave all changes uncommitted.
