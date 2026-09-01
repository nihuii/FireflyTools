const test = require("node:test");
const assert = require("node:assert/strict");
const detector = require("../candidate_detector.js");
const store = require("../capture_store.js");
const captureController = require("../capture_controller.js");

function createControllerWithClock(now) {
  return captureController.createCaptureController({
    detector,
    store,
    now: () => now,
  });
}

function startCapture(controller, tabId = 7) {
  controller.start({
    tabId,
    pageUrl: "https://site.test/watch",
    pageTitle: "Video",
  });
}

function captureHls(controller, {tabId = 7, requestId = "r1"} = {}) {
  controller.onBeforeRequest({
    tabId,
    requestId,
    url: "https://cdn.test/a.m3u8?token=secret",
    method: "GET",
    type: "media",
  });
  controller.onBeforeSendHeaders({
    tabId,
    requestId,
    url: "https://cdn.test/a.m3u8?token=secret",
    method: "GET",
    requestHeaders: [
      {name: "Cookie", value: "sid=secret"},
      {name: "Referer", value: "https://site.test/"},
    ],
  });
  controller.onHeadersReceived({
    tabId,
    requestId,
    url: "https://cdn.test/a.m3u8?token=secret",
    method: "GET",
    responseHeaders: [
      {name: "Content-Type", value: "application/vnd.apple.mpegurl"},
    ],
  });
}

test("controller ignores tabs without an active capture session", () => {
  const controller = createControllerWithClock(1000);
  startCapture(controller);

  controller.onBeforeRequest({
    tabId: 8,
    requestId: "r1",
    url: "https://cdn.test/a.m3u8",
    method: "GET",
    type: "media",
  });

  assert.deepEqual(controller.list(7), []);
});

test("only an active selected tab produces candidates", () => {
  const controller = createControllerWithClock(1000);
  startCapture(controller, 7);
  startCapture(controller, 9);

  captureHls(controller, {tabId: 7, requestId: "ignored"});
  captureHls(controller, {tabId: 9, requestId: "kept"});

  assert.deepEqual(controller.list(7), []);
  assert.deepEqual(
    controller.list(9).map((candidate) => candidate.id),
    ["kept"],
  );
});

test("request headers and response Content-Type join by requestId", () => {
  const now = Date.parse("2026-08-30T12:00:00Z");
  const controller = createControllerWithClock(now);
  startCapture(controller);

  controller.onBeforeRequest({
    tabId: 7,
    requestId: "r1",
    url: "https://cdn.test/stream",
    method: "GET",
    type: "xmlhttprequest",
  });
  controller.onBeforeSendHeaders({
    tabId: 7,
    requestId: "r1",
    url: "https://cdn.test/stream",
    method: "GET",
    requestHeaders: [
      {name: "Authorization", value: "Bearer secret"},
      {name: "Origin", value: "https://site.test"},
    ],
  });
  controller.onHeadersReceived({
    tabId: 7,
    requestId: "r1",
    url: "https://cdn.test/stream",
    method: "GET",
    responseHeaders: [
      {name: "content-type", value: "application/dash+xml; charset=utf-8"},
    ],
  });

  assert.deepEqual(controller.list(7), [
    {
      id: "r1",
      url: "https://cdn.test/stream",
      redactedUrl: "https://cdn.test/stream",
      kind: "dash",
      contentType: "application/dash+xml; charset=utf-8",
      method: "GET",
      headers: {Origin: "https://site.test"},
      capturedAt: now,
    },
  ]);
});

test("stop clears candidates and pending request state", () => {
  const controller = createControllerWithClock(1000);
  startCapture(controller);
  captureHls(controller);
  controller.onBeforeRequest({
    tabId: 7,
    requestId: "pending",
    url: "https://cdn.test/later",
    method: "GET",
    type: "media",
  });

  assert.equal(controller.list(7).length, 1);
  assert.equal(Object.keys(controller.snapshot().pendingRequests).length, 1);

  controller.stop(7);

  assert.deepEqual(controller.list(7), []);
  assert.deepEqual(controller.snapshot(), {sessions: {}, pendingRequests: {}});
  controller.onHeadersReceived({
    tabId: 7,
    requestId: "pending",
    url: "https://cdn.test/later",
    method: "GET",
    responseHeaders: [{name: "Content-Type", value: "video/mp4"}],
  });
  assert.deepEqual(controller.list(7), []);
});

