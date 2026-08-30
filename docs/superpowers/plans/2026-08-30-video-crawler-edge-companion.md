# Video Crawler Edge Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a user-authorized Microsoft Edge companion that captures HLS, MP4, and DASH requests in an ordinary Edge tab, imports a sanitized candidate into FireflyTools, and reuses the existing downloader without Playwright visiting the protected page.

**Architecture:** Deliver one protocol through two transport stages. Stage 1 proves the target-site path with an Edge Manifest V3 extension plus clipboard JSON import; Stage 2 keeps the same candidate model and confirmation UI, then adds a per-user Native Messaging host and a token-authenticated `127.0.0.1` receiver. The extension observes only a user-selected tab, stores no Cookie or Authorization data, and never starts a download without a second confirmation in FireflyTools.

**Tech Stack:** Python 3.13, PyQt6, Python stdlib HTTP/JSON/winreg, Microsoft Edge Manifest V3, JavaScript, Node.js built-in test runner, unittest, unittest.mock, Playwright only for existing regression tests

---

## Working-Tree and Delivery Constraints

Implement in the ignored worktree:

```text
D:\Study\Projects\PythonProject\FireflyTools\.worktrees\video-crawler-edge-companion
```

Branch:

```text
codex/video-crawler-edge-companion-plan
```

Baseline at plan creation: `2da3079`; full Python suite: 247 tests passed, one Windows symlink-permission test skipped. Do not modify, stage, delete, or copy the main worktree's untracked `pic_test/` images.

Use the checked-in development public key below. It fixes the unpacked extension ID at `applbmkghgaoadhmmcdnbmebgideiefg`; the key is public and is not a signing secret. No private key belongs in Git.

```text
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsbFUu9s0WkJ5Y2jA03jaUT0lIR2II3dQ6w8Y52XB16224XEmVtzC7T28M8SbptzXNPSCVgeDGBo5FTukrB172AG/5PyaiVK0BLAykUA6xtgYfC+NYBl6IVeRQtWALTpYZbhsFmPlROCG9MzgAWSyAgyEdZTOV8N1fOK/iQYCoiBr7GBFzIejsoEs3IT4KU6DvhM6yTS8mtGYxtEl/KXdtJvtBreooVT8uFj6s+xXln9imEf8N3zZ9kl2IGklmleQozYgRbOOPsVbyv9UI5yqlYu5oVueQT+6l2pS+wn2r7uSaxKavAo2Z/gJ6fhyyuYUUF0JpFRtBrQLnIeKyKN7tQIDAQAB
```

## File Responsibility Map

### Edge extension

- Create `browser_extensions/edge_video_capture/manifest.json`: permissions, stable development ID, popup, and service worker declaration.
- Create `browser_extensions/edge_video_capture/candidate_detector.js`: pure URL/MIME classification, safe-header selection, URL redaction, and ad rejection.
- Create `browser_extensions/edge_video_capture/capture_store.js`: pure five-minute session state, deduplication, ranking, and 50-item cap.
- Create `browser_extensions/edge_video_capture/capture_controller.js`: maps `webRequest` events to the pure detector/store without browser-global state.
- Create `browser_extensions/edge_video_capture/service_worker.js`: Edge API wiring only; observational listeners, storage.session persistence, alarm cleanup, and tab cleanup.
- Create `browser_extensions/edge_video_capture/native_client.js`: Native Messaging port, request/ack correlation, timeout, and disconnect errors.
- Create `browser_extensions/edge_video_capture/popup_model.js`: pure display rows and native-error-to-Chinese-message mapping.
- Create `browser_extensions/edge_video_capture/popup.html`, `popup.css`, and `popup.js`: explicit start/stop, candidate choice, send, and copy actions.
- Create `browser_extensions/edge_video_capture/package.json` and `browser_extensions/edge_video_capture/tests/*.test.js`: dependency-free Node tests for pure JavaScript modules.
- Create `browser_extensions/edge_video_capture/README.md`: sideload, permissions, stage-one clipboard, stage-two connector, and uninstall instructions.

### Python protocol, transport, and UI

- Create `tools/edge_companion/__init__.py`: stable public exports only.
- Create `tools/edge_companion/protocol.py`: V1 schema parsing/serialization, size limits, URL/header validation, expiry, and structured acknowledgements.
- Create `tools/edge_companion/ui.py`: candidate confirmation dialog; never displays raw sensitive query values or header values.
- Create `tools/edge_companion/runtime.py`: runtime descriptor path, atomic file I/O, expiry, and PID liveness checks without importing Qt.
- Create `tools/edge_companion/receiver.py`: loopback-only HTTP receiver, bearer-token authentication, wait gate, backoff, and Qt signals.
- Create `tools/edge_companion/native_host.py`: Native Messaging length framing, caller-origin validation, runtime discovery, and loopback forwarding.
- Create `tools/edge_companion/install.py`: explicit current-user install/status/uninstall CLI and native-host manifest generation.
- Create `pyproject.toml`: editable-install metadata and the `fireflytools-edge-host` console-script executable.

### Existing integration files

- Modify `tools/video_crawler/models.py`: immutable Edge candidate and request-context models.
- Modify `tools/video_crawler/errors.py`: Edge candidate invalid/expired error codes.
- Modify `tools/video_crawler/session.py`: merge only the V1 Edge header allowlist into download requests.
- Modify `tools/video_crawler/spider.py`: apply a supplied Edge request context to direct HLS/MP4/DASH candidates.
- Modify `tools/video_downloader.py`: Edge status area, paste/wait controls, confirmation, exclusive input mode, task snapshot, and worker reconstruction.
- Modify `tools/main.py`: receiver start, injection into the video page, and clean shutdown.
- Modify `docs/项目介绍.md`: installation, privacy boundary, workflow, troubleshooting, and limitations.

### Tests

- Create `tests/edge_companion_fixtures.py`.
- Create `tests/test_edge_companion_protocol.py`.
- Create `tests/test_edge_extension_contract.py`.
- Create `tests/test_edge_companion_receiver.py`.
- Create `tests/test_edge_companion_native_host.py`.
- Create `tests/test_edge_companion_install.py`.
- Modify `tests/test_video_crawler_models.py`, `tests/test_video_crawler_session.py`, `tests/test_video_downloader.py`, `tests/test_video_crawler_adapters.py`, and `tests/test_main_window.py`.

---

## Stage 1 — Clipboard Minimum Viable Loop

### Task 1: Define the V1 Edge candidate model and strict protocol parser

**Files:**
- Create: `tools/edge_companion/__init__.py`
- Create: `tools/edge_companion/protocol.py`
- Modify: `tools/video_crawler/models.py:8-66`
- Modify: `tools/video_crawler/errors.py:6-19`
- Create: `tests/edge_companion_fixtures.py`
- Create: `tests/test_edge_companion_protocol.py`
- Modify: `tests/test_video_crawler_models.py:33-70`

- [ ] **Step 1: Write failing model and parser tests**

Create a single valid fixture builder and tests that pin the public API:

```python
from datetime import datetime, timezone
import json
import unittest

from tests.edge_companion_fixtures import valid_edge_message
from tools.edge_companion.protocol import (
    EdgeProtocolError,
    candidate_from_task_payload,
    parse_candidate_json,
    serialize_candidate,
)
from tools.video_crawler.models import MediaKind
```

Create the reusable fixture in `tests/edge_companion_fixtures.py` so later UI and transport tests do not invent a second schema:

```python
def valid_edge_message():
    return {
        "protocol_version": 1,
        "type": "media_candidate",
        "request_id": "4e8ad6ef-94d5-4f53-8d30-2ac148183e3d",
        "captured_at": "2026-08-30T12:00:00Z",
        "page": {
            "url": "https://example.test/watch/1",
            "title": "Example",
        },
        "candidate": {
            "url": "https://cdn.example.test/master.m3u8?token=opaque",
            "kind": "hls",
            "content_type": "application/vnd.apple.mpegurl",
            "method": "GET",
            "headers": {
                "Referer": "https://example.test/",
                "Origin": "https://example.test",
                "User-Agent": "Edge UA",
                "Accept-Language": "zh-CN",
            },
        },
        "sensitive_headers_included": False,
    }
```

