# Runtime Code Comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add detailed, maintainable Chinese documentation to all Python runtime code while preserving behavior.

**Architecture:** Enforce objective coverage with an AST test, then document the code in four responsibility-based batches. Use concise docstrings for simple definitions and contract/rationale comments for complex UI, sniffing, HLS, and DASH logic.

**Tech Stack:** Python 3.10+, unittest, ast, PyQt6

---

### Task 1: Add Documentation Coverage Guard

**Files:**
- Create: `tests/test_code_documentation.py`

- [ ] Add an AST test that scans `tools/**/*.py` and reports every module, class,
  function, async function, method, or nested helper without a docstring.
- [ ] Run `python -m unittest tests.test_code_documentation -v` and confirm it
  fails because runtime files are currently undocumented.

### Task 2: Document Runtime Setup And UI Modules

**Files:**
- Modify: `tools/__init__.py`
- Modify: `tools/runtime_setup.py`
- Modify: `tools/main.py`
- Modify: `tools/theme_utils.py`
- Modify: `tools/video_downloader.py`
- Modify: `tools/video_extractor.py`
- Modify: `tools/keyword_organizer.py`
- Modify: `tools/image_resizer.py`

- [ ] Add module and definition docstrings.
- [ ] Explain UI painting, transparent scrolling, queue snapshots, worker/thread
  boundaries, batch retry decisions, and filesystem side effects.
- [ ] Run UI, runtime-setup, and documentation tests.

### Task 3: Document Crawler Models And Services

**Files:**
- Modify: `tools/video_crawler/__init__.py`
- Modify: `tools/video_crawler/models.py`
- Modify: `tools/video_crawler/errors.py`
- Modify: `tools/video_crawler/logging_utils.py`
- Modify: `tools/video_crawler/reporting.py`
- Modify: `tools/video_crawler/session.py`
- Modify: `tools/video_crawler/resume.py`
- Modify: `tools/video_crawler/diagnostics.py`

- [ ] Document model contracts, error semantics, redaction, session inheritance,
  manifest persistence, and diagnosis routing.
- [ ] Run model, diagnostics, session, resume, redaction, and documentation tests.

### Task 4: Document Sniffing And Spider Orchestration

**Files:**
- Modify: `tools/video_crawler/sniffer.py`
- Modify: `tools/video_crawler/spider.py`

- [ ] Document URL normalization, access-limited detection, response-body
  extraction, Playwright lifecycle, reliable-candidate wait policy, quality
  metrics, candidate tolerance, adapter construction, and output validation.
- [ ] Run sniffing, diagnostics, downloader, and documentation tests.

### Task 5: Document Download Adapters

**Files:**
- Modify: `tools/video_crawler/adapters/__init__.py`
- Modify: `tools/video_crawler/adapters/base.py`
- Modify: `tools/video_crawler/adapters/direct_mp4.py`
- Modify: `tools/video_crawler/adapters/ytdlp.py`
- Modify: `tools/video_crawler/adapters/dash.py`
- Modify: `tools/video_crawler/adapters/hls.py`

- [ ] Document adapter contracts and external-process behavior.
- [ ] Add rationale comments around MPD expansion, HLS encryption/ranges/maps,
  live polling, resume manifests, bounded concurrency, and FFmpeg merge cleanup.
- [ ] Run adapter, HLS, DASH, yt-dlp, structured-error, and documentation tests.

### Task 6: Verify Behavior And Coverage

- [ ] Run `python -m compileall -q tools`.
- [ ] Run `python -m unittest tests.test_code_documentation -v` and confirm every
  runtime module and definition has a non-empty docstring.
- [ ] Run the full offscreen unittest suite and confirm all tests pass.
- [ ] Run `git diff --check` and review the diff to ensure it contains only
  comments, docstrings, and the documentation coverage test.
