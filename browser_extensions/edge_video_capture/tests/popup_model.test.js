const test = require("node:test");
const assert = require("node:assert/strict");
const popupModel = require("../popup_model.js");

test("candidateRow exposes type, hostname, redacted URL, and local discovery time", () => {
  const candidate = {
    id: "r1",
    kind: "hls",
    url: "https://cdn.test/master.m3u8?token=secret",
    redactedUrl: "https://cdn.test/master.m3u8?token=<redacted>",
    capturedAt: Date.parse("2026-08-30T12:00:00Z"),
  };

  const row = popupModel.candidateRow(candidate);

  assert.equal(row.id, "r1");
  assert.equal(row.type, "HLS");
  assert.equal(row.hostname, "cdn.test");
  assert.equal(row.redactedUrl, candidate.redactedUrl);
  assert.equal(row.discoveredAt, new Date(candidate.capturedAt).toLocaleTimeString());
});

test("candidateRow safely handles incomplete untrusted candidate data", () => {
  assert.deepEqual(popupModel.candidateRow({}), {
    id: "",
    type: "未知",
    hostname: "",
    redactedUrl: "",
    discoveredAt: "",
  });
});

test("userMessage maps popup status codes to concise Chinese copy", () => {
  assert.equal(
    popupModel.userMessage("permission_denied"),
    "未授权网页和 CDN 访问权限，捕获未开始。",
  );
  assert.equal(popupModel.userMessage("capturing"), "正在捕获当前标签页…");
  assert.equal(popupModel.userMessage("stopped"), "捕获已停止。");
  assert.equal(
    popupModel.userMessage("stop_failed"),
    "未找到正在捕获的会话。",
  );
  assert.equal(popupModel.userMessage("copied"), "已复制所选候选项。");
  assert.equal(popupModel.userMessage("copy_failed"), "复制失败，请重试。");
  assert.equal(popupModel.userMessage("select_candidate"), "请先选择一个候选项。");
  assert.equal(popupModel.userMessage("unexpected"), "");
});
