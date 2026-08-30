const test = require("node:test");
const assert = require("node:assert/strict");
const detector = require("../candidate_detector.js");

test("classifies HLS, MP4, and DASH from URL or MIME", () => {
  assert.equal(
    detector.detectKind({ url: "https://cdn.test/a.m3u8", contentType: "" }),
    "hls",
  );
  assert.equal(
    detector.detectKind({ url: "https://cdn.test/a", contentType: "video/mp4" }),
    "direct_mp4",
  );
  assert.equal(
    detector.detectKind({
      url: "https://cdn.test/a",
      contentType: "application/dash+xml",
    }),
    "dash",
  );
});

test("rejects unsafe schemes, ads, and unsupported resources", () => {
  assert.equal(
    detector.detectKind({
      url: "blob:https://site.test/id",
      contentType: "video/mp4",
    }),
    null,
  );
  assert.equal(
    detector.detectKind({
      url: "https://doubleclick.test/ad.mp4",
      contentType: "video/mp4",
    }),
    null,
  );
  assert.equal(
    detector.detectKind({
      url: "https://cdn.test/logo.png",
      contentType: "image/png",
    }),
    null,
  );
});

test("keeps only V1 safe request headers", () => {
  const headers = detector.sanitizeRequestHeaders([
    { name: "Referer", value: "https://site.test/" },
    { name: "Cookie", value: "sid=secret" },
    { name: "Authorization", value: "Bearer secret" },
  ]);
  assert.deepEqual(headers, { Referer: "https://site.test/" });
});

test("canonicalizes all allowlisted headers and drops injected values", () => {
  const original = [
    { name: "referer", value: "https://site.test/" },
    { name: "ORIGIN", value: "https://site.test" },
    { name: "user-agent", value: "Edge UA" },
    { name: "Accept", value: "video/*" },
    { name: "accept-language", value: "zh-CN" },
    { name: "range", value: "bytes=0-" },
    { name: "X-Custom", value: "not allowed" },
    { name: "Referer", value: "https://site.test/\r\nX-Evil: 1" },
  ];

  const headers = detector.sanitizeRequestHeaders(original);
  original[0].value = "https://changed.test/";

  assert.deepEqual(headers, {
    Referer: "https://site.test/",
    Origin: "https://site.test",
    "User-Agent": "Edge UA",
    Accept: "video/*",
    "Accept-Language": "zh-CN",
    Range: "bytes=0-",
  });
});

test("redacts common secret query values for display", () => {
  assert.equal(
    detector.redactUrl(
      "https://cdn.test/master.m3u8?token=secret&quality=1080&sig=opaque#video",
    ),
    "https://cdn.test/master.m3u8?token=<redacted>&quality=1080&sig=<redacted>#video",
  );
});

test("builds a safe candidate without retaining request header objects", () => {
  const requestHeaders = [
    { name: "Referer", value: "https://site.test/watch" },
    { name: "Cookie", value: "sid=secret" },
  ];
  const candidate = detector.buildCandidate({
    url: "https://cdn.test/master.m3u8?token=secret",
    contentType: "application/vnd.apple.mpegurl",
    method: "get",
    requestHeaders,
  });
  requestHeaders[0].value = "https://changed.test/";

  assert.deepEqual(candidate, {
    url: "https://cdn.test/master.m3u8?token=secret",
    redactedUrl: "https://cdn.test/master.m3u8?token=<redacted>",
    kind: "hls",
    contentType: "application/vnd.apple.mpegurl",
    method: "GET",
    headers: { Referer: "https://site.test/watch" },
  });
  assert.equal(
    detector.buildCandidate({
      url: "https://cdn.test/logo.png",
      contentType: "image/png",
      method: "GET",
      requestHeaders: [],
    }),
    null,
  );
});
