# Video Downloader Queue Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add editable per-task HLS segment concurrency and one end-of-batch result dialog with retry-all-failures while preserving the current downloader UI and mode behavior.

**Architecture:** Extend `UniversalVideoSpider` so failures propagate and successful runs return a verified output path. Keep the existing single Python queue thread, snapshot mode/concurrency into each task, accumulate task result dictionaries until `Queue.unfinished_tasks` reaches zero, then emit one Qt signal to render the summary and optionally requeue failures on the GUI thread.

**Tech Stack:** Python 3.13, PyQt6, asyncio/aiohttp, requests, m3u8, standard-library `unittest`

---

## File Structure

- Modify `tools/video_downloader.py`: downloader result contract, configurable segment concurrency, UI input, batch aggregation, dialog, and retry behavior.
- Modify `tools/theme_utils.py`: include `QSpinBox` in the existing themed input selector.
- Create `tests/__init__.py`: make the test directory importable by `unittest` discovery.
- Create `tests/test_video_downloader.py`: downloader and queue/UI behavior tests without real network or FFmpeg.
- Create `tests/test_theme_utils.py`: theme coverage for the new spin box.

### Task 1: Configurable and Verifiable Download Core

**Files:**
- Modify: `tools/video_downloader.py:26-368`
- Create: `tests/__init__.py`
- Create: `tests/test_video_downloader.py`

- [ ] **Step 1: Write failing core tests**

Create the test package and add tests that express the new downloader contract:

```python
# tests/__init__.py
```

```python
# tests/test_video_downloader.py
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from tools.video_downloader import (
    UniversalVideoSpider,
    VideoDownloadError,
    VideoDownloaderTool,
)


class TrackingSpider(UniversalVideoSpider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_downloads = 0
        self.max_active_downloads = 0

    async def _download_ts(self, session, ts_url, save_path, cipher):
        self.active_downloads += 1
        self.max_active_downloads = max(
            self.max_active_downloads, self.active_downloads
        )
        await asyncio.sleep(0.01)
        self.active_downloads -= 1
        return True


class FailingSegmentSpider(UniversalVideoSpider):
    async def _download_ts(self, session, ts_url, save_path, cipher):
        return ts_url != "bad"


class UniversalVideoSpiderTests(unittest.TestCase):
    def test_segment_downloads_obey_selected_concurrency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            spider = TrackingSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                segment_concurrency=2,
            )
            items = [(str(index), os.path.join(temp_dir, str(index))) for index in range(5)]

            asyncio.run(spider._download_segments(None, items, None))

            self.assertEqual(spider.max_active_downloads, 2)

    def test_failed_segment_makes_download_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            spider = FailingSegmentSpider(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                segment_concurrency=3,
            )

            with self.assertRaisesRegex(VideoDownloadError, "1 个切片"):
                asyncio.run(
                    spider._download_segments(
                        None,
                        [("good", "good.ts"), ("bad", "bad.ts")],
                        None,
                    )
                )

    def test_missing_sniffed_stream_is_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
            with patch.object(spider, "_sniff_real_url", return_value=None):
                with self.assertRaisesRegex(VideoDownloadError, "未能找到视频流"):
                    spider.run("https://example.invalid/watch", "video")

    def test_empty_output_is_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "empty.mp4")
            open(output_path, "wb").close()
            spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)

            with self.assertRaisesRegex(VideoDownloadError, "输出文件"):
                spider._verify_output(output_path)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests -v
```

Expected: import failure because `VideoDownloadError` and the configurable concurrency API do not exist.

- [ ] **Step 3: Implement the minimal downloader contract**

In `tools/video_downloader.py`:

