# Edge Capture Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit Edge popup action that starts capture, waits for the persisted acknowledgement, and then reloads the selected tab without cache so initialization-time media requests can be observed.

**Architecture:** Keep the existing top-level `webRequest` listeners and capture controller unchanged. The popup owns the user-triggered orchestration: a shared start helper creates the current-tab session, clears stale UI state, and optionally calls `chrome.tabs.reload()` only after a successful `capture:start` response. Existing capture remains non-reloading; the new action is explicit and site-agnostic.

**Tech Stack:** Manifest V3 extension JavaScript, Chrome/Edge `tabs` and runtime messaging APIs, Node.js built-in test runner, HTML/CSS, Markdown.

---

### Task 1: Add capture-and-reload as a tested popup workflow

**Files:**
- Modify: `browser_extensions/edge_video_capture/popup.html`
- Modify: `browser_extensions/edge_video_capture/popup.css`
- Modify: `browser_extensions/edge_video_capture/popup.js`
- Modify: `browser_extensions/edge_video_capture/popup_model.js`
- Modify: `browser_extensions/edge_video_capture/tests/popup_interaction.test.js`
- Modify: `browser_extensions/edge_video_capture/tests/popup_model.test.js`
- Modify: `browser_extensions/edge_video_capture/README.md`
- Modify: `docs/项目介绍.md`

- [ ] **Step 1: Extend the popup interaction harness and write failing ordering/error tests**

Add `#start-reload-button` to the fake element map and give `FakeElement` a `disabled` property. Refactor the existing VM setup into a local harness that records runtime messages and `tabs.reload()` calls. Add a deferred promise helper so the test can prove reload does not happen before the `capture:start` acknowledgement:

```js
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return {promise, resolve, reject};
}

test("capture and reload waits for persisted capture before bypassing cache", async () => {
  const startGate = deferred();
  const harness = loadPopup({startResponse: startGate.promise});
  await flushTasks();
  harness.messages.length = 0;

  const click = harness.elements
    .get("#start-reload-button")
    .listeners.get("click")();
  await flushTasks();
  assert.equal(harness.reloads.length, 0);

  startGate.resolve({ok: true});
  await click;
  assert.deepEqual(harness.reloads, [
    {tabId: harness.currentTab.id, options: {bypassCache: true}},
  ]);
  assert.equal(
    harness.elements.get("#status").textContent,
    "capturing_reloaded",
  );
});

test("capture start rejection never reloads the tab", async () => {
  const harness = loadPopup({startResponse: Promise.resolve({ok: false})});
  await flushTasks();
  harness.messages.length = 0;

  await harness.elements.get("#start-reload-button").listeners.get("click")();

  assert.equal(harness.reloads.length, 0);
  assert.equal(
    harness.elements.get("#status").textContent,
    "capture_start_failed",
  );
});

test("reload failure keeps the established capture session", async () => {
  const harness = loadPopup({
    reload: async () => {
      throw new Error("reload unavailable");
    },
  });
  await flushTasks();
  harness.messages.length = 0;

  await harness.elements.get("#start-reload-button").listeners.get("click")();

  assert.equal(
    harness.elements.get("#status").textContent,
    "reload_failed",
  );
  assert.equal(
    harness.messages.some((message) => message.type === "capture:stop"),
    false,
  );
});

test("concurrent capture starts are ignored while one start is pending", async () => {
  const startGate = deferred();
  const harness = loadPopup({startResponse: startGate.promise});
  await flushTasks();
  harness.messages.length = 0;
  const listener = harness.elements
    .get("#start-reload-button")
    .listeners.get("click");

  const first = listener();
  const second = listener();
  await flushTasks();
  assert.equal(
    harness.messages.filter((message) => message.type === "capture:start").length,
    1,
  );

  startGate.resolve({ok: true});
  await Promise.all([first, second]);
});
```

The `loadPopup()` harness must expose `{elements, messages, reloads, currentTab}` and accept optional `startResponse` and `reload` functions. Its fake `chrome.tabs.reload(tabId, options)` must append the exact call to `reloads` before delegating to the injected function.

- [ ] **Step 2: Run the interaction tests and verify RED**

Run from `browser_extensions/edge_video_capture`:

```powershell
node --test --test-isolation=none tests/popup_interaction.test.js
```

Expected: FAIL because `#start-reload-button` and the reload orchestration do not exist. If the default Node runner reports Windows `spawn EPERM`, retain `--test-isolation=none`; do not treat the sandbox failure as a product failure.

- [ ] **Step 3: Add failing popup-message assertions**

Extend `tests/popup_model.test.js` with exact Chinese copy:

```js
assert.equal(
  popupModel.userMessage("capturing_reloaded"),
  "已开始捕获并重新加载页面。",
);
assert.equal(
  popupModel.userMessage("capture_start_failed"),
  "捕获未能开始，请重试。",
);
assert.equal(
  popupModel.userMessage("reload_failed"),
  "捕获已开始，但页面重新加载失败；请手动刷新或点击播放。",
);
```

