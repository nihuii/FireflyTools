"use strict";

const EdgeCaptureNativeClient = (() => {
  const HOST_NAME = "com.fireflytools.video_capture";
  const TIMEOUT_MS = 10_000;
  const MAX_MESSAGE_BYTES = 256 * 1024;
  const MAX_URL_CHARS = 16 * 1024;
  const SAFE_HEADERS = new Set([
    "Referer",
    "Origin",
    "User-Agent",
    "Accept",
    "Accept-Language",
    "Range",
  ]);

  class NativeClientError extends Error {
    constructor(code) {
      super(code);
      this.name = "NativeClientError";
      this.code = code;
    }
  }

  function isSafeHttpUrl(value) {
    if (
      typeof value !== "string" ||
      value.length === 0 ||
      Array.from(value).length > MAX_URL_CHARS ||
      /[\u0000-\u0020\u007f]|\p{White_Space}/u.test(value)
    ) {
      return false;
    }
    try {
      const parsed = new URL(value);
      return (
        (parsed.protocol === "http:" || parsed.protocol === "https:") &&
        Boolean(parsed.hostname) &&
        !parsed.username &&
        !parsed.password &&
        !/^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^/?#]*@/.test(value)
      );
    } catch (_error) {
      return false;
    }
  }

  function isValidMessage(message) {
    if (
      !message ||
      typeof message !== "object" ||
      Array.isArray(message) ||
      message.protocol_version !== 1 ||
      message.type !== "media_candidate" ||
      typeof message.request_id !== "string" ||
      message.request_id.length === 0 ||
      typeof message.captured_at !== "string" ||
      Number.isNaN(Date.parse(message.captured_at)) ||
      message.sensitive_headers_included !== false ||
      !message.page ||
      typeof message.page !== "object" ||
      !message.candidate ||
      typeof message.candidate !== "object"
    ) {
      return false;
    }
    const candidate = message.candidate;
    if (
      !isSafeHttpUrl(message.page.url) ||
      typeof message.page.title !== "string" ||
      message.page.title.length > 512 ||
      !isSafeHttpUrl(candidate.url) ||
      !["hls", "dash", "direct_mp4"].includes(candidate.kind) ||
      typeof candidate.content_type !== "string" ||
      !["GET", "HEAD"].includes(candidate.method) ||
      !candidate.headers ||
      typeof candidate.headers !== "object" ||
      Array.isArray(candidate.headers)
    ) {
      return false;
    }
    for (const [name, value] of Object.entries(candidate.headers)) {
      if (
        !SAFE_HEADERS.has(name) ||
        typeof value !== "string" ||
        /[\r\n]/.test(value)
      ) {
        return false;
      }
    }
    try {
      return new TextEncoder().encode(JSON.stringify(message)).length <= MAX_MESSAGE_BYTES;
    } catch (_error) {
      return false;
    }
  }

  function createNativeClient({
    runtime = globalThis.chrome && globalThis.chrome.runtime,
    setTimeoutFn = globalThis.setTimeout,
    clearTimeoutFn = globalThis.clearTimeout,
  } = {}) {
    const pending = new Map();
    let port = null;

    function rejectAll(code) {
      for (const {reject, timer} of pending.values()) {
        clearTimeoutFn(timer);
        reject(new NativeClientError(code));
      }
      pending.clear();
    }

    function handleMessage(ack) {
      if (
        !ack ||
        ack.type !== "ack" ||
        typeof ack.request_id !== "string"
      ) {
        return;
      }
      const request = pending.get(ack.request_id);
      if (!request) {
        return;
      }
      pending.delete(ack.request_id);
      clearTimeoutFn(request.timer);
      request.resolve(ack);
    }

    function handleDisconnect() {
      if (runtime) {
        void runtime.lastError;
      }
      port = null;
      rejectAll("HOST_NOT_INSTALLED");
    }

    function connect() {
      if (port) {
        return port;
      }
      if (!runtime || typeof runtime.connectNative !== "function") {
        throw new NativeClientError("HOST_NOT_INSTALLED");
      }
      try {
        port = runtime.connectNative(HOST_NAME);
        port.onMessage.addListener(handleMessage);
        port.onDisconnect.addListener(handleDisconnect);
        return port;
      } catch (_error) {
        port = null;
        throw new NativeClientError("HOST_NOT_INSTALLED");
      }
    }

    function send(message) {
      if (!isValidMessage(message) || pending.has(message.request_id)) {
        return Promise.reject(new NativeClientError("INVALID_MESSAGE"));
      }
      let activePort;
      try {
        activePort = connect();
      } catch (error) {
        return Promise.reject(error);
      }
      return new Promise((resolve, reject) => {
        const requestId = message.request_id;
        const timer = setTimeoutFn(() => {
          if (!pending.delete(requestId)) {
            return;
          }
          reject(new NativeClientError("TIMEOUT"));
        }, TIMEOUT_MS);
        pending.set(requestId, {resolve, reject, timer});
        try {
          activePort.postMessage(message);
        } catch (_error) {
          pending.delete(requestId);
          clearTimeoutFn(timer);
          reject(new NativeClientError("HOST_NOT_INSTALLED"));
        }
      });
    }

    return {pending, send};
  }

  return {HOST_NAME, TIMEOUT_MS, NativeClientError, createNativeClient};
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = EdgeCaptureNativeClient;
} else {
  globalThis.EdgeCaptureNativeClient = EdgeCaptureNativeClient;
}
