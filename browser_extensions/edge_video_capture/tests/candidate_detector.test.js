const test = require("node:test");
const assert = require("node:assert/strict");
const detector = require("../candidate_detector.js");

const PYTHON_V1_REJECTED_WHITESPACE_URLS = [
  ["NBSP in path", "https://cdn.test/video\u00a0name.mp4"],
  ["em space in query", "https://cdn.test/video.mp4?name=a\u2003b"],
  ["narrow NBSP in fragment", "https://cdn.test/video.mp4#a\u202fb"],
  ["ideographic space in path", "https://cdn.test/a\u3000b.mp4"],
];

const PYTHON_V1_REJECTED_USERINFO_URLS = [
  ["empty userinfo", "https://@cdn.test/video.mp4"],
  ["username userinfo", "https://user@cdn.test/video.mp4"],
  ["username and password", "https://user:pass@cdn.test/video.mp4"],
];

const PYTHON_V1_ALLOWED_AT_URLS = [
  ["raw at in path", "https://cdn.test/@folder/video.mp4"],
  ["raw at in query", "https://cdn.test/video.mp4?email=user@site.test"],
  ["raw at in fragment", "https://cdn.test/video.mp4#user@site.test"],
  ["encoded at in path", "https://cdn.test/user%40site.test/video.mp4"],
  ["encoded NBSP in path", "https://cdn.test/video%C2%A0name.mp4"],
];

function mediaUrlWithLength(length) {
  const prefix = "https://cdn.test/";
  const suffix = ".mp4";
  return prefix + "a".repeat(length - prefix.length - suffix.length) + suffix;
}

function mediaUrlWithCodePointLength(length) {
  const prefix = "https://cdn.test/";
  const suffix = ".mp4";
  return prefix + "😀".repeat(length - prefix.length - suffix.length) + suffix;
}

function assertUrlRejectedEverywhere(label, url) {
  assert.equal(
    detector.detectKind({ url, contentType: "video/mp4" }),
    null,
    `${label}: detectKind`,
  );
  assert.equal(
    detector.buildCandidate({
      url,
      contentType: "video/mp4",
      method: "GET",
      requestHeaders: [],
    }),
    null,
    `${label}: buildCandidate`,
  );
  assert.equal(detector.redactUrl(url), "", `${label}: redactUrl`);
}

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

