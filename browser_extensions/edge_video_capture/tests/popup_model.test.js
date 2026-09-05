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

test("userMessage maps native host failures while preserving copy fallback guidance", () => {
  assert.equal(
    popupModel.userMessage("HOST_NOT_INSTALLED"),
    "Edge 连接组件未安装；请查看安装说明，或使用复制候选 JSON。",
  );
  assert.equal(
    popupModel.userMessage("APP_NOT_RUNNING"),
    "请先打开 FireflyTools，再点击等待 Edge 捕获。",
  );
  assert.equal(
    popupModel.userMessage("APP_NOT_WAITING"),
    "FireflyTools 尚未进入等待捕获状态。",
  );
  assert.equal(
    popupModel.userMessage("UNSUPPORTED_VERSION"),
    "扩展与 FireflyTools 协议版本不一致，请升级对应组件。",
  );
  assert.equal(
    popupModel.userMessage("TIMEOUT"),
    "连接组件响应超时；候选仍可复制。",
  );
});

test("unmapped native host failures use the generic send fallback", () => {
  for (const code of [
    "RECEIVER_ERROR",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "unknown",
  ]) {
    assert.equal(
      popupModel.userMessage(code, "send_failed"),
      "发送失败；候选仍可复制。",
    );
  }
});
