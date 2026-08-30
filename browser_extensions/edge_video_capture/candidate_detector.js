"use strict";

const EdgeCaptureDetector = (() => {
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
  const SECRET_QUERY_PARAMETER =
    /([?&](?:access_token|authorization|auth|expires?|key|signature|sig|token)=)[^&#]*/gi;

  function parseSafeHttpUrl(rawUrl) {
    if (
      typeof rawUrl !== "string" ||
      rawUrl.length === 0 ||
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

    if (HLS_CONTENT_TYPES.has(mime) || pathname.endsWith(".m3u8")) {
      return "hls";
    }
    if (DASH_CONTENT_TYPES.has(mime) || pathname.endsWith(".mpd")) {
      return "dash";
    }
    if (MP4_CONTENT_TYPES.has(mime) || pathname.endsWith(".mp4")) {
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
      const canonicalName = SAFE_HEADER_NAMES[header.name.toLowerCase()];
      if (canonicalName) {
        safeHeaders[canonicalName] = header.value;
      }
    }
    return safeHeaders;
  }

  function redactUrl(url) {
    if (typeof url !== "string") {
      return "";
    }
    return url.replace(SECRET_QUERY_PARAMETER, "$1<redacted>");
  }

  function buildCandidate(details = {}) {
    const kind = detectKind(details);
    const method =
      typeof details.method === "string" ? details.method.toUpperCase() : "GET";
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