```python
class VideoDownloadError(RuntimeError):
    """Raised when a video task cannot produce a complete, non-empty output."""


class UniversalVideoSpider:
    def __init__(
        self,
        output_dir="./downloads",
        temp_dir="./temp",
        log_callback=None,
        is_high_speed=False,
        segment_concurrency=None,
    ):
        self.output_dir = output_dir
        self.temp_dir = temp_dir
        self.log_callback = log_callback
        self.is_high_speed = is_high_speed
        default_concurrency = 30 if is_high_speed else 5
        self.segment_concurrency = (
            default_concurrency if segment_concurrency is None else int(segment_concurrency)
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

    def _verify_output(self, output_path):
        if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
            raise VideoDownloadError(f"输出文件不存在或为空: {output_path}")
        return output_path

    async def _download_segments(self, session, download_items, cipher):
        semaphore = asyncio.Semaphore(self.segment_concurrency)

        async def bounded_download(ts_url, save_path):
            async with semaphore:
                return await self._download_ts(
                    session, ts_url, save_path, cipher
                )

        results = await asyncio.gather(
            *(bounded_download(url, path) for url, path in download_items)
        )
        failed_count = results.count(False)
        if failed_count:
            raise VideoDownloadError(f"有 {failed_count} 个切片下载失败")
```

Apply these exact result/error changes to the existing methods:

```python
# run branches
if url.lower().endswith('.mp4') or '.mp4?' in url:
    save_path = os.path.join(self.output_dir, f"{output_filename}.mp4")
    return self._download_mp4(url, save_path)
elif url.lower().endswith('.m3u8') or '.m3u8?' in url:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(self._download_m3u8(url, output_filename))
    finally:
        loop.close()
# after sniffing
if real_url:
    self.headers["Referer"] = url
    parsed_url = urlparse(url)
    self.headers["Origin"] = f"{parsed_url.scheme}://{parsed_url.netloc}"
    return self.run(real_url, output_filename)
raise VideoDownloadError("嗅探失败，未能找到视频流")

# last line of _download_mp4
return self._verify_output(save_path)

# successful write branch and final line of _download_ts
with open(save_path, 'wb') as f:
    f.write(content)
return True
# after the retry loop
return False

# required init segment exception branch
except Exception as exc:
    raise VideoDownloadError(f"Init 文件下载失败: {exc}") from exc

# replace task/coroutine construction in _download_m3u8
download_items = []
for i, segment in enumerate(playlist.segments):
    save_path = os.path.join(video_temp_dir, f"{i:05d}.ts")
    ts_files_list.append(save_path)
    download_items.append((segment.absolute_uri, save_path))
self.log(f"[*] 当前并发数限制设为: {self.segment_concurrency}")
await self._download_segments(session, download_items, cipher)

# after _merge_with_ffmpeg returns
return self._verify_output(final_mp4_path)

# _merge_with_ffmpeg failure branches
if not valid_ts_files:
    raise VideoDownloadError("没有任何有效切片，合并任务中止")
except subprocess.CalledProcessError as exc:
    error = exc.stderr.decode('utf-8', errors='ignore') if exc.stderr else str(exc)
    raise VideoDownloadError(f"FFmpeg 合并失败: {error}") from exc
except Exception as exc:
    raise VideoDownloadError(f"视频合并失败: {exc}") from exc
# return at the end of each successful merge branch
return self._verify_output(output_mp4)
```

Place the M3U8 download/merge body inside `try` and its existing temporary-file removal in `finally`. Use a nested cleanup `try/except` that logs `[!] 临时文件清理失败: {exc}` so cleanup cannot replace the download exception.

- [ ] **Step 4: Run core tests to verify GREEN**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit the core**

```powershell
git add tools/video_downloader.py tests/__init__.py tests/test_video_downloader.py
git commit -m "feat: make video downloads verifiable"
```

### Task 2: Per-Task Concurrency UI and Mode Defaults

**Files:**
- Modify: `tools/video_downloader.py:371-486`
- Modify: `tests/test_video_downloader.py`

- [ ] **Step 1: Write failing UI/task tests**

Append:

