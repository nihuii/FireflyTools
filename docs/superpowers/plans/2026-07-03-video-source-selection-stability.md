# Video Source Selection Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent early advertising MP4 responses from ending webpage sniffing before the main HLS stream appears, then choose fallback MP4 candidates by validated evidence rather than discovery order.

**Architecture:** Keep Playwright navigation on `domcontentloaded`, but make the explicit observation loop terminal only for network HLS or the configured deadline. Normalize duplicate candidates before the spider groups them by type. When HLS is absent, use a small HTTP metadata probe to rank MP4 candidates while requiring response-body-only MP4 URLs to be validated.

**Tech Stack:** Python 3.10+, Playwright sync API, requests, unittest, unittest.mock

---

## Working-Tree Constraint

The four implementation files already contain uncommitted Chinese documentation
changes owned by the user. Preserve those hunks exactly and edit on top of them.
Do not stage or commit implementation files during this plan; use diff and test
checkpoints instead so the existing comment work is not accidentally bundled.

### Task 1: Type-aware wait policy and candidate normalization

**Files:**
- Modify: `tests/test_video_crawler_sniffer_access.py:103-161`
- Modify: `tools/video_crawler/sniffer.py:170-190`
- Modify: `tools/video_crawler/sniffer.py:298-434`

- [ ] **Step 1: Write failing wait-policy and deduplication tests**

Replace the broad “network candidate is reliable” expectation with explicit
terminal-HLS behavior and add deduplication coverage:

```python
def test_network_mp4_keeps_waiting_before_limit(self):
    candidates = [
        MediaCandidate(
            url="https://cdn.example.test/ad.mp4",
            kind=MediaKind.DIRECT_MP4,
            source="network",
        )
    ]
    self.assertTrue(
        should_continue_waiting_for_media(candidates, 1, 10)
    )

def test_network_hls_stops_waiting(self):
    candidates = [
        MediaCandidate(
            url="https://cdn.example.test/main.m3u8",
            kind=MediaKind.HLS,
            source="network",
        )
    ]
    self.assertFalse(
        should_continue_waiting_for_media(candidates, 1, 10)
    )

def test_deduplicate_upgrades_response_body_candidate_to_network(self):
    url = "https://cdn.example.test/ad.mp4"
    result = deduplicate_media_candidates([
        MediaCandidate(url=url, kind=MediaKind.DIRECT_MP4,
                       source="response-body", score=70),
        MediaCandidate(url=url, kind=MediaKind.DIRECT_MP4,
                       source="network", score=75,
                       content_type="video/mp4"),
    ])
    self.assertEqual(len(result), 1)
    self.assertEqual(result[0].source, "network")
    self.assertEqual(result[0].content_type, "video/mp4")
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m unittest tests.test_video_crawler_sniffer_access.MediaWaitPolicyTests tests.test_video_crawler_sniffer_access.MediaCandidateDeduplicationTests -v
```

Expected: FAIL because the current helper accepts a candidate count, a network
MP4 stops waiting, and `deduplicate_media_candidates` does not exist.

- [ ] **Step 3: Implement the minimal wait and deduplication helpers**

In `sniffer.py`, implement:

```python
def has_terminal_media_candidate(candidates: list[MediaCandidate]) -> bool:
    return any(
        candidate.source == "network" and candidate.kind == MediaKind.HLS
        for candidate in candidates
    )

def should_continue_waiting_for_media(
    candidates: list[MediaCandidate],
    elapsed_seconds: float,
    limit_seconds: int,
    visible: bool = False,
) -> bool:
    if elapsed_seconds >= limit_seconds:
        return False
    return not has_terminal_media_candidate(candidates)

def deduplicate_media_candidates(
    candidates: list[MediaCandidate],
) -> list[MediaCandidate]:
    unique: list[MediaCandidate] = []
    positions: dict[str, int] = {}
    for candidate in candidates:
        position = positions.get(candidate.url)
        if position is None:
            positions[candidate.url] = len(unique)
            unique.append(candidate)
            continue
        current = unique[position]
        if current.source == "response-body" and candidate.source == "network":
            unique[position] = candidate
        elif candidate.score > current.score:
            unique[position] = candidate
    return unique
```