Then place the test class in `tests/test_edge_companion_protocol.py` below the imports shown above:

```python
class EdgeCompanionProtocolTests(unittest.TestCase):
    def test_valid_v1_message_round_trips_without_sensitive_headers(self):
        candidate = parse_candidate_json(json.dumps(valid_edge_message()))
        self.assertEqual(candidate.kind, MediaKind.HLS)
        self.assertEqual(candidate.media_url, valid_edge_message()["candidate"]["url"])
        self.assertEqual(candidate.headers["User-Agent"], "Edge UA")
        self.assertEqual(serialize_candidate(candidate), valid_edge_message())

    def test_rejects_sensitive_or_injected_headers(self):
        for name, value in (
            ("Cookie", "sid=secret"),
            ("Authorization", "Bearer secret"),
            ("Referer", "https://example.test/\r\nX-Evil: 1"),
        ):
            message = valid_edge_message()
            message["candidate"]["headers"] = {name: value}
            with self.subTest(name=name):
                with self.assertRaises(EdgeProtocolError):
                    parse_candidate_json(json.dumps(message))

    def test_rejects_non_http_url_wrong_version_and_oversized_json(self):
        message = valid_edge_message()
        message["candidate"]["url"] = "blob:https://example.test/id"
        with self.assertRaises(EdgeProtocolError):
            parse_candidate_json(json.dumps(message))
        message = valid_edge_message()
        message["protocol_version"] = 2
        with self.assertRaises(EdgeProtocolError):
            parse_candidate_json(json.dumps(message))
        with self.assertRaises(EdgeProtocolError):
            parse_candidate_json("x" * (256 * 1024 + 1))

    def test_task_payload_is_revalidated_and_expiry_is_reported(self):
        candidate = parse_candidate_json(json.dumps(valid_edge_message()))
        task_payload = serialize_candidate(candidate)
        restored = candidate_from_task_payload(task_payload)
        self.assertFalse(
            restored.is_expired(datetime(2026, 8, 30, 12, 4, 59, tzinfo=timezone.utc))
        )
        self.assertTrue(
            restored.is_expired(datetime(2026, 8, 30, 12, 5, 1, tzinfo=timezone.utc))
        )
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
python -m unittest tests.test_edge_companion_protocol tests.test_video_crawler_models -v
```

Expected: import failures for `tools.edge_companion` and missing Edge model/error definitions.

- [ ] **Step 3: Add the model and parser with fixed limits**

Add this model shape to `tools/video_crawler/models.py`:

```python
from datetime import datetime, timedelta, timezone

EDGE_CAPTURE_TTL_SECONDS = 300


@dataclass(frozen=True)
class EdgeCaptureCandidate:
    request_id: str
    captured_at: datetime
    page_url: str
    page_title: str
    media_url: str
    kind: MediaKind
    content_type: str
    method: str
    headers: dict[str, str] = field(default_factory=dict)
    protocol_version: int = 1

    @property
    def expires_at(self) -> datetime:
        return self.captured_at + timedelta(seconds=EDGE_CAPTURE_TTL_SECONDS)

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return current >= self.expires_at

    def to_session_snapshot(self) -> BrowserSessionSnapshot:
        lowered = {name.lower(): value for name, value in self.headers.items()}
        return BrowserSessionSnapshot(
            user_agent=lowered.get("user-agent", ""),
            referer=lowered.get("referer", self.page_url),
            origin=lowered.get("origin", ""),
            cookies=(),
            headers={
                name: value
                for name, value in self.headers.items()
                if name.lower() in {"accept", "accept-language", "range"}
            },
            local_storage={},
        )
```

In `tools/edge_companion/protocol.py`, define and use these exact boundaries:

```python
PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 256 * 1024
MAX_URL_CHARS = 16 * 1024
MAX_TITLE_CHARS = 512
ALLOWED_METHODS = {"GET", "HEAD"}
ALLOWED_HEADER_NAMES = {
    "referer": "Referer",
    "origin": "Origin",
    "user-agent": "User-Agent",
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "range": "Range",
}
SENSITIVE_HEADER_NAMES = {"cookie", "authorization", "proxy-authorization"}


class EdgeProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require_http_url(value: object, field_name: str) -> str:
    from urllib.parse import urlsplit

    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS:
        raise EdgeProtocolError("INVALID_URL", f"{field_name} 不是有效 URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EdgeProtocolError("INVALID_URL", f"{field_name} 只允许 HTTP(S)")
    return value


def _safe_headers(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise EdgeProtocolError("INVALID_HEADERS", "headers 必须是对象")
    safe = {}
    for name, value in raw.items():
        lowered = str(name).lower()
        if lowered in SENSITIVE_HEADER_NAMES or lowered not in ALLOWED_HEADER_NAMES:
            raise EdgeProtocolError("INVALID_HEADERS", f"不允许的 Header: {name}")
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise EdgeProtocolError("INVALID_HEADERS", f"Header 值无效: {name}")
        safe[ALLOWED_HEADER_NAMES[lowered]] = value
    return safe
```

`parse_candidate_json`, `serialize_candidate`, and `candidate_from_task_payload` must all call the same private `parse_candidate_mapping`; there must not be a trusted task-only bypass. Parse `captured_at` as an offset-aware UTC `datetime`, map `hls/direct_mp4/dash` to `MediaKind`, require `sensitive_headers_included is False`, and reject any unknown top-level `type`.

Add `EDGE_CANDIDATE_INVALID` and `EDGE_CANDIDATE_EXPIRED` to `VideoErrorCode`.

- [ ] **Step 4: Run protocol/model tests and verify GREEN**

```powershell
python -m unittest tests.test_edge_companion_protocol tests.test_video_crawler_models -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit the protocol boundary**

```powershell
git add tools/edge_companion/__init__.py tools/edge_companion/protocol.py tools/video_crawler/models.py tools/video_crawler/errors.py tests/edge_companion_fixtures.py tests/test_edge_companion_protocol.py tests/test_video_crawler_models.py
git commit -m "feat(edge-capture): 定义安全候选协议"
```

### Task 2: Build the pure extension detector and bounded capture store

**Files:**
- Create: `browser_extensions/edge_video_capture/manifest.json`
- Create: `browser_extensions/edge_video_capture/candidate_detector.js`
- Create: `browser_extensions/edge_video_capture/capture_store.js`
- Create: `browser_extensions/edge_video_capture/package.json`
- Create: `browser_extensions/edge_video_capture/tests/candidate_detector.test.js`
- Create: `browser_extensions/edge_video_capture/tests/capture_store.test.js`
- Create: `tests/test_edge_extension_contract.py`

- [ ] **Step 1: Write failing JavaScript and manifest-contract tests**

Use Node's built-in `node:test`; do not add npm dependencies. Pin these behaviors:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const detector = require("../candidate_detector.js");

test("classifies HLS, MP4, and DASH from URL or MIME", () => {
  assert.equal(detector.detectKind({url: "https://cdn.test/a.m3u8", contentType: ""}), "hls");
  assert.equal(detector.detectKind({url: "https://cdn.test/a", contentType: "video/mp4"}), "direct_mp4");
  assert.equal(detector.detectKind({url: "https://cdn.test/a", contentType: "application/dash+xml"}), "dash");
});

test("rejects unsafe schemes, ads, and unsupported resources", () => {
  assert.equal(detector.detectKind({url: "blob:https://site.test/id", contentType: "video/mp4"}), null);
  assert.equal(detector.detectKind({url: "https://doubleclick.test/ad.mp4", contentType: "video/mp4"}), null);
  assert.equal(detector.detectKind({url: "https://cdn.test/logo.png", contentType: "image/png"}), null);
});

test("keeps only V1 safe request headers", () => {
  const headers = detector.sanitizeRequestHeaders([
    {name: "Referer", value: "https://site.test/"},
    {name: "Cookie", value: "sid=secret"},
    {name: "Authorization", value: "Bearer secret"},
  ]);
  assert.deepEqual(headers, {Referer: "https://site.test/"});
});
```

