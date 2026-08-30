# Video Crawler System Chrome Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional system Chrome browser channel with an independent persistent profile while preserving the existing bundled-Chromium default and challenge-wait behavior.

**Architecture:** Extend immutable `SnifferOptions` with one mode flag, one Chrome-specific profile path, and derived channel/profile properties. Route both persistent and temporary Playwright launch paths through those properties, then snapshot the new UI checkbox into diagnosis and queued tasks exactly like the existing visible/persistent settings.

**Tech Stack:** Python 3.10+, PyQt6, Playwright sync API, unittest, unittest.mock

---

## Working-Tree Constraint

Implement inside the existing ignored worktree
`.worktrees/video-crawler-manual-challenge`, which already contains the approved
manual-challenge wait fix. The main working tree contains the same uncommitted
fix plus user-owned `pic_test/` files. Do not stage, commit, push, delete, or
modify `pic_test/`.

### Task 1: Model and browser launch routing

**Files:**
- Modify: `tests/test_video_crawler_sniffer_access.py`
- Modify: `tools/video_crawler/models.py:87-103`
- Modify: `tools/video_crawler/sniffer.py:316-345`

- [x] **Step 1: Write failing option and launch-routing tests**

Extend `SnifferOptionsTests`:

```python
def test_default_options_use_bundled_chromium_profile(self):
    options = SnifferOptions()
    self.assertFalse(options.use_system_chrome)
    self.assertIsNone(options.browser_channel)
    self.assertEqual(options.active_profile_dir, options.profile_dir)

def test_system_chrome_uses_channel_and_independent_profile(self):
    options = SnifferOptions(use_system_chrome=True)
    self.assertEqual(options.browser_channel, "chrome")
    self.assertIn("video_crawler_chrome", options.active_profile_dir)
    self.assertNotEqual(options.active_profile_dir, options.profile_dir)
```

Add a recording Chromium fake and direct `_launch_context` tests:

```python
class RecordingLaunchChromium:
    def __init__(self):
        self.launch_kwargs = None
        self.persistent_profile_dir = None
        self.persistent_kwargs = None

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return FakeBrowser()

    def launch_persistent_context(self, profile_dir, **kwargs):
        self.persistent_profile_dir = profile_dir
        self.persistent_kwargs = kwargs
        return FakeContext()


class RecordingLaunchPlaywright:
    def __init__(self):
        self.chromium = RecordingLaunchChromium()
```

```python
def test_nonpersistent_system_chrome_passes_channel_to_launch(self):
    playwright = RecordingLaunchPlaywright()
    sniffer = PageSniffer(options=SnifferOptions(use_system_chrome=True))
    browser, context = sniffer._launch_context(playwright)
    self.assertEqual(playwright.chromium.launch_kwargs["channel"], "chrome")
    self.assertIsNotNone(browser)

def test_persistent_system_chrome_uses_independent_profile(self):
    playwright = RecordingLaunchPlaywright()
    options = SnifferOptions(
        use_persistent_profile=True,
        use_system_chrome=True,
        system_chrome_profile_dir="./test-system-chrome-profile",
    )
    with patch("tools.video_crawler.sniffer.os.makedirs") as makedirs:
        browser, context = PageSniffer(options=options)._launch_context(playwright)
    makedirs.assert_called_once_with(options.active_profile_dir, exist_ok=True)
    self.assertIsNone(browser)
    self.assertEqual(
        playwright.chromium.persistent_profile_dir,
        options.active_profile_dir,
    )
    self.assertEqual(
        playwright.chromium.persistent_kwargs["channel"],
        "chrome",
    )
```

- [x] **Step 2: Run the new tests and verify RED**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.SnifferOptionsTests tests.test_video_crawler_sniffer_access.PageSnifferBrowserLaunchTests -v
```

Expected: failures for missing option fields/properties and absent `channel`.

- [x] **Step 3: Implement option properties and launch kwargs**

Add to `SnifferOptions`:

```python
use_system_chrome: bool = False
system_chrome_profile_dir: str = "./browser_profiles/video_crawler_chrome"

@property
def browser_channel(self) -> str | None:
    return "chrome" if self.use_system_chrome else None

@property
def active_profile_dir(self) -> str:
    if self.use_system_chrome:
        return self.system_chrome_profile_dir
    return self.profile_dir
```

Refactor `_launch_context`:

```python
launch_kwargs = {
    "headless": self.options.headless,
    "args": launch_args,
}
if self.options.browser_channel:
    launch_kwargs["channel"] = self.options.browser_channel

if self.options.use_system_chrome:
    self.log(
        "[*] 浏览器内核: 系统 Chrome（实验）；该模式不会隐藏自动化标记。"
    )