test("snapshot and restore retain only sanitized request headers", () => {
  const now = Date.parse("2026-08-30T12:00:00Z");
  const controller = createControllerWithClock(now);
  startCapture(controller);
  controller.onBeforeSendHeaders({
    tabId: 7,
    requestId: "r1",
    url: "https://cdn.test/video",
    method: "GET",
    requestHeaders: [
      {name: "Cookie", value: "sid=secret"},
      {name: "Authorization", value: "Bearer secret"},
      {name: "Referer", value: "https://site.test/"},
    ],
  });

  const snapshot = controller.snapshot();
  const serialized = JSON.stringify(snapshot);
  assert.equal(serialized.includes("sid=secret"), false);
  assert.equal(serialized.includes("Bearer secret"), false);
  assert.equal(serialized.includes("Cookie"), false);
  assert.equal(serialized.includes("Authorization"), false);
  assert.equal(serialized.includes("Referer"), true);

  const restored = createControllerWithClock(now);
  restored.restore(snapshot);
  restored.onHeadersReceived({
    tabId: 7,
    requestId: "r1",
    url: "https://cdn.test/video",
    method: "GET",
    responseHeaders: [{name: "Content-Type", value: "video/mp4"}],
  });

  assert.deepEqual(restored.list(7)[0].headers, {
    Referer: "https://site.test/",
  });
});

test("protocol message excludes sensitive headers and matches Python V1", () => {
  const now = Date.parse("2026-08-30T12:00:00Z");
  const controller = createControllerWithClock(now);
  startCapture(controller);
  controller.onBeforeSendHeaders({
    tabId: 7,
    requestId: "r1",
    url: "https://cdn.test/a.m3u8",
    method: "GET",
    requestHeaders: [
      {name: "Cookie", value: "sid=secret"},
      {name: "Referer", value: "https://site.test/"},
    ],
  });
  controller.onHeadersReceived({
    tabId: 7,
    requestId: "r1",
    url: "https://cdn.test/a.m3u8",
    method: "GET",
    responseHeaders: [
      {name: "Content-Type", value: "application/vnd.apple.mpegurl"},
    ],
  });

  const message = controller.toProtocolMessage(7, "r1", "uuid-1");

  assert.deepEqual(message, {
    protocol_version: 1,
    type: "media_candidate",
    request_id: "uuid-1",
    captured_at: "2026-08-30T12:00:00Z",
    page: {
      url: "https://site.test/watch",
      title: "Video",
    },
    candidate: {
      url: "https://cdn.test/a.m3u8",
      kind: "hls",
      content_type: "application/vnd.apple.mpegurl",
      method: "GET",
      headers: {Referer: "https://site.test/"},
    },
    sensitive_headers_included: false,
  });
});

test("restore rejects raw or sensitive persisted header names", () => {
  const controller = createControllerWithClock(1000);

  controller.restore({
    sessions: {
      7: {
        tabId: 7,
        pageUrl: "https://site.test/watch",
        pageTitle: "Video",
        startedAt: 1000,
        candidates: [],
      },
    },
    pendingRequests: {
      r1: {
        tabId: 7,
        requestId: "r1",
        url: "https://cdn.test/a.m3u8",
        method: "GET",
        requestHeaders: {
          Cookie: "sid=secret",
          Referer: "https://site.test/",
        },
      },
    },
  });

  const persisted = JSON.stringify(controller.snapshot());
  assert.equal(persisted.includes("sid=secret"), false);
  assert.equal(persisted.includes("Cookie"), false);
  assert.equal(persisted.includes("https://site.test/"), true);
});