```python
class VideoDownloaderToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tool = VideoDownloaderTool(start_worker=False)

    def tearDown(self):
        self.tool.close()

    def test_mode_switch_restores_default_concurrency(self):
        self.assertEqual(self.tool.concurrency_spin.value(), 5)

        self.tool.toggle_mode()
        self.assertEqual(self.tool.concurrency_spin.value(), 30)

        self.tool.concurrency_spin.setValue(12)
        self.tool.toggle_mode()
        self.assertEqual(self.tool.concurrency_spin.value(), 5)

    def test_added_task_snapshots_custom_concurrency(self):
        self.tool.url_entry.setText("https://example.invalid/video.m3u8")
        self.tool.name_entry.setText("example")
        self.tool.path_entry.setText("downloads")
        self.tool.concurrency_spin.setValue(12)

        self.tool.add_to_queue()
        task = self.tool.task_queue.get_nowait()

        self.assertEqual(task["segment_concurrency"], 12)
        self.assertFalse(task["is_high_speed"])
        self.assertIn("12并发", self.tool.queue_listbox.item(0).text())
        self.tool.task_queue.task_done()
```

- [ ] **Step 2: Run UI tests to verify RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_video_downloader.VideoDownloaderToolTests -v
```

Expected: failure because `start_worker` and `concurrency_spin` do not exist.

- [ ] **Step 3: Add the spin box and snapshot its value**

Import `QSpinBox`. Change the widget constructor and worker startup exactly as follows, then add the row below the save path:

```python
def __init__(self, start_worker=True, spider_factory=UniversalVideoSpider):
    super().__init__()
    self.spider_factory = spider_factory
    self.task_queue = queue.Queue()
    self.is_high_speed_mode = False

# at the former unconditional thread start
if start_worker:
    threading.Thread(target=self.queue_worker, daemon=True).start()
```

```python
row4 = QHBoxLayout()
row4.addWidget(QLabel("切片并发数:"))
self.concurrency_spin = QSpinBox()
self.concurrency_spin.setRange(1, 100)
self.concurrency_spin.setValue(5)
self.concurrency_spin.setSuffix(" 个")
row4.addWidget(self.concurrency_spin)
row4.addStretch()
layout.addLayout(row4)
```

Use these exact mode and task values:

```python
if self.is_high_speed_mode:
    self.concurrency_spin.setValue(30)
    self.mode_btn.setText("⚡ 当前: 高速爆发模式")
else:
    self.concurrency_spin.setValue(5)
    self.mode_btn.setText("🛡️ 当前: 低速稳定模式")

