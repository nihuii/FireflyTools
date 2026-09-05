const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class FakeElement {
  constructor() {
    this.listeners = new Map();
    this.children = [];
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

function flushTasks() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("start captures only the current tab without requesting optional permissions", async () => {
  const elements = new Map(
    [
      "#start-button",
      "#stop-button",
      "#send-button",
      "#copy-button",
      "#status",
      "#candidate-list",
    ].map((selector) => [selector, new FakeElement()]),
  );
  const messages = [];
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
    },
    runtime: {
      sendMessage: async (message) => {
        messages.push({...message});
        if (message.type === "capture:list") {
          return {ok: true, candidates: []};
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
  await flushTasks();
  messages.length = 0;

  await elements.get("#start-button").listeners.get("click")();

  const startMessages = messages.filter((message) => message.type === "capture:start");
  assert.equal(permissionRequests, 0);
  assert.equal(startMessages.length, 1);
  assert.deepEqual(startMessages[0], {
    type: "capture:start",
    tabId: currentTab.id,
    pageUrl: currentTab.url,
    pageTitle: currentTab.title,
  });
});
