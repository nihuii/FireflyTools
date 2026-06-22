# Video Segment Tolerance and Dialog Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow up to and including 3% failed HLS segments while preserving real merge failures, and theme the queue result message box for readable light/dark UI.

**Architecture:** Add one `UniversalVideoSpider` validation method that uses integer arithmetic and is called by both segment download aggregation and merge preflight. Extend the existing dynamic stylesheet with an opaque dialog background selected from the same wallpaper luminance branch as the existing text color.

**Tech Stack:** Python 3.13, asyncio, PyQt6 QSS, Pillow, standard-library `unittest`

---

### Task 1: Apply One 3% Segment-Failure Policy to Download and Merge

**Files:**
- Modify: `tools/video_downloader.py:259-271,340-349`
- Modify: `tests/test_video_downloader.py`

- [ ] **Step 1: Write failing boundary and merge tests**

Add a spider that returns `False` for URLs beginning with `bad`, then add tests for 3/100 allowed, 4/100 rejected, and merge preflight accepting exactly three missing files:

```python
class RatioFailingSegmentSpider(UniversalVideoSpider):
    async def _download_ts(self, session, ts_url, save_path, cipher):
        return not ts_url.startswith("bad")


def _segment_items(failed_count, total_count):
    return [
        (f"bad-{index}" if index < failed_count else f"good-{index}", f"{index}.ts")
        for index in range(total_count)
    ]


def test_three_percent_failed_segments_are_tolerated(self):
    with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
        spider = RatioFailingSegmentSpider(output_dir=temp_dir, temp_dir=temp_dir)
        asyncio.run(spider._download_segments(None, _segment_items(3, 100), None))


def test_more_than_three_percent_failed_segments_fail(self):
    with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
        spider = RatioFailingSegmentSpider(output_dir=temp_dir, temp_dir=temp_dir)
        with self.assertRaisesRegex(VideoDownloadError, "超过允许的 3%"):
            asyncio.run(spider._download_segments(None, _segment_items(4, 100), None))


def test_merge_accepts_three_percent_missing_segments(self):
    with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
        segment_paths = []
        for index in range(100):
            path = os.path.join(temp_dir, f"{index:05d}.ts")
            segment_paths.append(path)
            if index >= 3:
                with open(path, "wb") as segment_file:
                    segment_file.write(b"segment")
        output_path = os.path.join(temp_dir, "output.mp4")

        def fake_ffmpeg(command, **kwargs):
            with open(output_path, "wb") as output_file:
                output_file.write(b"video")

        spider = UniversalVideoSpider(output_dir=temp_dir, temp_dir=temp_dir)
        with patch("tools.video_downloader.subprocess.run", side_effect=fake_ffmpeg):
            result = spider._merge_with_ffmpeg(segment_paths, output_path)

        self.assertEqual(result, output_path)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests -v
```

Expected: 3/100 and merge tests fail because any failed/missing segment currently raises; 4/100 fails with the old error message rather than the new threshold reason.

- [ ] **Step 3: Implement the shared policy**

Add this method to `UniversalVideoSpider`:

```python
def _validate_segment_failures(self, failed_count, total_count):
    if failed_count <= 0:
        return
    if total_count <= 0 or failed_count * 100 > total_count * 3:
        raise VideoDownloadError(
            f"有 {failed_count}/{total_count} 个切片下载失败，超过允许的 3%"
        )
    failed_percent = failed_count * 100 / total_count
    self.log(
        f"[!] 警告: 有 {failed_count}/{total_count} 个切片失败 "
        f"({failed_percent:.2f}%)，未超过 3%，将继续合并。"
    )
```

Replace the download-stage unconditional raise with:

```python
failed_count = results.count(False)
self._validate_segment_failures(failed_count, len(results))
```

Replace the merge-stage missing-slice raise with:

```python
missing_count = len(ts_files) - len(valid_ts_files)
self._validate_segment_failures(missing_count, len(ts_files))
```

Keep the existing `if not valid_ts_files` failure before the ratio check so zero valid media never succeeds.

- [ ] **Step 4: Run core tests and verify GREEN**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests -v
```

Expected: all core tests pass, including the existing 1/2 failure test because 50% remains above the threshold.

- [ ] **Step 5: Commit the tolerance fix**

```powershell
git add tools/video_downloader.py tests/test_video_downloader.py
git commit -m "fix: tolerate up to three percent failed segments"
```

### Task 2: Theme Queue Result Message Boxes

**Files:**
- Modify: `tools/theme_utils.py:8-72,99-125`
- Modify: `tests/test_theme_utils.py`

- [ ] **Step 1: Write failing light/dark dialog tests**

Add imports for `os`, `tempfile`, and `PIL.Image`, create temporary white/black wallpapers, then assert generated dialog background and label colors:

```python
def test_message_box_uses_readable_light_theme(self):
    with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
        image_path = os.path.join(temp_dir, "light.png")
        Image.new("RGB", (2, 2), "white").save(image_path)
        stylesheet = get_global_stylesheet(image_path)
    self.assertIn("QMessageBox {\n            background-color: #f5f5f5;", stylesheet)
    self.assertIn("QMessageBox QLabel {\n            color: #1c2833;", stylesheet)


def test_message_box_uses_readable_dark_theme(self):
    with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
        image_path = os.path.join(temp_dir, "dark.png")
        Image.new("RGB", (2, 2), "black").save(image_path)
        stylesheet = get_global_stylesheet(image_path)
    self.assertIn("QMessageBox {\n            background-color: #252525;", stylesheet)
    self.assertIn("QMessageBox QLabel {\n            color: #fdfefe;", stylesheet)
```

- [ ] **Step 2: Run theme tests and verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_theme_utils -v
```

Expected: both tests fail because no `QMessageBox` background selector exists.

- [ ] **Step 3: Add dynamic message-box colors**

Initialize `dialog_bg = "#252525"` with the other defaults. In the light wallpaper branch set `dialog_bg = "#f5f5f5"`; in the dark branch set `dialog_bg = "#252525"`. Add these selectors after `QFrame#container`:

```css
QMessageBox {{
    background-color: {dialog_bg};
}}
QMessageBox QLabel {{
    color: {text_color};
    background: transparent;
}}
```

- [ ] **Step 4: Run full verification**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest discover -s tests -v
python -c "import ast, pathlib; [ast.parse(path.read_text(encoding='utf-8')) for path in pathlib.Path('tools').glob('*.py')]; import tools.video_downloader, tools.theme_utils; print('source and imports ok')"
git diff --check
```

Expected: all tests pass, source/import check prints `source and imports ok`, and `git diff --check` reports no errors.

- [ ] **Step 5: Commit the dialog theme and plan**

```powershell
git add tools/theme_utils.py tests/test_theme_utils.py docs/superpowers/plans/2026-06-23-video-segment-tolerance-dialog-theme.md
git commit -m "fix: theme download result dialogs"
```