Update `wait_for_candidates()` to pass the candidate list, log the stop reason,
and normalize candidates before constructing `DiagnosticReport`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2 plus:

```powershell
python -m unittest tests.test_video_crawler_diagnostics tests.test_video_crawler_sniffer_access -v
```

Expected: all tests pass.

- [ ] **Step 5: Review the diff checkpoint**

Run:

```powershell
git diff --check -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
git diff -- tools/video_crawler/sniffer.py tests/test_video_crawler_sniffer_access.py
```

Expected: only the pre-existing Chinese comments plus the tested wait and
normalization changes; no unrelated edits.

### Task 2: MP4 metadata validation and order-independent selection

**Files:**
- Modify: `tests/test_video_downloader.py:329-419`
- Modify: `tools/video_crawler/spider.py:391-453`

- [ ] **Step 1: Write failing MP4-selection tests**

Add tests that call the desired helper directly:

```python
def test_select_best_mp4_prefers_larger_validated_network_candidate(self):
    spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
    candidates = [
        MediaCandidate("https://cdn.test/ad.mp4", MediaKind.DIRECT_MP4,
                       "network", score=75),
        MediaCandidate("https://cdn.test/main.mp4", MediaKind.DIRECT_MP4,
                       "network", score=75),
    ]
    with patch.object(spider, "_probe_mp4_size", side_effect=[1_000, 50_000]):
        selected = spider._select_best_mp4(candidates)
    self.assertEqual(selected.url, "https://cdn.test/main.mp4")

def test_select_best_mp4_rejects_unverified_response_body_only_candidate(self):
    spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
    candidate = MediaCandidate(
        "https://cdn.test/ad.mp4", MediaKind.DIRECT_MP4,
        "response-body", score=70,
    )
    with patch.object(spider, "_probe_mp4_size", return_value=None):
        self.assertIsNone(spider._select_best_mp4([candidate]))

def test_sniff_selection_is_independent_of_dash_and_mp4_discovery_order(self):
    spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
    report = DiagnosticReport(source_url="https://site.test/watch", candidates=[
        MediaCandidate("https://cdn.test/stream.mpd", MediaKind.DASH,
                       "network", score=65),
        MediaCandidate("https://cdn.test/ad.mp4", MediaKind.DIRECT_MP4,
                       "response-body", score=70),
        MediaCandidate("https://cdn.test/main.mp4", MediaKind.DIRECT_MP4,
                       "network", score=75),
    ])
    sizes = {
        "https://cdn.test/ad.mp4": 1_000,
        "https://cdn.test/main.mp4": 50_000,
    }
    with patch("tools.video_crawler.spider.PageSniffer") as sniffer_class:
        sniffer_class.return_value.sniff.return_value = report
        with patch.object(
            spider,
            "_probe_mp4_size",
            side_effect=lambda url: sizes.get(url),
        ):
            result = spider._sniff_real_url("https://site.test/watch")
    self.assertEqual(result, "https://cdn.test/main.mp4")
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests.test_select_best_mp4_prefers_larger_validated_network_candidate tests.test_video_downloader.UniversalVideoSpiderTests.test_select_best_mp4_rejects_unverified_response_body_only_candidate tests.test_video_downloader.UniversalVideoSpiderTests.test_sniff_selection_is_independent_of_dash_and_mp4_discovery_order -v
```

Expected: FAIL with missing `_select_best_mp4` and `_probe_mp4_size`, and the
current discovery-order loop chooses DASH or the response-body MP4.

- [ ] **Step 3: Implement metadata size parsing and MP4 ranking**

Add a pure `response_total_size(response)` helper that parses the total after
`/` in `Content-Range`, then falls back to positive `Content-Length`.

Add `UniversalVideoSpider._probe_mp4_size(url)`:

