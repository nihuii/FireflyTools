"use strict";

const EdgeCaptureStore = (() => {
  const MAX_CANDIDATES = 50;
  const MAX_URL_CHARS = 16 * 1024;
  const SESSION_TTL_MS = 300000;
  const KIND_PRIORITY = Object.freeze({
    hls: 2,
    dash: 2,
    direct_mp4: 1,
  });

  function exceedsUrlCharacterLimit(url) {
    let count = 0;
    for (const _character of url) {
      count += 1;
      if (count > MAX_URL_CHARS) {
        return true;
      }
    }
    return false;
  }

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

  function isJsonLikeData(value, ancestors) {
    if (value === null) {
      return true;
    }
    if (typeof value === "string" || typeof value === "boolean") {
      return true;
    }
    if (typeof value === "number") {
      return Number.isFinite(value);
    }
    if (typeof value !== "object" || ancestors.has(value)) {
      return false;
    }

    const prototype = Object.getPrototypeOf(value);
    if (
      !Array.isArray(value) &&
      prototype !== Object.prototype &&
      prototype !== null
    ) {
      return false;
    }

    ancestors.add(value);
    for (const key of Reflect.ownKeys(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor.enumerable) {
        continue;
      }
      if (
        typeof key !== "string" ||
        !Object.hasOwn(descriptor, "value") ||
        !isJsonLikeData(descriptor.value, ancestors)
      ) {
        return false;
      }
    }
    ancestors.delete(value);
    return true;
  }

  function cloneCandidate(candidate) {
    try {
      if (
        typeof globalThis.structuredClone !== "function" ||
        !isJsonLikeData(candidate, new WeakSet())
      ) {
        return null;
      }
      return globalThis.structuredClone(candidate);
    } catch (_error) {
      return null;
    }
  }

  function compareCandidates(left, right) {
    const priorityDifference =
      (KIND_PRIORITY[right.candidate.kind] || 0) -
      (KIND_PRIORITY[left.candidate.kind] || 0);
    return priorityDifference || left.sequence - right.sequence;
  }

  function upsertCandidate(session, { tabId, candidate } = {}) {
    if (isExpired(session) || tabId !== session.tabId) {
      return false;
    }
    const clonedCandidate = cloneCandidate(candidate);
    if (
      !clonedCandidate ||
      typeof clonedCandidate !== "object" ||
      Array.isArray(clonedCandidate) ||
      typeof clonedCandidate.url !== "string" ||
      exceedsUrlCharacterLimit(clonedCandidate.url) ||
      !Object.hasOwn(KIND_PRIORITY, clonedCandidate.kind)
    ) {
      return false;
    }

    const existingIndex = session.candidates.findIndex(
      (item) =>
        item.candidate.kind === clonedCandidate.kind &&
        item.candidate.url === clonedCandidate.url,
    );
    if (existingIndex >= 0) {
      session.candidates[existingIndex] = {
        candidate: clonedCandidate,
        sequence: session.candidates[existingIndex].sequence,
      };
    } else {
      session.candidates.push({
        candidate: clonedCandidate,
        sequence: session.nextSequence,
      });
      session.nextSequence += 1;
    }

    session.candidates.sort(compareCandidates);
    if (session.candidates.length > MAX_CANDIDATES) {
      const lowestPriority = Math.min(
        ...session.candidates.map(
          (item) => KIND_PRIORITY[item.candidate.kind],
        ),
      );
      const evictionIndex = session.candidates.findIndex(
        (item) => KIND_PRIORITY[item.candidate.kind] === lowestPriority,
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
    return session.candidates.map((item) =>
      cloneCandidate(item.candidate),
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
