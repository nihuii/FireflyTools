# Video Crawler Beginner Learning Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a Chinese beginner guide that teaches FireflyTools through the complete video-download task flow while explaining every network and streaming protocol used by the implementation.

**Architecture:** Add one standalone learning guide under `docs/`. Organize it by runtime data flow instead of file listing, bind each concept to real classes and functions, and finish with tests and exercises that let a learner verify each layer independently.

**Tech Stack:** Markdown, Mermaid, Python 3, PyQt6, Playwright, HTTP, HLS, DASH, asyncio, FFmpeg, unittest

---

### Task 1: Build the source-code reading map

**Files:**
- Read: `tools/video_downloader.py`
- Read: `tools/video_crawler/models.py`
- Read: `tools/video_crawler/errors.py`
- Read: `tools/video_crawler/spider.py`
- Read: `tools/video_crawler/sniffer.py`
- Read: `tools/video_crawler/session.py`
- Read: `tools/video_crawler/adapters/base.py`
- Read: `tools/video_crawler/adapters/direct_mp4.py`
- Read: `tools/video_crawler/adapters/hls.py`
- Read: `tools/video_crawler/adapters/dash.py`
- Read: `tools/video_crawler/adapters/ytdlp.py`
- Read: `tools/video_crawler/resume.py`
- Read: `tools/video_crawler/logging_utils.py`
- Read: `tools/video_crawler/reporting.py`

- [ ] **Step 1: List public classes and task-entry methods**

Run:

```powershell
rg -n "^(class|def|async def)|^    (def|async def) (run|sniff|download|download_url|queue_worker|_execute_task|_resolve_candidate|_select_best_m3u8)" tools/video_downloader.py tools/video_crawler -g "*.py"
```

Expected: output includes `VideoDownloaderTool._execute_task`, `UniversalVideoSpider.run`, `PageSniffer.sniff`, adapter `download` methods, and HLS/DASH helpers.

- [ ] **Step 2: Trace return values and structured errors**

Run:

```powershell
rg -n "MediaCandidate|DiagnosticReport|BrowserSessionSnapshot|VideoDownloadError|VideoErrorCode" tools/video_downloader.py tools/video_crawler -g "*.py"
```

Expected: each central model is referenced by the layers described in the design.

### Task 2: Write the beginner learning guide

**Files:**
- Create: `docs/视频爬虫新手学习指南.md`
- Reference: `docs/superpowers/specs/2026-06-27-video-crawler-beginner-learning-guide-design.md`

- [ ] **Step 1: Write the orientation and full task-flow map**

Include the intended audience, legal boundary, project-root startup command, three-pass reading strategy, and a Mermaid graph from UI queue to output/result dialog.

- [ ] **Step 2: Write the staged source-reading route**

For each stage, include its purpose, exact source symbols, input/output data, prerequisite concepts, skippable details, and one verification exercise. Start with models and direct MP4, then cover UI queue, spider orchestration, Playwright sniffing, HLS, DASH, FFmpeg, errors, and UI result return.

- [ ] **Step 3: Add protocol explanations beside their implementation**

Explain HTTP headers/status/content type/range, browser cookies and storage, progressive MP4, HLS playlists and tags, AES-CBC and IV, fMP4, DASH MPD hierarchy and templates, FFmpeg mux/remux, and DRM boundaries. Link every protocol concept to a real file or symbol.

- [ ] **Step 4: Add tests, exercises, and second-pass routes**

Provide small safe exercises based on local unit tests and mocked data. Include a test-to-module table, common misconceptions, debugging advice, and separate follow-up routes for sniffing, HLS, DASH, and UI threading.

### Task 3: Validate document accuracy and readability

**Files:**
- Verify: `docs/视频爬虫新手学习指南.md`

- [ ] **Step 1: Validate every referenced repository path**

Run a read-only Python script that extracts backticked `tools/`, `tests/`, and `docs/` paths from the guide, removes symbol suffixes, and reports missing paths.

Expected:

```text
missing_paths=[]
```

- [ ] **Step 2: Validate required learning topics**

Run:

```powershell
rg -n "VideoDownloaderTool|UniversalVideoSpider|PageSniffer|DirectMp4Adapter|HlsAdapter|DashAdapter|SegmentManifest|HTTP|Cookie|HLS|M3U8|AES-CBC|BYTERANGE|fMP4|DASH|MPD|SegmentTemplate|FFmpeg|DRM" docs/视频爬虫新手学习指南.md
```

Expected: every required symbol and protocol appears in a substantive section.

- [ ] **Step 3: Check Markdown structure and placeholders**

Run:

```powershell
rg -n "TBD|TODO|待补充|以后再写|占位" docs/视频爬虫新手学习指南.md
```

Expected: no matches.

- [ ] **Step 4: Review the final Git diff**

Run:

```powershell
git diff --check -- docs/视频爬虫新手学习指南.md docs/superpowers/plans/2026-06-27-video-crawler-beginner-learning-guide.md
```

Expected: exit code 0 with no whitespace errors.
