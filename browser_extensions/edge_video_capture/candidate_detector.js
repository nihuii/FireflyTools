"use strict";

const EdgeCaptureDetector = (() => {
  const MAX_URL_CHARS = 16 * 1024;
  const SAFE_HEADER_NAMES = Object.freeze({
    referer: "Referer",
    origin: "Origin",
    "user-agent": "User-Agent",
    accept: "Accept",
    "accept-language": "Accept-Language",
    range: "Range",
  });
  const HLS_CONTENT_TYPES = new Set([
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
  ]);
  const DASH_CONTENT_TYPES = new Set(["application/dash+xml"]);
  const MP4_CONTENT_TYPES = new Set(["video/mp4", "application/mp4"]);
  const SENSITIVE_QUERY_NAMES = new Set([
    "access_token",
    "auth",
    "authorization",
    "token",
    "key",
    "sig",
    "signature",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "expires",
    "expiry",
    "policy",
  ]);

  function exceedsUrlCharacterLimit(url) {
    let count = 0;
    for (const _character of url) {
      count += 1;
      if (count > MAX_URL_CHARS) {
        return true;
      }
    }
    return false;
  }

  function parseSafeHttpUrl(rawUrl) {
    if (
      typeof rawUrl !== "string" ||
      rawUrl.length === 0 ||
      exceedsUrlCharacterLimit(rawUrl) ||
      /[\u0000-\u0020\u007f]/.test(rawUrl)
    ) {
      return null;
    }
    try {
      const parsed = new URL(rawUrl);
      if (
        (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
        !parsed.hostname ||
        parsed.username ||
        parsed.password
      ) {
        return null;
      }
      return parsed;
    } catch (_error) {
      return null;
    }
  }

  function isAdvertisingHost(hostname) {
    return /(^|\.)(?:doubleclick|googlesyndication|googleadservices)\./i.test(
      hostname,
    );
  }

  function detectKind({ url, contentType = "" } = {}) {
    const parsed = parseSafeHttpUrl(url);
    if (!parsed || isAdvertisingHost(parsed.hostname)) {
      return null;
    }

    const mime =
      typeof contentType === "string"
        ? contentType.split(";", 1)[0].trim().toLowerCase()
        : "";
    const pathname = parsed.pathname.toLowerCase();

    if (HLS_CONTENT_TYPES.has(mime)) {
      return "hls";
    }
    if (DASH_CONTENT_TYPES.has(mime)) {
      return "dash";
    }
    if (MP4_CONTENT_TYPES.has(mime)) {
      return "direct_mp4";
    }
    if (pathname.endsWith(".m3u8")) {
      return "hls";
    }
    if (pathname.endsWith(".mpd")) {
      return "dash";
    }
    if (pathname.endsWith(".mp4")) {
      return "direct_mp4";
    }
    return null;
  }

  function sanitizeRequestHeaders(requestHeaders) {
    const safeHeaders = {};
    if (!Array.isArray(requestHeaders)) {
      return safeHeaders;
    }

    for (const header of requestHeaders) {
      if (
        !header ||
        typeof header.name !== "string" ||
        typeof header.value !== "string" ||
        /[\r\n]/.test(header.value)
      ) {
        continue;
      }
      const loweredName = header.name.toLowerCase();
      if (!Object.hasOwn(SAFE_HEADER_NAMES, loweredName)) {
        continue;
      }
      safeHeaders[SAFE_HEADER_NAMES[loweredName]] = header.value;
    }
    return safeHeaders;
  }

  function redactUrl(url) {
    const parsed = parseSafeHttpUrl(url);
    if (!parsed) {
      return "";
    }
    parsed.hash = "";
    for (const [name] of Array.from(parsed.searchParams)) {
      if (SENSITIVE_QUERY_NAMES.has(name.toLowerCase())) {
        parsed.searchParams.set(name, "<redacted>");
      }
    }
    return parsed.toString().replaceAll("%3Credacted%3E", "<redacted>");
  }

  function buildCandidate(details = {}) {
    const kind = detectKind(details);
    const method =
      typeof details.method === "string" ? details.method.toUpperCase() : "";
    if (!kind || (method !== "GET" && method !== "HEAD")) {
      return null;
    }

    return {
      url: details.url,
      redactedUrl: redactUrl(details.url),
      kind,
      contentType:
        typeof details.contentType === "string" ? details.contentType : "",
      method,
      headers: sanitizeRequestHeaders(details.requestHeaders),
    };
  }

  return {
    detectKind,
    sanitizeRequestHeaders,
    redactUrl,
    buildCandidate,
  };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = EdgeCaptureDetector;
} else {
  globalThis.EdgeCaptureDetector = EdgeCaptureDetector;
}