Test `capture_store.js` with a fixed clock: only one selected `tabId` is accepted, `(kind, url)` is deduplicated, HLS/DASH outrank MP4 at the 50-item boundary, and `stop`, tab close, or `startedAt + 300000` returns no candidates.

Create `tests/test_edge_extension_contract.py` to load `manifest.json` and assert:

```python
self.assertEqual(manifest["manifest_version"], 3)
self.assertEqual(manifest["incognito"], "not_allowed")
self.assertIn("webRequest", manifest["permissions"])
self.assertIn("alarms", manifest["permissions"])
self.assertIn("clipboardWrite", manifest["permissions"])
self.assertNotIn("cookies", manifest["permissions"])
self.assertNotIn("webRequestBlocking", manifest["permissions"])
self.assertNotIn("content_scripts", manifest)
self.assertEqual(
    manifest["optional_host_permissions"],
    ["http://*/*", "https://*/*"],
)
self.assertEqual(
    manifest["key"],
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsbFUu9s0WkJ5Y2jA03jaUT0lIR2II3dQ6w8Y52XB16224XEmVtzC7T28M8SbptzXNPSCVgeDGBo5FTukrB172AG/5PyaiVK0BLAykUA6xtgYfC+NYBl6IVeRQtWALTpYZbhsFmPlROCG9MzgAWSyAgyEdZTOV8N1fOK/iQYCoiBr7GBFzIejsoEs3IT4KU6DvhM6yTS8mtGYxtEl/KXdtJvtBreooVT8uFj6s+xXln9imEf8N3zZ9kl2IGklmleQozYgRbOOPsVbyv9UI5yqlYu5oVueQT+6l2pS+wn2r7uSaxKavAo2Z/gJ6fhyyuYUUF0JpFRtBrQLnIeKyKN7tQIDAQAB",
)
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
node --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js
python -m unittest tests.test_edge_extension_contract -v
```

Expected: missing-module and missing-manifest failures. If `node` is not on PATH, install a supported Node.js LTS development runtime before implementation; Node is not a FireflyTools runtime dependency.

- [ ] **Step 3: Implement the manifest and pure modules**

Use this manifest permission boundary:

```json
{
  "manifest_version": 3,
  "name": "FireflyTools Edge 视频捕获",
  "version": "0.1.0",
  "description": "在用户授权的当前 Edge 标签页中观察媒体请求并发送给本机 FireflyTools。",
  "minimum_chrome_version": "102",
  "incognito": "not_allowed",
  "key": "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsbFUu9s0WkJ5Y2jA03jaUT0lIR2II3dQ6w8Y52XB16224XEmVtzC7T28M8SbptzXNPSCVgeDGBo5FTukrB172AG/5PyaiVK0BLAykUA6xtgYfC+NYBl6IVeRQtWALTpYZbhsFmPlROCG9MzgAWSyAgyEdZTOV8N1fOK/iQYCoiBr7GBFzIejsoEs3IT4KU6DvhM6yTS8mtGYxtEl/KXdtJvtBreooVT8uFj6s+xXln9imEf8N3zZ9kl2IGklmleQozYgRbOOPsVbyv9UI5yqlYu5oVueQT+6l2pS+wn2r7uSaxKavAo2Z/gJ6fhyyuYUUF0JpFRtBrQLnIeKyKN7tQIDAQAB",
  "permissions": ["activeTab", "alarms", "clipboardWrite", "nativeMessaging", "storage", "webRequest"],
  "optional_host_permissions": ["http://*/*", "https://*/*"],
  "background": {"service_worker": "service_worker.js"},
  "action": {"default_popup": "popup.html", "default_title": "FireflyTools Edge 视频捕获"}
}
```

Use a dependency-free package marker:

```json
{
  "name": "fireflytools-edge-video-capture",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "test": "node --test tests/candidate_detector.test.js tests/capture_store.test.js"
  }
}
```

`candidate_detector.js` must export `detectKind`, `sanitizeRequestHeaders`, `redactUrl`, and `buildCandidate`. `capture_store.js` must export `createSession`, `upsertCandidate`, `stopSession`, `expireSession`, and `listCandidates`. Use a UMD-style footer so the same pure module works in Node and `importScripts`:

```javascript
if (typeof module !== "undefined" && module.exports) {
  module.exports = EdgeCaptureDetector;
} else {
  globalThis.EdgeCaptureDetector = EdgeCaptureDetector;
}
```

The detector may inspect request header objects but must only copy `Referer`, `Origin`, `User-Agent`, `Accept`, `Accept-Language`, and `Range`. Never retain the original header array.

- [ ] **Step 4: Run pure extension tests and verify GREEN**

```powershell
node --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js
python -m unittest tests.test_edge_extension_contract -v
```

Expected: all tests pass without npm install.

- [ ] **Step 5: Commit detector and storage core**

```powershell
git add browser_extensions/edge_video_capture/manifest.json browser_extensions/edge_video_capture/candidate_detector.js browser_extensions/edge_video_capture/capture_store.js browser_extensions/edge_video_capture/package.json browser_extensions/edge_video_capture/tests tests/test_edge_extension_contract.py
git commit -m "feat(edge-capture): 添加受控媒体请求检测器"
```

### Task 3: Wire observational webRequest capture and the clipboard popup

**Files:**
- Create: `browser_extensions/edge_video_capture/capture_controller.js`
- Create: `browser_extensions/edge_video_capture/service_worker.js`
- Create: `browser_extensions/edge_video_capture/popup.html`
- Create: `browser_extensions/edge_video_capture/popup.css`
- Create: `browser_extensions/edge_video_capture/popup_model.js`
- Create: `browser_extensions/edge_video_capture/popup.js`
- Modify: `browser_extensions/edge_video_capture/package.json`
- Create: `browser_extensions/edge_video_capture/tests/capture_controller.test.js`
- Create: `browser_extensions/edge_video_capture/tests/popup_model.test.js`

- [ ] **Step 1: Write failing controller and popup-model tests**

Inject detector, store, and clock dependencies into `createCaptureController`. Assert that requests from a different `tabId` are ignored; request headers and response `Content-Type` join by `requestId`; stopping removes all candidates; and `toProtocolMessage` emits `sensitive_headers_included: false`.

```javascript
function createControllerWithClock(now) {
  return EdgeCaptureController.createCaptureController({
    detector: EdgeCaptureDetector,
    store: EdgeCaptureStore,
    now: () => now,
  });
}

test("controller ignores tabs without an active capture session", () => {
  const controller = createControllerWithClock(1000);
  controller.start({tabId: 7, pageUrl: "https://site.test/watch", pageTitle: "Video"});
  controller.onBeforeRequest({tabId: 8, requestId: "r1", url: "https://cdn.test/a.m3u8", type: "media"});
  assert.deepEqual(controller.list(7), []);
});

test("protocol message excludes sensitive headers", () => {
  const controller = createControllerWithClock(Date.parse("2026-08-30T12:00:00Z"));
  controller.start({tabId: 7, pageUrl: "https://site.test/watch", pageTitle: "Video"});
  controller.onBeforeSendHeaders({
    tabId: 7,
    requestId: "r1",
    url: "https://cdn.test/a.m3u8",
    requestHeaders: [{name: "Cookie", value: "sid=secret"}, {name: "Referer", value: "https://site.test/"}],
  });
  controller.onHeadersReceived({tabId: 7, requestId: "r1", url: "https://cdn.test/a.m3u8", responseHeaders: []});
  const message = controller.toProtocolMessage(7, controller.list(7)[0].id, "uuid-1");
  assert.equal(message.sensitive_headers_included, false);
  assert.deepEqual(message.candidate.headers, {Referer: "https://site.test/"});
});
```