task = {
    "url": url,
    "name": name,
    "save_dir": save_dir,
    "is_high_speed": self.is_high_speed_mode,
    "segment_concurrency": self.concurrency_spin.value(),
}
self._enqueue_task(task)
```

The `_enqueue_task` implementation is introduced in Task 3. Until then, keep the current `task_queue.put` and queue-list insertion with display text `f"[{mode_label} / {task['segment_concurrency']}并发] {name} -> {url[:40]}..."`; Task 3 replaces those two lines with `_enqueue_task(task)`.

- [ ] **Step 4: Run UI/task tests to verify GREEN**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_video_downloader.VideoDownloaderToolTests -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit the UI input**

```powershell
git add tools/video_downloader.py tests/test_video_downloader.py
git commit -m "feat: configure per-task segment concurrency"
```

### Task 3: End-of-Batch Summary and Retry-All-Failures

**Files:**
- Modify: `tools/video_downloader.py:371-520`
- Modify: `tests/test_video_downloader.py`

- [ ] **Step 1: Write failing batch tests**

Append methods to `VideoDownloaderToolTests`:

```python
    def test_batch_emits_once_after_all_tasks_finish(self):
        self.tool.batch_finished_signal.disconnect(self.tool.show_batch_results)
        batches = []
        self.tool.batch_finished_signal.connect(batches.append)
        first = {"name": "one"}
        second = {"name": "two"}
        self.tool.task_queue.put(first)
        self.tool.task_queue.put(second)

        self.tool._finish_task(
            {"task": first, "success": True, "output_path": "one.mp4", "error": ""}
        )
        self.assertEqual(batches, [])

        self.tool._finish_task(
            {"task": second, "success": False, "output_path": "", "error": "failed"}
        )
        self.assertEqual(len(batches), 1)
        self.assertEqual([item["task"]["name"] for item in batches[0]], ["one", "two"])

    def test_retry_requeues_only_failed_tasks_with_original_configuration(self):
        failed_task = {
            "url": "https://example.invalid/fail.m3u8",
            "name": "failed",
            "save_dir": "downloads",
            "is_high_speed": True,
            "segment_concurrency": 17,
        }
        results = [
            {"task": {"name": "ok"}, "success": True, "output_path": "ok.mp4", "error": ""},
            {"task": failed_task, "success": False, "output_path": "", "error": "network"},
        ]

        self.tool.retry_failed_tasks(results)
        retried = self.tool.task_queue.get_nowait()

        self.assertEqual(retried, failed_task)
        self.assertTrue(self.tool.task_queue.empty())
        self.tool.task_queue.task_done()

    def test_batch_summary_contains_success_and_failure_details(self):
        summary, details = self.tool.format_batch_results([
            {"task": {"name": "ok"}, "success": True, "output_path": "ok.mp4", "error": ""},
            {"task": {"name": "bad"}, "success": False, "output_path": "", "error": "merge failed"},
        ])

        self.assertIn("成功 1 个，失败 1 个", summary)
        self.assertIn("ok", details)
        self.assertIn("bad: merge failed", details)
```

- [ ] **Step 2: Run batch tests to verify RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_video_downloader.VideoDownloaderToolTests -v
```

Expected: failures because the batch signal and helper methods do not exist.

- [ ] **Step 3: Implement batch aggregation, dialog, and retry**

Add `batch_finished_signal = pyqtSignal(object)`, connect it to `show_batch_results`, and initialize `self._batch_results = []`.

Implement helpers with these contracts:

```python
def _enqueue_task(self, task):
    queued_task = dict(task)
    self.task_queue.put(queued_task)
    mode_label = "高速" if queued_task["is_high_speed"] else "稳定"
    concurrency = queued_task["segment_concurrency"]
    self.queue_listbox.addItem(
        f"[{mode_label} / {concurrency}并发] {queued_task['name']} -> "
        f"{queued_task['url'][:40]}..."
    )

def _finish_task(self, result):
    self._batch_results.append(result)
    self.task_queue.task_done()
    if self.task_queue.unfinished_tasks == 0:
        completed_batch = list(self._batch_results)
        self._batch_results.clear()
        self.batch_finished_signal.emit(completed_batch)

def retry_failed_tasks(self, results):
    for result in results:
        if not result["success"]:
            self._enqueue_task(result["task"])

def format_batch_results(self, results):
    succeeded = [result for result in results if result["success"]]
    failed = [result for result in results if not result["success"]]
    summary = f"队列处理完成：成功 {len(succeeded)} 个，失败 {len(failed)} 个。"
    detail_lines = ["成功任务："]
    detail_lines.extend(
        f"  ✓ {result['task']['name']}" for result in succeeded
    )
    detail_lines.append("失败任务：")
    detail_lines.extend(
        f"  ✗ {result['task']['name']}: {result['error']}" for result in failed
    )
    return summary, "\n".join(detail_lines)
```

Use these exact worker and dialog methods so each dequeued task is finished exactly once and QWidget access stays on the main thread:

