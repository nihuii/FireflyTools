"use strict";

const EdgeCapturePopupModel = (() => {
  const TYPE_LABELS = Object.freeze({
    hls: "HLS",
    dash: "DASH",
    direct_mp4: "MP4",
  });
  const MESSAGES = Object.freeze({
    permission_denied: "未授权网页和 CDN 访问权限，捕获未开始。",
    capturing: "正在捕获当前标签页…",
    stopped: "捕获已停止。",
    copied: "已复制所选候选项。",
    copy_failed: "复制失败，请重试。",
    select_candidate: "请先选择一个候选项。",
  });

  function hostnameFrom(candidate) {
    const displayUrl =
      typeof candidate.redactedUrl === "string" ? candidate.redactedUrl : "";
    try {
      const parsed = new URL(displayUrl);
      return parsed.protocol === "http:" || parsed.protocol === "https:"
        ? parsed.hostname
        : "";
    } catch (_error) {
      return "";
    }
  }

  function candidateRow(candidate = {}) {
    const capturedAt = candidate.capturedAt;
    const discovered = new Date(capturedAt);
    return {
      id: typeof candidate.id === "string" ? candidate.id : "",
      type: TYPE_LABELS[candidate.kind] || "未知",
      hostname: hostnameFrom(candidate),
      redactedUrl:
        typeof candidate.redactedUrl === "string" ? candidate.redactedUrl : "",
      discoveredAt:
        capturedAt !== undefined && !Number.isNaN(discovered.getTime())
          ? discovered.toLocaleTimeString()
          : "",
    };
  }

  function userMessage(code) {
    return MESSAGES[code] || "";
  }

  return {candidateRow, userMessage};
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = EdgeCapturePopupModel;
} else {
  globalThis.EdgeCapturePopupModel = EdgeCapturePopupModel;
}