- [ ] **Step 2: Run JavaScript tests and verify RED**

```powershell
node --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js browser_extensions/edge_video_capture/tests/capture_controller.test.js browser_extensions/edge_video_capture/tests/popup_model.test.js
```

Expected: missing `capture_controller.js` and popup-model exports.

- [ ] **Step 3: Implement Edge API wiring without blocking or page injection**

`service_worker.js` must use only observational listeners:

```javascript
importScripts("candidate_detector.js", "capture_store.js", "capture_controller.js");

const controller = EdgeCaptureController.createCaptureController({
  detector: EdgeCaptureDetector,
  store: EdgeCaptureStore,
  now: () => Date.now(),
});

function persistController() {
  return chrome.storage.session.set({
    edgeCaptureState: controller.snapshot(),
  });
}

const ready = chrome.storage.session.get("edgeCaptureState").then(({edgeCaptureState}) => {
  controller.restore(edgeCaptureState || {sessions: {}, pendingRequests: {}});
});

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    void ready.then(() => {
      controller.onBeforeRequest(details);
      return persistController();
    });
  },
  {urls: ["http://*/*", "https://*/*"]}
);
chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    void ready.then(() => {
      controller.onBeforeSendHeaders(details);
      return persistController();
    });
  },
  {urls: ["http://*/*", "https://*/*"]},
  ["requestHeaders", "extraHeaders"]
);
chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    void ready.then(() => {
      controller.onHeadersReceived(details);
      return persistController();
    });
  },
  {urls: ["http://*/*", "https://*/*"]},
  ["responseHeaders"]
);
```

No listener may include `blocking`, return `requestHeaders`, cancel, redirect, inject JavaScript, or modify the DOM. The controller public API is `start`, `stop`, `list`, `onBeforeRequest`, `onBeforeSendHeaders`, `onHeadersReceived`, `toProtocolMessage`, `snapshot`, and `restore`. Persist `snapshot()` in `chrome.storage.session`, not `storage.local`, and call `restore()` before processing the first event after service-worker restart. Create a one-minute `chrome.alarms` cleanup event because MV3 service-worker timers are not durable. Delete a session on `chrome.tabs.onRemoved`.

The popup start handler must call `chrome.permissions.request({origins: ["http://*/*", "https://*/*"]})` directly inside the user's click handler. If denied, show `未授权网页和 CDN 访问权限，捕获未开始。` and do not create a session.

`popup_model.js` exports pure `candidateRow(candidate)` and `userMessage(code)` functions using the same UMD footer as the detector. Render each candidate with type, hostname, redacted URL, and local discovery time. Load `popup_model.js` before `popup.js` from `popup.html`. The copy button serializes exactly one selected protocol message and calls `navigator.clipboard.writeText`. Never copy the controller's internal request state.

Update the package test script to run the four files present at the end of this task:

```json
"test": "node --test tests/candidate_detector.test.js tests/capture_store.test.js tests/capture_controller.test.js tests/popup_model.test.js"
```

- [ ] **Step 4: Run JavaScript tests and static contract tests**

```powershell
node --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js browser_extensions/edge_video_capture/tests/capture_controller.test.js browser_extensions/edge_video_capture/tests/popup_model.test.js
python -m unittest tests.test_edge_extension_contract -v
```

Expected: all tests pass; the manifest remains free of cookies, scripting, content scripts, and blocking webRequest permission.

- [ ] **Step 5: Commit the stage-one Edge extension**

```powershell
git add browser_extensions/edge_video_capture/capture_controller.js browser_extensions/edge_video_capture/service_worker.js browser_extensions/edge_video_capture/popup.html browser_extensions/edge_video_capture/popup.css browser_extensions/edge_video_capture/popup_model.js browser_extensions/edge_video_capture/popup.js browser_extensions/edge_video_capture/package.json browser_extensions/edge_video_capture/tests
git commit -m "feat(edge-capture): 完成 Edge 捕获与剪贴板导出"
```

### Task 4: Add FireflyTools paste, confirmation, and exclusive Edge input mode

**Files:**
- Create: `tools/edge_companion/ui.py`
- Modify: `tools/video_downloader.py:7-20,25-45,118-205,273-358`
- Modify: `tests/test_video_downloader.py:981-1225`

- [ ] **Step 1: Write failing UI tests**

Inject `clipboard_getter`, `edge_dialog_factory`, and `now` into `VideoDownloaderTool` so tests do not touch the real clipboard or show a modal dialog.

```python
from datetime import datetime, timezone
import json

from PyQt6.QtWidgets import QDialog

from tests.edge_companion_fixtures import valid_edge_message


class RecordingEdgeDialog:
    def __init__(self, accepted):
        self.accepted = accepted
        self.candidate = None
        self.shown = False

    def bind(self, candidate):
        self.candidate = candidate
        return self

    def exec(self):
        self.shown = True
        if self.accepted:
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected


def fixed_edge_now():
    return datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc)


def setUp(self):
    self.tool = VideoDownloaderTool(start_worker=False, now=fixed_edge_now)


def test_edge_area_defaults_to_clipboard_ready(self):
    self.assertEqual(self.tool.edge_status_label.text(), "未连接")
    self.assertEqual(self.tool.edge_wait_btn.text(), "等待 Edge 捕获")
    self.assertEqual(self.tool.edge_paste_btn.text(), "粘贴 Edge 候选")

def test_paste_requires_confirmation_before_filling_url(self):
    dialog = RecordingEdgeDialog(accepted=True)
    tool = VideoDownloaderTool(
        start_worker=False,
        clipboard_getter=lambda: json.dumps(valid_edge_message()),
        edge_dialog_factory=lambda candidate, parent: dialog.bind(candidate),
        now=fixed_edge_now,
    )
    tool.paste_edge_candidate()
    self.assertTrue(dialog.shown)
    self.assertEqual(tool.url_entry.text(), valid_edge_message()["candidate"]["url"])
    self.assertFalse(tool.visible_sniff_chk.isEnabled())
    self.assertEqual(tool.edge_status_label.text(), "已收到候选")
    tool.close()

def test_rejected_or_expired_candidate_does_not_change_url(self):
    self.tool.url_entry.setText("https://existing.test/watch")
    with patch.object(self.tool, "_confirm_edge_candidate", return_value=False):
        self.tool._receive_edge_json(json.dumps(valid_edge_message()))
    self.assertEqual(self.tool.url_entry.text(), "https://existing.test/watch")
```

Test that the dialog body contains page origin, media hostname, kind, capture time, and `包含临时请求头: 是/否`, while neither `token=opaque` nor any header value appears.

- [ ] **Step 2: Run affected UI tests and verify RED**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader.VideoDownloaderToolTests -v
```

Expected: missing Edge widgets and injection points.

- [ ] **Step 3: Implement the confirmation dialog and UI state**

Create `EdgeCandidateDialog(QDialog)` in `tools/edge_companion/ui.py`. Build display strings only from `urlsplit(candidate.page_url).hostname`, `urlsplit(candidate.media_url).hostname`, `candidate.kind.value`, the UTC capture time, and a boolean header-presence label. If a redacted URL is shown, run it through `redact_for_display` first.

Add an Edge row below `sniff_options_layout`:

```python
self.edge_status_label = QLabel("未连接")
self.edge_wait_btn = QPushButton("等待 Edge 捕获")
self.edge_paste_btn = QPushButton("粘贴 Edge 候选")
self.edge_wait_btn.clicked.connect(self.toggle_edge_waiting)
self.edge_paste_btn.clicked.connect(self.paste_edge_candidate)
```

Store the confirmed candidate in `self._pending_edge_candidate`. While it is active, disable `visible_sniff_chk`, `persistent_profile_chk`, `system_chrome_chk`, and `sniff_wait_spin`; re-enable them after queueing or when the user clears Edge input. This is the UI enforcement that Edge capture and Playwright sniffing cannot be active for one task.

`paste_edge_candidate` reads text only after the button click, calls `parse_candidate_json`, rejects expired candidates with a Chinese warning, displays the confirmation dialog, and only then changes `url_entry`.

- [ ] **Step 4: Run the downloader UI tests and verify GREEN**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_video_downloader.VideoDownloaderToolTests -v
```

