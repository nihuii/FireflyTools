# Repository Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a clean GitHub repository that retains all wallpapers and the bundled Windows FFmpeg, excludes local/generated data, and starts after documented environment setup.

**Architecture:** Keep runtime source under the explicit `tools` package, development material under `docs` and `tests`, and machine-specific state outside the Git index through `.gitignore`. Package initialization will prepend the bundled FFmpeg directory to `PATH`, allowing every existing subprocess call to keep using the `ffmpeg` command without adapter-specific changes.

**Tech Stack:** Python 3.10+, PyQt6, unittest, Playwright, Git, PowerShell

---

### Task 1: Make Bundled FFmpeg Discoverable

**Files:**
- Create: `tools/runtime_setup.py`
- Create: `tools/__init__.py`
- Create: `tests/test_runtime_setup.py`

- [ ] **Step 1: Write the failing runtime setup test**

```python
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools.runtime_setup import configure_bundled_ffmpeg


class RuntimeSetupTests(TestCase):
    def test_configure_bundled_ffmpeg_prepends_tools_directory(self):
        tools_dir = Path(__file__).resolve().parents[1] / "tools"
        with patch.dict(os.environ, {"PATH": r"C:\Windows\System32"}):
            executable = configure_bundled_ffmpeg(tools_dir)

            self.assertEqual(executable, tools_dir / "ffmpeg.exe")
            self.assertEqual(
                Path(os.environ["PATH"].split(os.pathsep)[0]),
                tools_dir,
            )
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_runtime_setup -v
```

Expected: import error because `tools.runtime_setup` does not exist.

- [ ] **Step 3: Add minimal FFmpeg environment setup**

Create `tools/runtime_setup.py`:

```python
import os
from pathlib import Path


def configure_bundled_ffmpeg(tools_dir=None):
    tools_path = Path(tools_dir or Path(__file__).resolve().parent).resolve()
    executable = tools_path / "ffmpeg.exe"
    if os.name != "nt" or not executable.is_file():
        return None

    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    normalized_tools = os.path.normcase(str(tools_path))
    remaining = [
        entry
        for entry in path_entries
        if entry and os.path.normcase(os.path.abspath(entry)) != normalized_tools
    ]
    os.environ["PATH"] = os.pathsep.join([str(tools_path), *remaining])
    return executable
```

Create `tools/__init__.py`:

```python
from tools.runtime_setup import configure_bundled_ffmpeg


BUNDLED_FFMPEG = configure_bundled_ffmpeg()
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_runtime_setup -v
```

Expected: `Ran 1 test` and `OK`.

- [ ] **Step 5: Commit only runtime setup files**

```powershell
git add -- tools/runtime_setup.py tools/__init__.py tests/test_runtime_setup.py
git commit -m "build: configure bundled ffmpeg"
```

### Task 2: Add Repository Metadata and Ignore Rules

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `requirements.txt`
- Create: `LICENSE`

- [ ] **Step 1: Replace the minimal ignore file with repository-safe rules**

Use these categories in `.gitignore`:

```gitignore
# Python bytecode and tooling
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# Virtual environments and local environment files
.venv/
venv/
env/
.env
.env.*

# IDE and agent-local state
.idea/
.vscode/
.agents/
.worktrees/

# Runtime output and resumable download state
downloads/
temp/
browser_profiles/
*.part
*.tmp
.firefly-segments.json*

# Test scratch data
tests/.tmp/

# OS metadata
.DS_Store
Thumbs.db
Desktop.ini
```

Do not ignore `pic/` or `tools/ffmpeg.exe`.

- [ ] **Step 2: Add installable dependency metadata**

Create `requirements.txt`:

```text
PyQt6>=6.6,<7
Pillow>=10,<13
requests>=2.31,<3
aiohttp>=3.9,<4
m3u8>=6,<7
cryptography>=42,<47
playwright>=1.40,<2
yt-dlp
```

- [ ] **Step 3: Add the MIT license already advertised by README**

Create `LICENSE` with the standard MIT text and:

```text
Copyright (c) 2026 FireflyTools contributors
```

- [ ] **Step 4: Rewrite README setup and structure sections**

