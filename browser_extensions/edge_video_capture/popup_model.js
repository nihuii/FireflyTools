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
    capturing_reloaded: "已开始捕获并重新加载页面。",
    capture_start_failed: "捕获未能开始，请重试。",
    reload_failed: "捕获已开始，但页面重新加载失败；请手动刷新或点击播放。",
    stopped: "捕获已停止。",
    stop_failed: "未找到正在捕获的会话。",
    copied: "已复制所选候选项。",
    sent: "已发送到 FireflyTools。",
    copy_failed: "复制失败，请重试。",
    select_candidate: "请先选择一个候选项。",
    send_failed: "发送失败；候选仍可复制。",
    HOST_NOT_INSTALLED: "Edge 连接组件未安装；请查看安装说明，或使用复制候选 JSON。",
    APP_NOT_RUNNING: "请先打开 FireflyTools，再点击等待 Edge 捕获。",
    APP_NOT_WAITING: "FireflyTools 尚未进入等待捕获状态。",
    UNSUPPORTED_VERSION: "扩展与 FireflyTools 协议版本不一致，请升级对应组件。",
    TIMEOUT: "连接组件响应超时；候选仍可复制。",
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

  function userMessage(code, fallbackCode) {
    return MESSAGES[code] || MESSAGES[fallbackCode] || "";
  }

  return {candidateRow, userMessage};
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = EdgeCapturePopupModel;
} else {
  globalThis.EdgeCapturePopupModel = EdgeCapturePopupModel;
}