Expected: all downloader UI tests pass offscreen.

- [ ] **Step 5: Commit clipboard import UI**

```powershell
git add tools/edge_companion/ui.py tools/video_downloader.py tests/test_video_downloader.py
git commit -m "feat(edge-capture): 添加候选确认与粘贴入口"
```

### Task 5: Freeze Edge context into tasks and reuse existing adapters

**Files:**
- Modify: `tools/video_downloader.py:273-403`
- Modify: `tools/video_crawler/session.py:9-77`
- Modify: `tools/video_crawler/spider.py:129-174,241-307`
- Modify: `tests/test_video_downloader.py:1095-1271`
- Modify: `tests/test_video_crawler_session.py:11-84`
- Modify: `tests/test_video_crawler_adapters.py:37-150`

- [ ] **Step 1: Write failing task/header/adapter tests**

Pin task serialization and worker reconstruction:

```python
def complete_task_fixture():
    return {
        "url": valid_edge_message()["candidate"]["url"],
        "name": "edge-video",
        "save_dir": "downloads",
        "is_high_speed": False,
        "segment_concurrency": 5,
        "resume_enabled": True,
        "use_ytdlp_fallback": False,
        "live_record_seconds": 300,
        "sniffer_headless": True,
        "sniffer_use_persistent_profile": False,
        "sniffer_use_system_chrome": False,
        "sniffer_manual_wait_seconds": 25,
    }


def test_confirmed_edge_candidate_is_frozen_into_task(self):
    candidate = parse_candidate_json(json.dumps(valid_edge_message()))
    self.tool._activate_edge_candidate(candidate)
    self.tool.name_entry.setText("edge-video")
    self.tool.path_entry.setText("downloads")
    self.tool.add_to_queue()
    task = self.tool.task_queue.get_nowait()
    self.assertEqual(task["input_source"], "edge")
    self.assertEqual(task["url"], candidate.media_url)
    self.assertEqual(task["edge_candidate"], serialize_candidate(candidate))
    self.assertTrue(task["sniffer_headless"])

def test_worker_revalidates_edge_payload_and_passes_safe_session(self):
    task = complete_task_fixture()
    task["input_source"] = "edge"
    task["edge_candidate"] = valid_edge_message()
    result = self.tool._execute_task(task)
    self.assertTrue(result["success"])
    snapshot = RecordingSpider.init_kwargs["session_snapshot"]
    self.assertEqual(snapshot.user_agent, "Edge UA")
    self.assertEqual(snapshot.cookies, ())
    self.assertNotIn("Authorization", snapshot.headers)
```

Add a fixed-clock expired-task test that returns `EDGE_CANDIDATE_EXPIRED` with `retryable=False` before constructing the spider.

In `tests/test_video_crawler_adapters.py`, pass a safe `BrowserSessionSnapshot` to `UniversalVideoSpider`, download a direct `.m3u8`, `.mp4`, and `.mpd` candidate through a recording adapter, and assert every adapter sees `Referer`, `Origin`, and `User-Agent` but never `Cookie` or `Authorization`.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader tests.test_video_crawler_session tests.test_video_crawler_adapters -v
```

Expected: missing task fields, no Edge expiry error, and direct candidates not inheriting the supplied session snapshot.

- [ ] **Step 3: Implement task and spider integration**

For confirmed Edge input, add these task fields:

```python
task["input_source"] = "edge"
task["edge_candidate"] = serialize_candidate(self._pending_edge_candidate)
task["url"] = self._pending_edge_candidate.media_url
task["sniffer_headless"] = True
task["sniffer_use_persistent_profile"] = False
task["sniffer_use_system_chrome"] = False
```

Legacy and ordinary URL tasks use `input_source="url"` and `edge_candidate=None`.

At worker execution, call `candidate_from_task_payload` again, check expiry against the injected clock, require task URL equality, and pass `candidate.to_session_snapshot()` as `session_snapshot` to `spider_factory`. Convert validation and expiry failures to `VideoDownloadError` with the two Edge error codes and `retryable=False`.

In `UniversalVideoSpider._resolve_candidate`, merge a supplied snapshot for direct media before returning:

```python
if direct_candidate:
    if self.session_snapshot:
        self.headers = build_download_headers(
            self.headers,
            self.session_snapshot,
            direct_candidate.url,
        )
    self._log_direct_candidate(direct_candidate)
    return direct_candidate
```

Extract the existing three direct-kind log branches into `_log_direct_candidate` so the new merge does not duplicate classification logic. Do not modify `sniffer.py`.

- [ ] **Step 4: Run focused and full Python tests**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_edge_companion_protocol tests.test_video_crawler_models tests.test_video_crawler_session tests.test_video_crawler_adapters tests.test_video_downloader -v
python -m unittest discover -s tests -v
```

Expected: focused tests pass; full suite passes with only the existing Windows symlink-permission skip.

- [ ] **Step 5: Commit downloader reuse**

```powershell
git add tools/video_downloader.py tools/video_crawler/session.py tools/video_crawler/spider.py tests/test_video_downloader.py tests/test_video_crawler_session.py tests/test_video_crawler_adapters.py
git commit -m "feat(edge-capture): 复用安全请求上下文下载候选"
```

### Task 6: Verify and document the stage-one real-site loop

**Files:**
- Create: `browser_extensions/edge_video_capture/README.md`
- Modify: `docs/项目介绍.md`
- Verify: every Stage 1 file

- [ ] **Step 1: Document exact sideload and clipboard workflow**

The extension README must instruct the user to:

```text
1. Open edge://extensions and enable Developer mode.
2. Choose Load unpacked and select browser_extensions/edge_video_capture.
3. Verify the displayed extension ID is applbmkghgaoadhmmcdnbmebgideiefg.
4. Start FireflyTools and open the video downloader page.
5. In ordinary Edge, pass the site's human verification and open the real player.
6. Click the extension, choose Start capture for current tab, and approve runtime host access.
7. Return to the page and click Play.
8. Choose one HLS/MP4/DASH candidate and click Copy candidate JSON.
9. In FireflyTools click Paste Edge candidate, inspect the confirmation dialog, and confirm.
10. Choose name/output directory and add the task to the existing queue.
```

State that the extension never reads Cookie/Authorization, does not handle DRM, and may receive 401/403 if a site requires sensitive browser session state.

- [ ] **Step 2: Run the complete automated Stage 1 gate**

```powershell
node --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js browser_extensions/edge_video_capture/tests/capture_controller.test.js browser_extensions/edge_video_capture/tests/popup_model.test.js
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
git diff --check
```

Expected: all JavaScript tests pass; Python suite passes with the existing single skip; `git diff --check` produces no output.

- [ ] **Step 3: Perform the Stage 1 manual acceptance**

Use Microsoft Edge Stable and the target site. Record PASS/FAIL for all of these observations in the implementation handoff:

```text
- Extension ID matches the fixed development ID.
- Permission is requested only after Start capture is clicked.
- A real candidate appears within 10 seconds after Play.
- Other tabs do not add candidates.
- Stop capture immediately clears the selected tab's candidate list.
- Clipboard JSON contains no Cookie or Authorization field/value.
- FireflyTools confirmation does not display the raw token query value.
- Confirmed HLS/MP4/DASH enters the normal queue without launching Playwright.
```

If real playback returns 401/403, preserve the non-sensitive diagnostic evidence and keep the task failed; do not expand the protocol in this plan.

- [ ] **Step 4: Commit Stage 1 documentation**

