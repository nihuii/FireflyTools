const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class FakeElement {
  constructor() {
    this.listeners = new Map();
    this.children = [];
    this.disabled = false;
    this.textContent = "";
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  append(...children) {
    this.children.push(...children);
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return {promise, resolve, reject};
}

function flushTasks() {
  return new Promise((resolve) => setImmediate(resolve));
}

function clickListener(harness, selector) {
  const listener = harness.elements.get(selector).listeners.get("click");
  assert.equal(typeof listener, "function", `${selector} has no click listener`);
  return listener;
}

function loadPopup({
  startResponse = {ok: true},
  reload = async () => {},
  candidates = [],
} = {}) {
  const elements = new Map(
    [
      "#start-button",
      "#start-reload-button",
      "#stop-button",
      "#send-button",
      "#copy-button",
      "#status",
      "#candidate-list",
    ].map((selector) => [selector, new FakeElement()]),
  );
  const messages = [];
  const reloads = [];
  let permissionRequests = 0;
  const currentTab = {
    id: 37,
    url: "https://video.example/watch/1",
    title: "Selected video",
  };
  const chrome = {
    permissions: {
      request: async () => {
        permissionRequests += 1;
        throw new Error("optional host permissions must not be requested");
      },
    },
    tabs: {
      query: async () => [currentTab],
      reload: async (tabId, options) => {
        reloads.push({tabId, options: {...options}});
        return reload(tabId, options);
      },
    },
    runtime: {
      sendMessage: async (message) => {
        messages.push({...message});
        if (message.type === "capture:list") {
          return {ok: true, candidates};
        }
        if (message.type === "capture:start") {
          return typeof startResponse === "function"
            ? startResponse(message)
            : startResponse;
        }
        if (message.type === "capture:protocol") {
          return {ok: true, message: {type: "video_candidate"}};
        }
        return {ok: true};
      },
    },
  };
  const document = {
    querySelector: (selector) => elements.get(selector),
    createElement: () => new FakeElement(),
  };
  const source = fs.readFileSync(path.join(__dirname, "..", "popup.js"), "utf8");

  vm.runInNewContext(source, {
    chrome,
    crypto: {randomUUID: () => "request-id"},
    document,
    EdgeCapturePopupModel: {
      candidateRow: (candidate) => candidate,
      userMessage: (code) => code || "",
    },
    navigator: {clipboard: {writeText: async () => {}}},
    setInterval: () => 1,
  });

  return {
    currentTab,
    elements,
    get permissionRequests() {
      return permissionRequests;
    },
    messages,
    reloads,
  };
}

test("start captures only the current tab without requesting permissions or reloading", async () => {
  const harness = loadPopup();
  await flushTasks();
  harness.messages.length = 0;

  await clickListener(harness, "#start-button")();

  const startMessages = harness.messages.filter(
    (message) => message.type === "capture:start",
  );
  assert.equal(harness.permissionRequests, 0);
  assert.equal(startMessages.length, 1);
  assert.deepEqual(startMessages[0], {
    type: "capture:start",
    tabId: harness.currentTab.id,
    pageUrl: harness.currentTab.url,
    pageTitle: harness.currentTab.title,
  });
  assert.equal(harness.reloads.length, 0);
  assert.equal(harness.elements.get("#status").textContent, "capturing");
});

test("capture and reload waits for persisted capture before bypassing cache", async () => {
  const startGate = deferred();
  const harness = loadPopup({startResponse: startGate.promise});
  await flushTasks();
  harness.messages.length = 0;

  const click = clickListener(harness, "#start-reload-button")();
  await flushTasks();

  assert.equal(harness.reloads.length, 0);
  assert.equal(harness.elements.get("#start-button").disabled, true);
  assert.equal(harness.elements.get("#start-reload-button").disabled, true);

  startGate.resolve({ok: true});
  await click;

  assert.deepEqual(harness.reloads, [
    {tabId: harness.currentTab.id, options: {bypassCache: true}},
  ]);
  assert.equal(
    harness.elements.get("#status").textContent,
    "capturing_reloaded",
  );
  assert.equal(harness.elements.get("#start-button").disabled, false);
  assert.equal(harness.elements.get("#start-reload-button").disabled, false);
});

test("capture start failure never reloads the tab", async (context) => {
  const failures = [
    {name: "unsuccessful response", startResponse: {ok: false}},
    {
      name: "rejected response",
      startResponse: async () => {
        throw new Error("capture unavailable");
      },
    },
  ];

  for (const failure of failures) {
    await context.test(failure.name, async () => {
      const harness = loadPopup({startResponse: failure.startResponse});
      await flushTasks();
      harness.messages.length = 0;

      await clickListener(harness, "#start-reload-button")();

      assert.equal(harness.reloads.length, 0);
      assert.equal(
        harness.elements.get("#status").textContent,
        "capture_start_failed",
      );
    });
  }
});

test("reload failure keeps the established capture session", async () => {
  const harness = loadPopup({
    reload: async () => {
      throw new Error("reload unavailable");
    },
  });
  await flushTasks();
  harness.messages.length = 0;

  await clickListener(harness, "#start-reload-button")();

  assert.equal(harness.elements.get("#status").textContent, "reload_failed");
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
  const listener = clickListener(harness, "#start-reload-button");

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

test("successful capture clears the visible candidates and current selection", async () => {
  const harness = loadPopup({
    candidates: [
      {
        id: "candidate-1",
        type: "HLS",
        hostname: "cdn.example",
        discoveredAt: "12:00:00",
        redactedUrl: "https://cdn.example/master.m3u8",
      },
    ],
  });
  await flushTasks();
  const candidateList = harness.elements.get("#candidate-list");
  const candidateRadio = candidateList.children[0].children[0];
  candidateRadio.listeners.get("change")();
  harness.messages.length = 0;

  await clickListener(harness, "#start-button")();
  await clickListener(harness, "#send-button")();

  assert.equal(candidateList.children.length, 1);
  assert.equal(candidateList.children[0].className, "empty");
  assert.equal(harness.elements.get("#status").textContent, "select_candidate");
  assert.equal(
    harness.messages.some((message) => message.type === "capture:protocol"),
    false,
  );
});