else:
    self.log("[*] 浏览器内核: Playwright Chromium。")

try:
    if self.options.use_persistent_profile:
        os.makedirs(self.options.active_profile_dir, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            self.options.active_profile_dir,
            **launch_kwargs,
            **context_kwargs,
        )
        return None, context
    browser = playwright.chromium.launch(**launch_kwargs)
except Exception:
    if self.options.use_system_chrome:
        self.log("[X] 系统 Chrome 启动失败；请确认已安装 Google Chrome。")
    raise
```

Create the non-persistent context as before after successful browser launch.

- [x] **Step 4: Run launch tests and the full sniffer module**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access -v
```

Expected: all sniffer tests pass.

### Task 2: UI and queued-task wiring

**Files:**
- Modify: `tests/test_video_downloader.py:1060-1211`
- Modify: `tools/video_downloader.py:146-163`
- Modify: `tools/video_downloader.py:239-332`

- [x] **Step 1: Write failing UI wiring tests**

Add a default-state test:

```python
def test_system_chrome_mode_defaults_off(self):
    self.assertFalse(self.tool.system_chrome_chk.isChecked())
    self.assertIn("实验", self.tool.system_chrome_chk.text())
```

Update existing diagnosis, queue, worker, and legacy tests to select or assert:

```python
self.tool.system_chrome_chk.setChecked(True)
self.assertTrue(options.use_system_chrome)
self.assertTrue(task["sniffer_use_system_chrome"])
self.assertTrue(RecordingSpider.init_kwargs["sniffer_options"].use_system_chrome)
self.assertFalse(legacy_options.use_system_chrome)
```

Add `"sniffer_use_system_chrome": True` to the explicit worker task fixture.

- [x] **Step 2: Run affected UI tests and verify RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader.VideoDownloaderToolTests.test_system_chrome_mode_defaults_off tests.test_video_downloader.VideoDownloaderToolTests.test_diagnose_task_uses_ui_sniffer_options tests.test_video_downloader.VideoDownloaderToolTests.test_added_task_snapshots_sniffer_options tests.test_video_downloader.VideoDownloaderToolTests.test_worker_passes_task_concurrency_to_spider tests.test_video_downloader.VideoDownloaderToolTests.test_worker_uses_25_second_wait_for_legacy_task_snapshot -v
```

Expected: failures because the checkbox and task field do not exist.

- [x] **Step 3: Add the checkbox and propagate its value**

Create the checkbox after persistent-session mode:

```python
self.system_chrome_chk = QCheckBox("系统 Chrome（实验）")
self.system_chrome_chk.setChecked(False)
self.system_chrome_chk.setToolTip(
    "使用本机 Google Chrome；仍由 Playwright 控制，不保证通过网站验证。"
)
self.sniff_options_layout.addWidget(self.system_chrome_chk)
```

Add `use_system_chrome=self.system_chrome_chk.isChecked()` to
`_build_sniffer_options`, `sniffer_use_system_chrome` to the queue snapshot,
and `use_system_chrome=task.get("sniffer_use_system_chrome", False)` to worker
reconstruction.

- [x] **Step 4: Run all downloader tests and verify GREEN**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_downloader -v
```

Expected: all downloader tests pass.

### Task 3: Documentation, synchronization, and regression verification

**Files:**
- Modify: `docs/项目介绍.md:633-651`
- Verify: all files from Tasks 1 and 2

- [x] **Step 1: Document system Chrome's compatibility boundary**

Add after the visible-challenge description:

```markdown
“系统 Chrome（实验）”会让 Playwright 使用本机安装的 Google Chrome，且在
复用会话时使用独立的 `browser_profiles/video_crawler_chrome` profile。该模式
只解决浏览器版本或内核兼容性，不隐藏 `navigator.webdriver`，不保证通过站点
自动化检测。
```

- [x] **Step 2: Run focused tests in the worktree**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_sniffer_access tests.test_video_downloader -v
```

Expected: all focused tests pass. If `tests/.tmp` is blocked by the sandbox,
rerun with the same command outside the sandbox.

- [x] **Step 3: Synchronize only approved files to the main working tree**

Confirm the main copies have not changed since the worktree fork, then apply the
verified patch to the six approved files. Preserve design/plan documents and
the unrelated `pic_test/` directory.

- [x] **Step 4: Run the full suite from the main working tree**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected: all tests pass, apart from the existing Windows symlink-permission
skip.

- [x] **Step 5: Final safety review**

Run `git diff --check`, list modified/untracked files, and confirm no browser
profile, Cookie database, media output, or `pic_test/` file was added to the
implementation diff. Leave all changes uncommitted.