```powershell
git add browser_extensions/edge_video_capture/README.md docs/项目介绍.md
git commit -m "docs(edge-capture): 说明剪贴板捕获闭环"
```

---

## Stage 2 — Native Messaging Automatic Transfer

### Task 7: Build runtime discovery and the authenticated loopback receiver

**Files:**
- Create: `tools/edge_companion/runtime.py`
- Create: `tools/edge_companion/receiver.py`
- Create: `tests/test_edge_companion_receiver.py`

- [ ] **Step 1: Write failing descriptor and receiver tests**

Use temporary paths, an injected clock, and an injected token factory. Tests must assert:

```python
receiver.start()
self.assertEqual(receiver.server_address[0], "127.0.0.1")
self.assertNotEqual(receiver.server_address[1], 0)
descriptor = read_runtime_descriptor(runtime_path)
self.assertEqual(descriptor.pid, os.getpid())
self.assertEqual(descriptor.protocol_version, 1)
self.assertEqual(descriptor.token, "test-token")
self.assertFalse(runtime_path.with_suffix(".tmp").exists())
```

Send POST requests with `http.client.HTTPConnection` and cover: missing/wrong bearer token → 401; non-loopback client address → 403 through a directly invoked handler seam; wrong content type → 415; body over 256 KiB → 413; valid but receiver not waiting → 409 `APP_NOT_WAITING`; valid and waiting → 202 plus one `candidate_received` signal; after three authentication failures the injected clock observes a bounded blocked interval and the next request receives 429 without logging the token.

- [ ] **Step 2: Run receiver tests and verify RED**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_edge_companion_receiver -v
```

Expected: missing runtime and receiver modules.

- [ ] **Step 3: Implement atomic descriptor and loopback receiver**

`runtime.py` must use:

```python
RUNTIME_TTL_SECONDS = 600


@dataclass(frozen=True)
class RuntimeDescriptor:
    port: int
    token: str
    pid: int
    protocol_version: int
    expires_at: datetime

    def to_dict(self) -> dict:
        return {
            "port": self.port,
            "token": self.token,
            "pid": self.pid,
            "protocol_version": self.protocol_version,
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }


def default_runtime_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA 不可用")
    return Path(local) / "FireflyTools" / "runtime" / "edge_capture.json"


def write_runtime_descriptor(path: Path, descriptor: RuntimeDescriptor) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(descriptor.to_dict()), encoding="utf-8")
    os.replace(temporary, path)


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        ctypes.windll.kernel32.CloseHandle(process)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
```

`read_runtime_descriptor` must require exactly those five fields, parse an offset-aware `expires_at`, reject ports outside `1..65535`, reject an empty token, and reject a descriptor when `expires_at <= now` or `pid_is_alive(pid)` is false. Use `secrets.token_urlsafe(32)` for 256 bits of startup entropy. Remove the descriptor on normal receiver stop only when its token still matches this process.

`EdgeCaptureReceiver(QObject)` exposes:

```python
candidate_received = pyqtSignal(object)
status_changed = pyqtSignal(str, str)

def start(self) -> None:
    if self._server is not None:
        return
    self._server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        self._make_handler(),
    )
    self._server.daemon_threads = True
    self._thread = threading.Thread(
        target=self._server.serve_forever,
        name="edge-capture-receiver",
        daemon=True,
    )
    self._write_descriptor()
    self._thread.start()
    self.status_changed.emit("未连接", "接收器已启动，等待用户授权捕获。")

def set_accepting(self, accepting: bool) -> None:
    self._accepting = bool(accepting)
    state = "等待捕获" if self._accepting else "未连接"
    self.status_changed.emit(state, "")

def stop(self) -> None:
    if self._server is None:
        return
    self._server.shutdown()
    self._server.server_close()
    if self._thread is not None:
        self._thread.join(timeout=2)
    self._remove_own_descriptor()
    self._server = None
    self._thread = None
```

Bind `ThreadingHTTPServer(("127.0.0.1", 0), handler)`, set `daemon_threads=True`, and run `serve_forever` in one named daemon thread. The handler authenticates and checks size before decoding JSON, then calls the shared protocol parser and emits the candidate signal. Track consecutive authentication failures; after the third failure set `auth_blocked_until = clock() + min(2 ** (failure_count - 3), 30)` and return 429 while blocked. Reset the count after one authenticated request. Never sleep in the handler and never log the Authorization header, bearer token, or raw message.

- [ ] **Step 4: Run receiver tests and verify GREEN**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_edge_companion_receiver -v
```

Expected: all receiver tests pass and leave no runtime descriptor behind.

- [ ] **Step 5: Commit the local receiver**

```powershell
git add tools/edge_companion/runtime.py tools/edge_companion/receiver.py tests/test_edge_companion_receiver.py
git commit -m "feat(edge-capture): 添加本机认证接收器"
```

### Task 8: Implement Native Messaging framing and forwarding

**Files:**
- Create: `tools/edge_companion/native_host.py`
- Create: `tests/test_edge_companion_native_host.py`

- [ ] **Step 1: Write failing binary framing and forwarding tests**

Use `io.BytesIO` and pin the native-byte-order 32-bit length format:

```python
payload = {"protocol_version": 1, "type": "ping", "request_id": "r1"}
stream = io.BytesIO()
write_native_message(stream, payload)
stream.seek(0)
self.assertEqual(read_native_message(stream), payload)
```

Test truncated prefix/body, invalid UTF-8/JSON, and announced sizes over 256 KiB. Test `run_host` with caller origin `chrome-extension://applbmkghgaoadhmmcdnbmebgideiefg/` and with a rejected origin. Mock runtime descriptor loading, PID liveness, and `urllib.request.urlopen`; assert the POST targets only `http://127.0.0.1:<port>/v1/candidate`, sets `Authorization: Bearer <token>` in memory, and writes a redacted structured ack. Missing/stale descriptor or dead PID must return `APP_NOT_RUNNING`.

- [ ] **Step 2: Run native-host tests and verify RED**

```powershell
python -m unittest tests.test_edge_companion_native_host -v
```

Expected: missing native host module.

- [ ] **Step 3: Implement the host loop**

Use native framing exactly as documented by Edge:

```python
def read_native_message(stream) -> dict | None:
    prefix = stream.read(4)
    if prefix == b"":
        return None
    if len(prefix) != 4:
        raise EdgeProtocolError("INVALID_FRAME", "Native Messaging 长度前缀不完整")
    length = struct.unpack("=I", prefix)[0]
    if length > MAX_MESSAGE_BYTES:
        raise EdgeProtocolError("MESSAGE_TOO_LARGE", "Native Messaging 消息超过 256 KiB")
    body = stream.read(length)
    if len(body) != length:
        raise EdgeProtocolError("INVALID_FRAME", "Native Messaging 消息体不完整")
    return json.loads(body.decode("utf-8"))


def write_native_message(stream, message: dict) -> None:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise EdgeProtocolError("MESSAGE_TOO_LARGE", "响应超过 256 KiB")
    stream.write(struct.pack("=I", len(body)))
    stream.write(body)
    stream.flush()
```

`main()` reads the caller origin from the first Edge-supplied argument and ignores only the optional `--parent-window=` argument. It loops until EOF, validates each candidate before forwarding, and writes one ack/error with the same `request_id`. Protocol stdout contains frames only; diagnostics go to stderr through `redact_for_display`.

- [ ] **Step 4: Run host and protocol tests**

```powershell
python -m unittest tests.test_edge_companion_native_host tests.test_edge_companion_protocol tests.test_edge_companion_receiver -v
```

Expected: all transport tests pass.

- [ ] **Step 5: Commit Native Messaging host logic**

```powershell
git add tools/edge_companion/native_host.py tests/test_edge_companion_native_host.py
git commit -m "feat(edge-capture): 实现 Native Messaging 转发"
```

### Task 9: Add a stable Windows launcher and explicit per-user installer