Document exactly:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
playwright install chromium
python -m tools.main
```

State Python 3.10+, bundled Windows FFmpeg behavior, PATH fallback on non-Windows,
all-wallpaper retention, output directory behavior, and the optional yt-dlp fallback.

- [ ] **Step 5: Validate metadata**

Run:

```powershell
python -m pip install --dry-run -r requirements.txt
git check-ignore -v tools/ffmpeg.exe pic/流萤-140834037.png
```

Expected: dependency resolution succeeds; `git check-ignore` prints nothing for
FFmpeg and the wallpaper.

- [ ] **Step 6: Commit metadata files**

```powershell
git add -- .gitignore README.md requirements.txt LICENSE
git commit -m "build: add reproducible project setup"
```

### Task 3: Archive Development Documentation

**Files:**
- Move: `plan/*.md` to `docs/plans/*.md`
- Move: `项目介绍_新版.md` to `docs/project-overview.md`
- Modify: `docs/project-overview.md`
- Modify: `README.md`

- [ ] **Step 1: Create the documentation plan directory**

```powershell
New-Item -ItemType Directory -Path docs\plans -Force
```

- [ ] **Step 2: Move each plan without touching its content**

```powershell
Move-Item -LiteralPath plan\video-crawler-roadmap.md -Destination docs\plans\video-crawler-roadmap.md
Move-Item -LiteralPath plan\video-crawler-phased-implementation-plan.md -Destination docs\plans\video-crawler-phased-implementation-plan.md
Move-Item -LiteralPath plan\video-crawler-gap-closure-implementation-plan.md -Destination docs\plans\video-crawler-gap-closure-implementation-plan.md
Move-Item -LiteralPath plan\video-crawler-access-limited-sniffing-implementation-plan.md -Destination docs\plans\video-crawler-access-limited-sniffing-implementation-plan.md
```

- [ ] **Step 3: Move the current project handoff document**

```powershell
Move-Item -LiteralPath 项目介绍_新版.md -Destination docs\project-overview.md
```

- [ ] **Step 4: Update paths and current-state wording**

In `docs/project-overview.md`, replace every `plan/` path with `docs/plans/`,
update the tree to show `docs/project-overview.md` and `docs/plans/`, and remove
the stale statement that the access-limited sniffing phases are unimplemented.
Add README links to the project overview and implementation-plan archive.

- [ ] **Step 5: Verify no obsolete paths remain**

Run:

```powershell
rg -n "(^|`)plan/|项目介绍_新版\.md" README.md docs
```

Expected: no obsolete root paths; references point to `docs/plans/` and
`docs/project-overview.md`.

- [ ] **Step 6: Commit documentation moves only**

```powershell
git add -- README.md docs/project-overview.md docs/plans plan 项目介绍_新版.md
git commit -m "docs: organize project documentation"
```

### Task 4: Remove Generated and Machine-Specific Files from Git

**Files:**
- Untrack: `.idea/**`
- Untrack: every tracked `__pycache__/**` and `*.pyc`
- Delete: root `__init__.py`
- Preserve deletion: `项目介绍.md`
- Preserve in Git: `pic/**`, `tools/ffmpeg.exe`, `tests/**/*.py`

- [ ] **Step 1: Remove IDE settings from the index only**

```powershell
git rm -r --cached --ignore-unmatch -- .idea
```

- [ ] **Step 2: Remove every tracked Python cache from the index only**

```powershell
git ls-files | Where-Object { $_ -match '(^|/)__pycache__/' -or $_ -match '\.py[co]$' } | ForEach-Object { git rm --cached --ignore-unmatch -- $_ }
```

- [ ] **Step 3: Remove obsolete root package marker and stage legacy-doc deletion**

```powershell
git rm --ignore-unmatch -- __init__.py 项目介绍.md
```

- [ ] **Step 4: Audit the index before committing**

Run:

```powershell
git diff --cached --name-status
```

Expected: only `.idea`, cache files, root `__init__.py`, and the already deleted
legacy project introduction are removed in this cleanup commit. No `pic/` or
`tools/ffmpeg.exe` deletion appears.

- [ ] **Step 5: Commit index cleanup**

```powershell
git commit -m "chore: stop tracking generated files"
```

### Task 5: Verify Fresh-Checkout Readiness

**Files:**
- Verify only

- [ ] **Step 1: Verify required files are tracked and private/generated files are not**

Run:

```powershell
git ls-files tools/ffmpeg.exe pic tests tools docs README.md requirements.txt LICENSE
git ls-files | Select-String -Pattern '(^|/)__pycache__/|\.py[co]$|^\.idea/|browser_profiles|(^|/)temp/|(^|/)downloads/'
```

Expected: FFmpeg, all eight wallpapers, source, tests, and docs are listed; the
second command prints nothing.

- [ ] **Step 2: Verify ignore behavior for representative generated paths**

Run:

```powershell
git check-ignore -v tools/browser_profiles/video_crawler/Default/Preferences tools/temp/sample.ts tests/.tmp/sample tools/__pycache__/main.pyc .venv/pyvenv.cfg
```

Expected: every path is matched by `.gitignore`.

- [ ] **Step 3: Verify package import and bundled FFmpeg discovery**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -c "import shutil; import tools.main; print(shutil.which('ffmpeg'))"
```

Expected: import succeeds and prints the absolute path to `tools/ffmpeg.exe`.

- [ ] **Step 4: Run the complete test suite**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected: all tests pass. If sandbox permissions prevent cleanup under
`tests/.tmp`, rerun the same command with approved elevated filesystem access.

- [ ] **Step 5: Inspect final status without changing unrelated work**

Run:

```powershell
git status --short --branch
git diff --check
```

Expected: no generated files are tracked or shown as untracked. Existing source
and test modifications from the video-crawler work remain untouched unless they
were explicitly part of this plan.
