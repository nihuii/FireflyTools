const test = require("node:test");
const assert = require("node:assert/strict");
const store = require("../capture_store.js");

function candidate(kind, url, contentType = "") {
  return {
    kind,
    url,
    contentType,
    method: "GET",
    headers: {},
  };
}

function fixedClock(start = 1_000) {
  let now = start;
  return {
    now: () => now,
    advance: (milliseconds) => {
      now += milliseconds;
    },
  };
}

test("accepts candidates only from the selected tab", () => {
  const clock = fixedClock();
  const session = store.createSession({ tabId: 7, clock: clock.now });

  assert.equal(
    store.upsertCandidate(session, {
      tabId: 8,
      candidate: candidate("hls", "https://cdn.test/ignored.m3u8"),
    }),
    false,
  );
  assert.equal(
    store.upsertCandidate(session, {
      tabId: 7,
      candidate: candidate("hls", "https://cdn.test/kept.m3u8"),
    }),
    true,
  );
  assert.deepEqual(
    store.listCandidates(session).map((item) => item.url),
    ["https://cdn.test/kept.m3u8"],
  );
});

test("deduplicates candidates by kind and URL", () => {
  const clock = fixedClock();
  const session = store.createSession({ tabId: 7, clock: clock.now });
  const url = "https://cdn.test/video.mp4";

  store.upsertCandidate(session, {
    tabId: 7,
    candidate: candidate("direct_mp4", url, "video/mp4"),
  });
  clock.advance(1);
  store.upsertCandidate(session, {
    tabId: 7,
    candidate: candidate("direct_mp4", url, "video/mp4; codecs=avc1"),
  });
  store.upsertCandidate(session, {
    tabId: 7,
    candidate: candidate("hls", url, "application/vnd.apple.mpegurl"),
  });

  const candidates = store.listCandidates(session);
  assert.equal(candidates.length, 2);
  assert.equal(
    candidates.find((item) => item.kind === "direct_mp4").contentType,
    "video/mp4; codecs=avc1",
  );
});

test("keeps HLS and DASH ahead of MP4 at the 50-item boundary", () => {
  const clock = fixedClock();
  const session = store.createSession({ tabId: 7, clock: clock.now });

  for (let index = 0; index < 50; index += 1) {
    store.upsertCandidate(session, {
      tabId: 7,
      candidate: candidate(
        "direct_mp4",
        `https://cdn.test/video-${index}.mp4`,
      ),
    });
    clock.advance(1);
  }
  store.upsertCandidate(session, {
    tabId: 7,
    candidate: candidate("hls", "https://cdn.test/master.m3u8"),
  });
  store.upsertCandidate(session, {
    tabId: 7,
    candidate: candidate("dash", "https://cdn.test/manifest.mpd"),
  });

  const candidates = store.listCandidates(session);
  assert.equal(candidates.length, 50);
  assert.equal(candidates[0].kind, "hls");
  assert.equal(candidates[1].kind, "dash");
  assert.equal(
    candidates.filter((item) => item.kind === "direct_mp4").length,
    48,
  );
  const retainedUrls = new Set(candidates.map((item) => item.url));
  assert.equal(retainedUrls.has("https://cdn.test/video-0.mp4"), false);
  assert.equal(retainedUrls.has("https://cdn.test/video-1.mp4"), false);
  assert.equal(retainedUrls.has("https://cdn.test/video-48.mp4"), true);
  assert.equal(retainedUrls.has("https://cdn.test/video-49.mp4"), true);
});

test("returns no candidates after stop", () => {
  const clock = fixedClock();
  const session = store.createSession({ tabId: 7, clock: clock.now });
  store.upsertCandidate(session, {
    tabId: 7,
    candidate: candidate("hls", "https://cdn.test/master.m3u8"),
  });

  store.stopSession(session);

  assert.deepEqual(store.listCandidates(session), []);
  assert.equal(
    store.upsertCandidate(session, {
      tabId: 7,
      candidate: candidate("dash", "https://cdn.test/manifest.mpd"),
    }),
    false,
  );
});

test("returns no candidates after the selected tab is closed", () => {
  const clock = fixedClock();
  const session = store.createSession({ tabId: 7, clock: clock.now });
  store.upsertCandidate(session, {
    tabId: 7,
    candidate: candidate("hls", "https://cdn.test/master.m3u8"),
  });

  store.expireSession(session);

  assert.deepEqual(store.listCandidates(session), []);
});

test("expires at startedAt plus 300000 milliseconds", () => {
  const clock = fixedClock(10_000);
  const session = store.createSession({ tabId: 7, clock: clock.now });
  store.upsertCandidate(session, {
    tabId: 7,
    candidate: candidate("hls", "https://cdn.test/master.m3u8"),
  });

  clock.advance(299_999);
  assert.equal(store.listCandidates(session).length, 1);
  clock.advance(1);
  assert.deepEqual(store.listCandidates(session), []);
});
