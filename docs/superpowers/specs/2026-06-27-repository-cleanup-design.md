# FireflyTools Repository Cleanup Design

## Goal

Keep the repository small, reproducible, and safe to publish while preserving the
current application, tests, development history, and bundled Windows FFmpeg.
A fresh checkout must run after installing Python dependencies and Playwright
Chromium.

## Repository Layout

```text
FireflyTools/
|- .gitignore
|- LICENSE
|- README.md
|- requirements.txt
|- pic/                       # All bundled UI wallpapers
|- tools/
|  |- ffmpeg.exe
|  |- *.py
|  `- video_crawler/
|- tests/
`- docs/
   |- project-overview.md
   |- plans/
   `- superpowers/
```

## Tracking Rules

Keep in Git:

- All Python source files under `tools/`.
- All tests under `tests/`, excluding generated caches and temporary data.
- `tools/ffmpeg.exe`.
- All bundled wallpapers under `pic/`; they are part of the application's visual
  experience and must remain available after a fresh checkout.
- README, dependency metadata, license, project overview, plans, and design docs.

Stop tracking but preserve locally where useful:

- `.idea/`.
- Python bytecode and `__pycache__/` directories.
- Browser profiles, virtual environments, downloads, temporary segments, and
  test temporary directories.

Remove entirely:

- The empty root `__init__.py`.
- The already deleted legacy `项目介绍.md`.

## Reproducible Startup

- Add `requirements.txt` with all Python runtime dependencies, including the
  optional yt-dlp fallback command.
- Add an explicit `tools/__init__.py` package marker.
- Update the README to use `python -m tools.main` from the repository root.
- Configure the application to put the bundled `tools/ffmpeg.exe` directory at
  the front of the child-process `PATH` on Windows, so HLS, DASH, and yt-dlp can
  use the tracked binary without a separate FFmpeg installation.
- Document `playwright install chromium` as the only required post-install
  browser setup step.

## Safety

- Do not revert or overwrite existing source and test changes.
- Use index-only removal for IDE settings, bytecode, and extra wallpapers so
  local copies remain available.
- Never track `browser_profiles/`; it can contain cookies and site storage.
- Verify ignore behavior, runtime imports, FFmpeg discovery, tests, and the final
  Git index before completion.