```python
headers = build_download_headers(self.headers, self.session_snapshot, url)
with requests.head(
    url, headers=headers, allow_redirects=True, timeout=5
) as response:
    response.raise_for_status()
    size = response_total_size(response)
    if size:
        return size
range_headers = dict(headers)
range_headers["Range"] = "bytes=0-0"
with requests.get(url, headers=range_headers, stream=True,
                  allow_redirects=True, timeout=5) as response:
    response.raise_for_status()
    return response_total_size(response)
```

Catch request failures, log them, and return `None`. Add
`_select_best_mp4(candidates)` that probes each candidate, rejects an
unverified response-body-only candidate, and ranks by network source, successful
probe, size, score, and inverse discovery index.

Normalize and group report candidates in `_sniff_real_url()`. Keep the existing
HLS block first, then call `_select_best_mp4()`, then choose the best DASH by
source and score. Preserve structured HLS failures without MP4 fallback.

- [ ] **Step 4: Add HTTP metadata fallback tests**

Add this small response fake near the existing `FakeResponse`:

```python
class FakeMetadataResponse:
    def __init__(self, headers):
        self.headers = headers

    def raise_for_status(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False
```

Add the metadata tests:

```python
def test_probe_mp4_size_uses_head_content_length_without_get(self):
    spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
    with patch(
        "tools.video_crawler.spider.requests.head",
        return_value=FakeMetadataResponse({"Content-Length": "50000"}),
    ):
        with patch("tools.video_crawler.spider.requests.get") as get:
            size = spider._probe_mp4_size("https://cdn.test/main.mp4")
    self.assertEqual(size, 50_000)
    get.assert_not_called()

def test_probe_mp4_size_falls_back_to_range_get(self):
    spider = UniversalVideoSpider(output_dir="downloads", temp_dir="temp")
    with patch(
        "tools.video_crawler.spider.requests.head",
        return_value=FakeMetadataResponse({}),
    ):
        with patch(
            "tools.video_crawler.spider.requests.get",
            return_value=FakeMetadataResponse(
                {"Content-Range": "bytes 0-0/50000"}
            ),
        ) as get:
            size = spider._probe_mp4_size("https://cdn.test/main.mp4")
    self.assertEqual(size, 50_000)
    self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=0-0")
    self.assertTrue(get.call_args.kwargs["stream"])
```

Run:

```powershell
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests.test_probe_mp4_size_uses_head_content_length_without_get tests.test_video_downloader.UniversalVideoSpiderTests.test_probe_mp4_size_falls_back_to_range_get -v
```

Expected: both tests pass after Step 3.

- [ ] **Step 5: Run focused spider tests and verify GREEN**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests -v
```

Expected: all spider tests pass.

- [ ] **Step 6: Review the diff checkpoint**

Run `git diff --check` and `git diff` for `spider.py` and
`test_video_downloader.py`. Confirm existing comment changes remain present and
all new behavior is covered by tests.

### Task 3: Regression verification and live diagnosis

**Files:**
- Verify: `tools/video_crawler/sniffer.py`
- Verify: `tools/video_crawler/spider.py`
- Verify: `tests/test_video_crawler_sniffer_access.py`
- Verify: `tests/test_video_downloader.py`

- [ ] **Step 1: Run the combined focused suite**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_crawler_diagnostics tests.test_video_crawler_sniffer_access tests.test_video_downloader -v
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the full suite**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected: all tests pass. If the known `tests/.tmp` permission residue occurs,
rerun outside the sandbox and report it separately from business-code results.

- [ ] **Step 3: Run the live no-download regression check**

Invoke `PageSniffer` for the supplied KanAV page with a 10-second wait and print
normalized candidates. Do not download video data.

Expected: when the page emits the known HLS request within the budget, the
candidate list includes `cdn16.11yun.space/GAV1/328752/328752.m3u8`, duplicate
MP4 URLs are collapsed, and the wait log states why observation ended.

- [ ] **Step 4: Perform final safety review**

Run:

```powershell
git diff --check
git status --short --branch -uall
```

Confirm no unknown file was deleted, no browser profile or download output is
tracked, and the implementation is limited to the approved files.
