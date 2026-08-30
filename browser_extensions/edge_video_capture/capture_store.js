"use strict";

const EdgeCaptureStore = (() => {
  const MAX_CANDIDATES = 50;
  const SESSION_TTL_MS = 300000;
  const KIND_PRIORITY = Object.freeze({
    hls: 2,
    dash: 2,
    direct_mp4: 1,
  });

  function clearSession(session) {
    session.active = false;
    session.candidates = [];
  }

  function isExpired(session) {
    if (!session || !session.active) {
      return true;
    }
    if (session.clock() >= session.startedAt + SESSION_TTL_MS) {
      clearSession(session);
      return true;
    }
    return false;
  }

  function createSession({ tabId, clock = Date.now } = {}) {
    if (!Number.isInteger(tabId)) {
      throw new TypeError("tabId must be an integer");
    }
    if (typeof clock !== "function") {
      throw new TypeError("clock must be a function");
    }

    return {
      tabId,
      startedAt: clock(),
      clock,
      active: true,
      nextSequence: 0,
      candidates: [],
    };
  }

  function cloneCandidate(candidate) {
    return {
      ...candidate,
      headers:
        candidate.headers && typeof candidate.headers === "object"
          ? { ...candidate.headers }
          : {},
    };
  }

  function compareCandidates(left, right) {
    const priorityDifference =
      (KIND_PRIORITY[right.kind] || 0) - (KIND_PRIORITY[left.kind] || 0);
    return priorityDifference || left.sequence - right.sequence;
  }

  function upsertCandidate(session, { tabId, candidate } = {}) {
    if (
      isExpired(session) ||
      tabId !== session.tabId ||
      !candidate ||
      typeof candidate.url !== "string" ||
      !Object.hasOwn(KIND_PRIORITY, candidate.kind)
    ) {
      return false;
    }

    const existingIndex = session.candidates.findIndex(
      (item) => item.kind === candidate.kind && item.url === candidate.url,
    );
    if (existingIndex >= 0) {
      session.candidates[existingIndex] = {
        ...cloneCandidate(candidate),
        sequence: session.candidates[existingIndex].sequence,
      };
    } else {
      session.candidates.push({
        ...cloneCandidate(candidate),
        sequence: session.nextSequence,
      });
      session.nextSequence += 1;
    }

    session.candidates.sort(compareCandidates);
    if (session.candidates.length > MAX_CANDIDATES) {
      const lowestPriority = Math.min(
        ...session.candidates.map((item) => KIND_PRIORITY[item.kind]),
      );
      const evictionIndex = session.candidates.findIndex(
        (item) => KIND_PRIORITY[item.kind] === lowestPriority,
      );
      session.candidates.splice(evictionIndex, 1);
    }
    return true;
  }

  function stopSession(session) {
    if (!session) {
      return false;
    }
    clearSession(session);
    return true;
  }

  function expireSession(session, tabId = session && session.tabId) {
    if (!session || tabId !== session.tabId) {
      return false;
    }
    clearSession(session);
    return true;
  }

  function listCandidates(session) {
    if (isExpired(session)) {
      return [];
    }
    return session.candidates.map(({ sequence: _sequence, ...candidate }) =>
      cloneCandidate(candidate),
    );
  }

  return {
    createSession,
    upsertCandidate,
    stopSession,
    expireSession,
    listCandidates,
  };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = EdgeCaptureStore;
} else {
  globalThis.EdgeCaptureStore = EdgeCaptureStore;
}
