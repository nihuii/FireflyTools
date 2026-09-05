const test = require("node:test");
const assert = require("node:assert/strict");
const nativeClient = require("../native_client.js");

function chromeEvent() {
  const listeners = [];
  return {
    addListener(listener) {
      listeners.push(listener);
    },
    emit(value) {
      for (const listener of listeners) {
        listener(value);
      }
    },
  };
}

function validMessage(requestId = "request-1") {
  return {
    protocol_version: 1,
    type: "media_candidate",
    request_id: requestId,
    captured_at: "2026-08-30T12:00:00Z",
    page: {url: "https://site.test/watch", title: "Video"},
    candidate: {
      url: "https://cdn.test/master.m3u8",
      kind: "hls",
      content_type: "application/vnd.apple.mpegurl",
      method: "GET",
      headers: {Referer: "https://site.test/"},
    },
    sensitive_headers_included: false,
  };
}

function fakeRuntime() {
  const port = {
    onMessage: chromeEvent(),
    onDisconnect: chromeEvent(),
    posted: [],
    postMessage(message) {
      this.posted.push(message);
    },
  };
  const hostNames = [];
  const runtime = {
    lastError: null,
    connectNative(hostName) {
      hostNames.push(hostName);
      return port;
    },
  };
  return {runtime, port, hostNames};
}

test("connects to the fixed host and resolves only the matching ack", async () => {
  const {runtime, port, hostNames} = fakeRuntime();
  const client = nativeClient.createNativeClient({runtime});
  const pending = client.send(validMessage("request-1"));

  assert.deepEqual(hostNames, ["com.fireflytools.video_capture"]);
  assert.deepEqual(port.posted, [validMessage("request-1")]);

  port.onMessage.emit({
    type: "ack",
    request_id: "different-request",
    ok: true,
    code: "ACCEPTED",
  });
  port.onMessage.emit({
    type: "ack",
    request_id: "request-1",
    ok: true,
    code: "ACCEPTED",
  });

  assert.deepEqual(await pending, {
    type: "ack",
    request_id: "request-1",
    ok: true,
    code: "ACCEPTED",
  });
  assert.equal(client.pending.size, 0);
});

test("ignores malformed matching acks until one valid ack settles the request", async () => {
  const {runtime, port} = fakeRuntime();
  const scheduled = [];
  const cleared = [];
  const client = nativeClient.createNativeClient({
    runtime,
    setTimeoutFn(callback, delay) {
      scheduled.push({callback, delay});
      return scheduled.length;
    },
    clearTimeoutFn(timer) {
      cleared.push(timer);
    },
  });
  let settlementCount = 0;
  const pending = client.send(validMessage("validated-ack")).then((ack) => {
    settlementCount += 1;
    return ack;
  });

  for (const malformed of [
    {type: "ack", request_id: "validated-ack", code: "ACCEPTED"},
    {type: "ack", request_id: "validated-ack", ok: "yes", code: "ACCEPTED"},
    {type: "ack", request_id: "validated-ack", ok: true, code: ""},
  ]) {
    port.onMessage.emit(malformed);
  }
  await Promise.resolve();

  assert.equal(settlementCount, 0);
  assert.equal(client.pending.size, 1);
  assert.deepEqual(cleared, []);
  assert.equal(scheduled.length, 1);

  port.onMessage.emit({
    type: "ack",
    request_id: "validated-ack",
    ok: false,
    code: "RECEIVER_ERROR",
  });
  assert.deepEqual(await pending, {
    type: "ack",
    request_id: "validated-ack",
    ok: false,
    code: "RECEIVER_ERROR",
  });
  port.onMessage.emit({
    type: "ack",
    request_id: "validated-ack",
    ok: true,
    code: "ACCEPTED",
  });
  await Promise.resolve();

  assert.equal(settlementCount, 1);
  assert.equal(client.pending.size, 0);
  assert.deepEqual(cleared, [1]);
});

test("uses a 10-second timeout and removes the timed-out request", async () => {
  const {runtime} = fakeRuntime();
  const scheduled = [];
  const client = nativeClient.createNativeClient({
    runtime,
    setTimeoutFn(callback, delay) {
      scheduled.push({callback, delay});
      return scheduled.length;
    },
    clearTimeoutFn() {},
  });

  const pending = client.send(validMessage("slow-request"));
  assert.equal(scheduled[0].delay, 10_000);
  scheduled[0].callback();

  await assert.rejects(pending, {code: "TIMEOUT"});
  assert.equal(client.pending.size, 0);
});

test("disconnect rejects every pending request as HOST_NOT_INSTALLED", async () => {
  const {runtime, port} = fakeRuntime();
  const client = nativeClient.createNativeClient({runtime});
  const first = client.send(validMessage("first"));
  const second = client.send(validMessage("second"));

  runtime.lastError = {message: "Specified native messaging host not found."};
  port.onDisconnect.emit();

  await assert.rejects(first, {code: "HOST_NOT_INSTALLED"});
  await assert.rejects(second, {code: "HOST_NOT_INSTALLED"});
  assert.equal(client.pending.size, 0);
});

test("rejects malformed protocol messages before posting them", async () => {
  const {runtime, port} = fakeRuntime();
  const client = nativeClient.createNativeClient({runtime});
  const invalid = validMessage("");
  invalid.sensitive_headers_included = true;

  await assert.rejects(client.send(invalid), {code: "INVALID_MESSAGE"});
  assert.deepEqual(port.posted, []);
});
