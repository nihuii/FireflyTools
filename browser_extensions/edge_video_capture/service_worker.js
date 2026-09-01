"use strict";

importScripts("candidate_detector.js", "capture_store.js", "capture_controller.js");

const controller = EdgeCaptureController.createCaptureController({
  detector: EdgeCaptureDetector,
  store: EdgeCaptureStore,
  now: () => Date.now(),
});
const CLEANUP_ALARM = "edge-capture-cleanup";

function persistController() {
  return chrome.storage.session.set({edgeCaptureState: controller.snapshot()});
}

const ready = chrome.storage.session.get("edgeCaptureState").then(
  ({edgeCaptureState}) => {
    controller.restore(edgeCaptureState || {sessions: {}, pendingRequests: {}});
  },
);

function applyAndPersist(operation) {
  return ready.then(() => {
    const result = operation();
    return persistController().then(() => result);
  });
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    void applyAndPersist(() => controller.onBeforeRequest(details));
  },
  {urls: ["http://*/*", "https://*/*"]},
);

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    void applyAndPersist(() => controller.onBeforeSendHeaders(details));
  },
  {urls: ["http://*/*", "https://*/*"]},
  ["requestHeaders", "extraHeaders"],
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    void applyAndPersist(() => controller.onHeadersReceived(details));
  },
  {urls: ["http://*/*", "https://*/*"]},
  ["responseHeaders"],
);

chrome.alarms.create(CLEANUP_ALARM, {periodInMinutes: 1});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === CLEANUP_ALARM) {
    void applyAndPersist(() => controller.cleanup());
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void applyAndPersist(() => controller.expire(tabId));
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const operation = ready.then(async () => {
    switch (message && message.type) {
      case "capture:start":
        controller.start({
          tabId: message.tabId,
          pageUrl: message.pageUrl,
          pageTitle: message.pageTitle,
        });
        await persistController();
        return {ok: true};
      case "capture:stop":
        const stopped = controller.stop();
        await persistController();
        return {ok: stopped};
      case "capture:list":
        return {ok: true, candidates: controller.list(message.tabId)};
      case "capture:protocol":
        return {
          ok: true,
          message: controller.toProtocolMessage(
            message.tabId,
            message.candidateId,
            message.requestId,
          ),
        };
      default:
        return {ok: false, error: "unsupported_message"};
    }
  });

  void operation.then(sendResponse, () => {
    sendResponse({ok: false, error: "operation_failed"});
  });
  return true;
});