- [ ] **Step 4: Run the model test and verify RED**

```powershell
node --test --test-isolation=none tests/popup_model.test.js
```

Expected: FAIL because the three message codes are not mapped yet.

- [ ] **Step 5: Implement the minimal popup UI and orchestration**

Add the explicit action next to the existing controls without changing the old button text:

```html
<button id="start-button" type="button">开始捕获</button>
<button id="start-reload-button" type="button">开始捕获并重新加载</button>
<button id="stop-button" type="button" class="secondary">停止</button>
```

Allow the toolbar to wrap and make disabled state visible:

```css
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

#start-reload-button {
  flex-basis: 100%;
}

button:disabled {
  cursor: wait;
  opacity: 0.6;
}
```

Add the three exact message strings to `MESSAGES` in `popup_model.js`. In `popup.js`, query the new button and replace the old inline start handler with a shared helper equivalent to:

```js
const startReloadButton = document.querySelector("#start-reload-button");
let startPending = false;

function setStartDisabled(disabled) {
  startButton.disabled = disabled;
  startReloadButton.disabled = disabled;
}

async function beginCapture({reload = false} = {}) {
  if (startPending) {
    return;
  }
  startPending = true;
  setStartDisabled(true);
  try {
    activeTab = await currentTab();
    if (!activeTab || !Number.isInteger(activeTab.id)) {
      showStatus("capture_start_failed");
      return;
    }
    const response = await chrome.runtime.sendMessage({
      type: "capture:start",
      tabId: activeTab.id,
      pageUrl: typeof activeTab.url === "string" ? activeTab.url : "",
      pageTitle: typeof activeTab.title === "string" ? activeTab.title : "",
    });
    if (!response || !response.ok) {
      showStatus("capture_start_failed");
      return;
    }
    selectedCandidateId = "";
    renderCandidates([]);
    if (!reload) {
      showStatus("capturing");
      return;
    }
    try {
      await chrome.tabs.reload(activeTab.id, {bypassCache: true});
      showStatus("capturing_reloaded");
    } catch (_error) {
      showStatus("reload_failed");
    }
  } catch (_error) {
    showStatus("capture_start_failed");
  } finally {
    startPending = false;
    setStartDisabled(false);
  }
}

startButton.addEventListener("click", () => beginCapture());
startReloadButton.addEventListener("click", () =>
  beginCapture({reload: true}),
);
```

Do not add a `capture:stop` rollback when reload fails: the capture session is already valid and must remain available for manual refresh. Do not change `manifest.json`; the Tabs API permits reloading a tab without adding a broad `tabs` permission, and current host permissions already expose the current page URL/title.

- [ ] **Step 6: Run focused tests and verify GREEN**

```powershell
node --test --test-isolation=none tests/popup_interaction.test.js tests/popup_model.test.js
```

Expected: all focused tests PASS with zero failures.

- [ ] **Step 7: Update user documentation**

Update `browser_extensions/edge_video_capture/README.md` and `docs/项目介绍.md` so the normal workflow recommends “开始捕获并重新加载” when the player requests media during initial page load. Preserve the manual alternative “开始捕获” followed by `Ctrl+F5`. State that reload can re-trigger a site challenge, but capture remains active in the same tab; do not claim automated checks replace real Edge validation.

- [ ] **Step 8: Run the full extension regression and safety audit**

From `browser_extensions/edge_video_capture`:

```powershell
node --test --test-isolation=none tests/candidate_detector.test.js tests/capture_store.test.js tests/capture_controller.test.js tests/popup_model.test.js tests/popup_interaction.test.js tests/native_client.test.js
```

From the worktree root:

```powershell
git diff --check
rg -n "content_scripts|chrome\.cookies|Authorization|webRequestBlocking|declarativeNetRequest|webdriver|stealth|remote-debugging" browser_extensions/edge_video_capture
```

Expected: all extension tests PASS; `git diff --check` reports no errors; the audit shows no newly introduced content script, Cookie access, Authorization capture, request blocking/modification, or automation-evasion capability. Existing documentation statements that explicitly say these capabilities are excluded are acceptable matches.

- [ ] **Step 9: Commit the implementation**

Stage only the eight files listed for Task 1 and verify the cached diff before committing:

```powershell
git add -- browser_extensions/edge_video_capture/popup.html browser_extensions/edge_video_capture/popup.css browser_extensions/edge_video_capture/popup.js browser_extensions/edge_video_capture/popup_model.js browser_extensions/edge_video_capture/tests/popup_interaction.test.js browser_extensions/edge_video_capture/tests/popup_model.test.js browser_extensions/edge_video_capture/README.md docs/项目介绍.md
git diff --cached --check
git diff --cached --name-status
git commit -m "feat(edge-capture): 支持捕获后重新加载页面"
```

Before reporting completion, record the exact test counts, the commit SHA, and that real Edge validation remains `PENDING` until the user reloads the unpacked extension and exercises the target page.