**Files:**
- Create: `pyproject.toml`
- Create: `tools/edge_companion/install.py`
- Create: `tests/test_edge_companion_install.py`

- [ ] **Step 1: Write failing installer tests**

Mock `winreg`, `shutil.which`, and filesystem paths. Assert install writes:

```json
{
  "name": "com.fireflytools.video_capture",
  "description": "FireflyTools Edge video capture host",
  "path": "C:\\Python\\Scripts\\fireflytools-edge-host.exe",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://applbmkghgaoadhmmcdnbmebgideiefg/"
  ]
}
```

Assert the HKCU default value points to the manifest file under `%LOCALAPPDATA%\FireflyTools\edge_companion\`, install refuses a launcher with arguments or a non-`.exe` path, `status` detects mismatched origins/path, and uninstall removes only `HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.fireflytools.video_capture` plus the generated manifest.

- [ ] **Step 2: Run installer tests and verify RED**

```powershell
python -m unittest tests.test_edge_companion_install -v
```

Expected: missing install module and console-script metadata.

- [ ] **Step 3: Add editable-install metadata and installer CLI**

Create minimal packaging metadata without duplicating `requirements.txt`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "fireflytools-local"
version = "0.1.0"
requires-python = ">=3.11"

[project.scripts]
fireflytools-edge-host = "tools.edge_companion.native_host:main"

[tool.setuptools.packages.find]
include = ["tools*"]
```

Expose CLI subcommands:

```powershell
python -m tools.edge_companion.install install
python -m tools.edge_companion.install status
python -m tools.edge_companion.install uninstall
```

`install` finds `fireflytools-edge-host.exe` with `shutil.which`, writes the host manifest atomically, then uses `winreg.CreateKey` under HKCU. It must fail with a Chinese instruction to run `python -m pip install -e . --no-deps` when the launcher is absent. Do not request elevation and do not write HKLM.

Expose a side-effect-free status model for the UI:

```python
HOST_NAME = "com.fireflytools.video_capture"
ALLOWED_ORIGIN = "chrome-extension://applbmkghgaoadhmmcdnbmebgideiefg/"
REGISTRY_KEY = (
    r"Software\Microsoft\Edge\NativeMessagingHosts\com.fireflytools.video_capture"
)


@dataclass(frozen=True)
class HostInstallStatus:
    installed: bool
    detail: str
    manifest_path: Path | None = None
    launcher_path: Path | None = None


def get_install_status() -> HostInstallStatus:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            manifest_value, _ = winreg.QueryValueEx(key, "")
    except FileNotFoundError:
        return HostInstallStatus(False, "Edge 连接组件未安装。")
    manifest_path = Path(manifest_value)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return HostInstallStatus(False, "Edge 连接组件清单不可用。", manifest_path)
    launcher_path = Path(str(payload.get("path", "")))
    valid = (
        payload.get("name") == HOST_NAME
        and payload.get("type") == "stdio"
        and payload.get("allowed_origins") == [ALLOWED_ORIGIN]
        and launcher_path.suffix.lower() == ".exe"
        and launcher_path.is_file()
    )
    detail = "Edge 连接组件已安装。" if valid else "Edge 连接组件配置不匹配。"
    return HostInstallStatus(valid, detail, manifest_path, launcher_path)
```

`get_install_status` returns `installed=True` only when the registry value exists, the manifest parses, its host name/origin match the fixed constants, and its `.exe` path exists.

- [ ] **Step 4: Run tests and perform a reversible development install smoke test**

```powershell
python -m unittest tests.test_edge_companion_install -v
python -m pip install -e . --no-deps
Get-Command fireflytools-edge-host.exe
python -m tools.edge_companion.install install
python -m tools.edge_companion.install status
python -m tools.edge_companion.install uninstall
```

Expected: tests pass; launcher resolves to the active Python Scripts directory; status reports the fixed extension ID; uninstall reports the component removed. Obtain explicit user approval immediately before the real registry install/uninstall smoke test.

- [ ] **Step 5: Commit packaging and installer**

```powershell
git add pyproject.toml tools/edge_companion/install.py tests/test_edge_companion_install.py
git commit -m "feat(edge-capture): 添加当前用户连接组件安装器"
```

### Task 10: Connect extension automatic send, receiver state, and main-window lifecycle

**Files:**
- Create: `browser_extensions/edge_video_capture/native_client.js`
- Create: `browser_extensions/edge_video_capture/tests/native_client.test.js`
- Modify: `browser_extensions/edge_video_capture/package.json`
- Modify: `browser_extensions/edge_video_capture/service_worker.js`
- Modify: `browser_extensions/edge_video_capture/popup.js`
- Modify: `tools/video_downloader.py:25-45,146-205,210-358`
- Modify: `tools/main.py:126-170,220-245`
- Modify: `tests/test_video_downloader.py:981-1225`
- Modify: `tests/test_main_window.py:18-107`

- [ ] **Step 1: Write failing native-client, UI signal, and lifecycle tests**

Use an injected fake `chrome.runtime.connectNative` port. Assert host name `com.fireflytools.video_capture`, request/ack matching, 10-second timeout, disconnect mapping to `HOST_NOT_INSTALLED`, `APP_NOT_RUNNING` rendering, and preservation of the copy fallback.

Add a fake receiver QObject and inject `edge_install_status_getter=lambda: HostInstallStatus(installed=True, detail="")` into Python tests:

```python
class FakeEdgeReceiver(QObject):
    candidate_received = pyqtSignal(object)
    status_changed = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.started = False
        self.accepting = False
        self.stopped = False

    def start(self):
        self.started = True

    def set_accepting(self, value):
        self.accepting = value

    def stop(self):
        self.stopped = True
```

Assert `MediaToolboxApp(edge_receiver_factory=FakeEdgeReceiver)` starts one receiver, injects the same instance into `VideoDownloaderTool`, and stops it only on an accepted close. Assert a missing host status renders `未安装`, an installed idle host renders `未连接`, the wait button toggles `receiver.set_accepting`, status signals render `等待捕获/已收到候选/错误`, and `candidate_received` routes through the same confirmation method as clipboard JSON.

- [ ] **Step 2: Run affected tests and verify RED**

```powershell
node --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js browser_extensions/edge_video_capture/tests/capture_controller.test.js browser_extensions/edge_video_capture/tests/popup_model.test.js browser_extensions/edge_video_capture/tests/native_client.test.js
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader tests.test_main_window -v
```

Expected: missing native client and receiver injection/lifecycle behavior.

- [ ] **Step 3: Implement automatic send and app lifecycle**

`NativeClient` must keep a `Map<requestId, {resolve, reject, timer}>`, post only validated protocol messages, clear the matching timer on ack, and reject all pending calls on disconnect. `service_worker.js` exposes a `send_candidate` message that passes the selected protocol message to the client. Popup errors map as follows:

```javascript
const USER_MESSAGES = {
  HOST_NOT_INSTALLED: "Edge 连接组件未安装；请查看安装说明，或使用复制候选 JSON。",
  APP_NOT_RUNNING: "请先打开 FireflyTools，再点击等待 Edge 捕获。",
  APP_NOT_WAITING: "FireflyTools 尚未进入等待捕获状态。",
  UNSUPPORTED_VERSION: "扩展与 FireflyTools 协议版本不一致，请升级对应组件。",
  TIMEOUT: "连接组件响应超时；候选仍可复制。",
};
```

Update the package test script to:

```json
"test": "node --test tests/candidate_detector.test.js tests/capture_store.test.js tests/capture_controller.test.js tests/popup_model.test.js tests/native_client.test.js"
```

In `MediaToolboxApp.__init__`, construct and start the receiver before creating the video tab, keep `self.video_downloader_tool`, and inject the receiver. In an accepted `closeEvent`, stop the receiver before `super().closeEvent(event)`. Do not stop it while image-similarity workers cause the close event to be ignored.