```python
def _execute_task(self, task):
    try:
        spider = self.spider_factory(
            output_dir=task["save_dir"],
            temp_dir="./temp",
            log_callback=self.log_signal.emit,
            is_high_speed=task["is_high_speed"],
            segment_concurrency=task["segment_concurrency"],
        )
        output_path = spider.run(task["url"], task["name"])
        return {
            "task": task,
            "success": True,
            "output_path": output_path,
            "error": "",
        }
    except Exception as exc:
        self.log_signal.emit(f"\n[X] 错误: {exc}")
        return {
            "task": task,
            "success": False,
            "output_path": "",
            "error": str(exc),
        }

def queue_worker(self):
    while True:
        task = self.task_queue.get()
        self.queue_pop_signal.emit()
        self.log_signal.emit("\n" + "=" * 50)
        self.log_signal.emit(f"▶ 开始执行: {task['name']}")
        result = self._execute_task(task)
        self.log_signal.emit(
            f"⏹ 任务 {task['name']} 结束。等待下一个任务...\n"
        )
        self._finish_task(result)

def show_batch_results(self, results):
    summary, details = self.format_batch_results(results)
    failed = [result for result in results if not result["success"]]
    message_box = QMessageBox(self)
    message_box.setWindowTitle("下载队列处理完成")
    message_box.setText(summary)
    message_box.setDetailedText(details)
    message_box.setIcon(
        QMessageBox.Icon.Warning if failed else QMessageBox.Icon.Information
    )
    retry_button = None
    if failed:
        retry_button = message_box.addButton(
            "重试全部失败任务", QMessageBox.ButtonRole.AcceptRole
        )
    message_box.addButton(QMessageBox.StandardButton.Close)
    message_box.exec()
    if retry_button is not None and message_box.clickedButton() is retry_button:
        self.retry_failed_tasks(results)
```

- [ ] **Step 4: Run batch tests to verify GREEN**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_video_downloader.VideoDownloaderToolTests -v
```

Expected: 5 UI/batch tests pass.

- [ ] **Step 5: Commit batch results**

```powershell
git add tools/video_downloader.py tests/test_video_downloader.py
git commit -m "feat: summarize download batches and retry failures"
```

### Task 4: Theme Integration and Full Verification

**Files:**
- Modify: `tools/theme_utils.py:77-84`
- Create: `tests/test_theme_utils.py`

- [ ] **Step 1: Write the failing theme test**

```python
# tests/test_theme_utils.py
import unittest

from tools.theme_utils import get_global_stylesheet


class ThemeUtilsTests(unittest.TestCase):
    def test_spin_box_uses_themed_input_style(self):
        stylesheet = get_global_stylesheet("missing-wallpaper.png")
        self.assertIn("QLineEdit, QSpinBox, QTextEdit, QListWidget", stylesheet)
```

- [ ] **Step 2: Run theme test to verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_theme_utils -v
```

Expected: failure because `QSpinBox` is absent from the selector.

- [ ] **Step 3: Extend the existing selector**

In `tools/theme_utils.py`, change:

```css
QLineEdit, QTextEdit, QListWidget
```

to:

```css
QLineEdit, QSpinBox, QTextEdit, QListWidget
```

- [ ] **Step 4: Run all tests**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest discover -s tests -v
```

Expected: all 10 tests pass with zero failures or errors.

- [ ] **Step 5: Run source and import verification**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -c "import ast, pathlib; [ast.parse(path.read_text(encoding='utf-8')) for path in pathlib.Path('tools').glob('*.py')]; import tools.video_downloader, tools.theme_utils; print('source and imports ok')"
git diff --check
git status --short
```

Expected: `source and imports ok`, no diff errors, and only intended files are modified/untracked.

- [ ] **Step 6: Commit theme and tests**

```powershell
git add tools/theme_utils.py tests/test_theme_utils.py docs/superpowers/plans/2026-06-19-video-downloader-queue-results.md
git commit -m "test: verify downloader queue enhancements"
```

- [ ] **Step 7: Review requirements against the design**

Confirm each requirement in `docs/superpowers/specs/2026-06-19-video-downloader-queue-results-design.md` has evidence in tests or the final diff: editable 1–100 spin box, mode defaults 30/5, per-task snapshot, one batch signal, explicit failures, retry-all-failures, themed UI, and no other tool-page changes.