test("uses a recognized MIME before a conflicting path extension", () => {
  assert.equal(
    detector.detectKind({
      url: "https://cdn.test/master.m3u8",
      contentType: "application/dash+xml",
    }),
    "dash",
  );
  assert.equal(
    detector.detectKind({
      url: "https://cdn.test/manifest.mpd",
      contentType: "video/mp4",
    }),
    "direct_mp4",
  );
  assert.equal(
    detector.detectKind({
      url: "https://cdn.test/master.m3u8",
      contentType: "application/octet-stream",
    }),
    "hls",
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

test("matches Python V1 raw Unicode whitespace rejection", () => {
  for (const [label, url] of PYTHON_V1_REJECTED_WHITESPACE_URLS) {
    assertUrlRejectedEverywhere(label, url);
  }
});

test("matches Python V1 authority userinfo rejection", () => {
  for (const [label, url] of PYTHON_V1_REJECTED_USERINFO_URLS) {
    assertUrlRejectedEverywhere(label, url);
  }
});

test("allows at signs outside authority and percent-encoded whitespace", () => {
  for (const [label, url] of PYTHON_V1_ALLOWED_AT_URLS) {
    assert.equal(
      detector.detectKind({ url, contentType: "video/mp4" }),
      "direct_mp4",
      `${label}: detectKind`,
    );
    assert.notEqual(
      detector.buildCandidate({
        url,
        contentType: "video/mp4",
        method: "GET",
        requestHeaders: [],
      }),
      null,
      `${label}: buildCandidate`,
    );
    assert.notEqual(detector.redactUrl(url), "", `${label}: redactUrl`);
  }
});

test("accepts 16384-character URLs and rejects longer URLs everywhere", () => {
  const boundaryUrl = mediaUrlWithLength(16 * 1024);
  const overlongUrl = mediaUrlWithLength(16 * 1024 + 1);

  assert.equal(boundaryUrl.length, 16 * 1024);
  assert.equal(
    detector.detectKind({ url: boundaryUrl, contentType: "" }),
    "direct_mp4",
  );
  assert.equal(
    detector.buildCandidate({
      url: boundaryUrl,
      contentType: "",
      method: "GET",
      requestHeaders: [],
    }).url,
    boundaryUrl,
  );
  assert.equal(detector.redactUrl(boundaryUrl), boundaryUrl);

  assert.equal(overlongUrl.length, 16 * 1024 + 1);
  assert.equal(detector.detectKind({ url: overlongUrl, contentType: "" }), null);
  assert.equal(
    detector.buildCandidate({
      url: overlongUrl,
      contentType: "",
      method: "GET",
      requestHeaders: [],
    }),
    null,
  );
  assert.equal(detector.redactUrl(overlongUrl), "");
});

test("counts V1 URL characters as Unicode code points", () => {
  const boundaryUrl = mediaUrlWithCodePointLength(16 * 1024);
  const overlongUrl = mediaUrlWithCodePointLength(16 * 1024 + 1);

  assert.equal(Array.from(boundaryUrl).length, 16 * 1024);
  assert.equal(boundaryUrl.length > 16 * 1024, true);
  assert.equal(
    detector.detectKind({ url: boundaryUrl, contentType: "" }),
    "direct_mp4",
  );
  assert.equal(
    detector.detectKind({ url: overlongUrl, contentType: "" }),
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

test("rejects inherited header names and non-string values", () => {
  const original = [
    { name: "__proto__", value: "polluted" },
    { name: "constructor", value: "polluted" },
    { name: "toString", value: "polluted" },
    { name: "Accept", value: 123 },
    { name: "Origin", value: "https://site.test\r\nX-Evil: 1" },
    { name: "Range", value: "bytes=0-" },
  ];

  const headers = detector.sanitizeRequestHeaders(original);
  original[5].value = "bytes=100-";

  assert.deepEqual(headers, { Range: "bytes=0-" });
});

test("redacts common secret query values for display", () => {
  assert.equal(
    detector.redactUrl(
      "https://cdn.test/master.m3u8?token=secret&quality=1080&sig=opaque#video",
    ),
    "https://cdn.test/master.m3u8?token=<redacted>&quality=1080&sig=<redacted>",
  );
});

test("redacts decoded AWS query names and removes the entire fragment", () => {
  const redacted = detector.redactUrl(
    "https://cdn.test/master.m3u8?%58-Amz-%53ignature=aws-secret" +
      "&X-Amz-Credential=credential-secret" +
      "&X-Amz-Security-Token=security-secret" +
      "&Expires=expiry-secret&Policy=policy-secret&quality=1080#fragment-secret",
  );
  const parsed = new URL(redacted);

  assert.equal(parsed.hash, "");
  assert.equal(parsed.searchParams.get("X-Amz-Signature"), "<redacted>");
  assert.equal(parsed.searchParams.get("X-Amz-Credential"), "<redacted>");
  assert.equal(parsed.searchParams.get("X-Amz-Security-Token"), "<redacted>");
  assert.equal(parsed.searchParams.get("Expires"), "<redacted>");
  assert.equal(parsed.searchParams.get("Policy"), "<redacted>");
  assert.equal(parsed.searchParams.get("quality"), "1080");
  for (const secret of [
    "aws-secret",
    "credential-secret",
    "security-secret",
    "expiry-secret",
    "policy-secret",
    "fragment-secret",
  ]) {
    assert.equal(redacted.includes(secret), false);
  }
});

test("redacts every common credential query key case-insensitively", () => {
  const sensitiveNames = [
    "ACCESS_TOKEN",
    "Auth",
    "authorization",
    "token",
    "key",
    "sig",
    "signature",
    "expiry",
  ];
  const query = sensitiveNames
    .map((name, index) => `${name}=secret-${index}`)
    .concat("quality=1080")
    .join("&");
  const parsed = new URL(
    detector.redactUrl(`https://cdn.test/master.m3u8?${query}`),
  );

  for (const name of sensitiveNames) {
    assert.equal(parsed.searchParams.get(name), "<redacted>");
  }
  assert.equal(parsed.searchParams.get("quality"), "1080");
  assert.equal(parsed.toString().includes("secret-"), false);
});

test("returns an empty display URL for malformed or non-string input", () => {
  assert.equal(
    detector.redactUrl("https://[::1?token=query-secret#fragment-secret"),
    "",
  );
  assert.equal(detector.redactUrl({ url: "https://cdn.test/a.mp4" }), "");
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

test("requires an explicit GET or HEAD method", () => {
  const details = {
    url: "https://cdn.test/video.mp4",
    contentType: "video/mp4",
    requestHeaders: [],
  };

  assert.equal(detector.buildCandidate(details), null);
  assert.equal(detector.buildCandidate({ ...details, method: 123 }), null);
  assert.equal(detector.buildCandidate({ ...details, method: "POST" }), null);
  assert.equal(detector.buildCandidate({ ...details, method: "get" }).method, "GET");
  assert.equal(
    detector.buildCandidate({ ...details, method: "head" }).method,
    "HEAD",
  );
});