In `VideoDownloaderTool`, add injectable `edge_receiver` and `edge_install_status_getter` constructor arguments and connect receiver signals once during initialization. Read the install status only to choose `未安装` versus `未连接`; never install from the constructor. `toggle_edge_waiting` changes only the receiver gate and status; it does not open Edge, refresh a page, click Play, or start a download. When no receiver or no installed host is available, show the install command and leave the clipboard fallback enabled.

- [ ] **Step 4: Run JavaScript, UI, and lifecycle tests**

```powershell
node --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js browser_extensions/edge_video_capture/tests/capture_controller.test.js browser_extensions/edge_video_capture/tests/popup_model.test.js browser_extensions/edge_video_capture/tests/native_client.test.js
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_edge_companion_receiver tests.test_video_downloader tests.test_main_window -v
```

Expected: all tests pass; no real receiver or registry change occurs in unit tests.

- [ ] **Step 5: Commit end-to-end automatic transfer**

```powershell
git add browser_extensions/edge_video_capture/native_client.js browser_extensions/edge_video_capture/service_worker.js browser_extensions/edge_video_capture/popup.js browser_extensions/edge_video_capture/package.json browser_extensions/edge_video_capture/tests/native_client.test.js tools/video_downloader.py tools/main.py tests/test_video_downloader.py tests/test_main_window.py
git commit -m "feat(edge-capture): 接通 Edge 与下载界面"
```

### Task 11: Finish installation, privacy, and troubleshooting documentation

**Files:**
- Modify: `browser_extensions/edge_video_capture/README.md`
- Modify: `docs/项目介绍.md`
- Verify: `manifest.json`, native-host manifest generation, and all runtime files

- [ ] **Step 1: Document the Stage 2 setup and recovery commands**

Add exact development commands:

```powershell
python -m pip install -e . --no-deps
python -m tools.edge_companion.install install
python -m tools.edge_companion.install status
python -m tools.edge_companion.install uninstall
```

Explain the five UI states (`未安装 / 未连接 / 等待捕获 / 已收到候选 / 错误`), Native Messaging policy restrictions, extension ID mismatch, app-not-running, candidate expiry, no-candidate replay, and 401/403 without sensitive session headers. State that uninstall removes only the current user's Edge native-host registration and generated manifest.

- [ ] **Step 2: Add a security inventory table**

Document these exact boundaries:

| Boundary | Stored/transmitted | Explicitly excluded |
|---|---|---|
| Extension session | selected tab ID, page URL/title, up to 50 media candidates, safe headers | Cookie, Authorization, history, LocalStorage, HAR |
| Clipboard/native message | one user-selected V1 candidate | other tabs and unselected candidates |
| Runtime descriptor | loopback port, random token, PID, version, expiry | candidate URL and browser data |
| Download task | confirmed media URL and safe header snapshot | browser profile, Cookie database, DRM keys |

- [ ] **Step 3: Run documentation and repository safety checks**

```powershell
rg -n "Cookie|Authorization|DRM|127\.0\.0\.1|Native Messaging|applbmkghgaoadhmmcdnbmebgideiefg" browser_extensions/edge_video_capture/README.md docs/项目介绍.md
rg -n "待补充|占位内容|未完成实现" browser_extensions/edge_video_capture tools/edge_companion docs/项目介绍.md
git diff --check
```

Expected: the first command finds all security and setup sections; the second command returns no output; diff check returns no output.

- [ ] **Step 4: Commit final documentation**

```powershell
git add browser_extensions/edge_video_capture/README.md docs/项目介绍.md
git commit -m "docs(edge-capture): 完善安装与隐私说明"
```

### Task 12: Run complete automated and manual release gates

**Files:**
- Verify: all Edge companion and existing video crawler files

- [ ] **Step 1: Run every automated test layer**

```powershell
node --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js browser_extensions/edge_video_capture/tests/capture_controller.test.js browser_extensions/edge_video_capture/tests/popup_model.test.js browser_extensions/edge_video_capture/tests/native_client.test.js
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_edge_companion_protocol tests.test_edge_extension_contract tests.test_edge_companion_receiver tests.test_edge_companion_native_host tests.test_edge_companion_install tests.test_video_crawler_models tests.test_video_crawler_session tests.test_video_crawler_adapters tests.test_video_downloader tests.test_main_window -v
python -m unittest discover -s tests -v
git diff --check
```

Expected: all JavaScript and focused Python tests pass; full Python suite passes with no new skip/failure; only the pre-existing Windows symlink privilege skip is acceptable.

- [ ] **Step 2: Audit forbidden capabilities and sensitive output**

```powershell
rg -n "webRequestBlocking|declarativeNetRequest|content_scripts|chrome\.cookies|navigator\.webdriver|remote-debugging|Authorization|Cookie" browser_extensions/edge_video_capture tools/edge_companion
git status --short --untracked-files=all
```

Expected: `Authorization`/`Cookie` appear only in rejection, redaction, tests, and documentation; no browser automation evasion, request modification, cookie API, content script, remote debugging, browser profile, media output, runtime descriptor, or private key is tracked.

- [ ] **Step 3: Perform the Native Messaging real-site acceptance**

After explicit approval for the current-user registry change:

```powershell
python -m tools.edge_companion.install install
python -m tools.edge_companion.install status
```

Reload the unpacked extension, start FireflyTools, click `等待 Edge 捕获`, complete the target site's verification in ordinary Edge, start the selected-tab capture, click Play, select a candidate, and click `发送到 FireflyTools`.

Record PASS/FAIL for:

```text
- Candidate appears within 10 seconds.
- FireflyTools receives exactly one selected candidate and shows confirmation.
- Rejecting confirmation produces no task; accepting fills one Edge-mode task.
- No Playwright browser opens for the Edge-mode task.
- Logs and dialogs contain no Cookie, Authorization, bearer token, or raw sensitive query value.
- Stopping capture, closing the tab, and waiting five minutes each clear candidates.
- FireflyTools closed → APP_NOT_RUNNING and copy fallback remains available.
- Native host uninstalled → installation guidance and copy fallback remain available.
- Candidate older than five minutes → EDGE_CANDIDATE_EXPIRED and no automatic retry.
- Other tabs never enter the selected tab's list.
```

- [ ] **Step 4: Remove the development connector if the user does not want it retained**

```powershell
python -m tools.edge_companion.install uninstall
```

Expected: only the current-user native-host registry entry and generated host manifest are removed; the unpacked extension and repository files remain.

- [ ] **Step 5: Prepare the implementation handoff**

List commit SHAs, automated counts, manual PASS/FAIL evidence, registry install state, known 401/403 limitations, and any enterprise `NativeMessagingAllowlist` restriction. Do not claim the target site works unless the real-site acceptance reached a downloadable media candidate.

---

## Official References Locked for Implementation

- Microsoft Edge Native Messaging host manifest, framing, origin argument, and Windows HKCU registration: <https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/native-messaging>
- Microsoft Edge Manifest V3 observational `webRequest` and service-worker model: <https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/migrate-your-extension-from-manifest-v2-to-v3>
- Chromium runtime optional host permissions: <https://developer.chrome.com/docs/extensions/reference/api/permissions>
- Chromium `webRequest` permission and observational header events: <https://developer.chrome.com/docs/extensions/reference/api/webRequest>
- Chromium service-worker state and alarm guidance: <https://developer.chrome.com/docs/extensions/get-started/tutorial/service-worker-events>
- Chromium clipboard permission: <https://developer.chrome.com/docs/extensions/reference/permissions-list>

## Out of Scope for This Plan

- Cookie, Authorization, browser profile, LocalStorage, or full HAR extraction.
- DRM/EME/Widevine handling or protected-content circumvention.
- Stealth, browser fingerprint changes, CAPTCHA automation, DOM injection, request blocking, or remote debugging.
- Microsoft Edge Add-ons publication and production signing.
- Any sensitive-session enhancement after a real 401/403; that requires a new security specification and separate approval.
