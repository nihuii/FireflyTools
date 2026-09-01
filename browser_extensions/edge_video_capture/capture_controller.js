"use strict";

const EdgeCaptureController = (() => {
  function createCaptureController({detector, store, now = Date.now} = {}) {
    if (!detector || typeof detector.buildCandidate !== "function") {
      throw new TypeError("detector must provide buildCandidate");
    }
    if (!store || typeof store.createSession !== "function") {
      throw new TypeError("store must provide capture session operations");
    }
    if (typeof now !== "function") {
      throw new TypeError("now must be a function");
    }

    const sessions = new Map();
    const pendingRequests = new Map();
    let activeTabId = null;

    function safeHeaderObject(headers) {
      const entries = [];
      if (Array.isArray(headers)) {
        entries.push(...headers);
      } else if (headers && typeof headers === "object") {
        for (const [name, value] of Object.entries(headers)) {
          entries.push({name, value});
        }
      }
      return detector.sanitizeRequestHeaders(entries);
    }

    function isActiveTab(tabId) {
      return Number.isInteger(tabId) && tabId === activeTabId && sessions.has(tabId);
    }

    function clearPendingForTab(tabId) {
      for (const [requestId, pending] of pendingRequests) {
        if (pending.tabId === tabId) {
          pendingRequests.delete(requestId);
        }
      }
    }

    function stop(tabId = activeTabId) {
      if (!Number.isInteger(tabId)) {
        return false;
      }
      const record = sessions.get(tabId);
      if (record) {
        store.stopSession(record.session);
        sessions.delete(tabId);
      }
      clearPendingForTab(tabId);
      if (activeTabId === tabId) {
        activeTabId = null;
      }
      return Boolean(record);
    }

    function start({tabId, pageUrl = "", pageTitle = ""} = {}) {
      if (!Number.isInteger(tabId)) {
        throw new TypeError("tabId must be an integer");
      }
      if (activeTabId !== null) {
        stop(activeTabId);
      }
      const session = store.createSession({tabId, clock: now});
      sessions.set(tabId, {
        tabId,
        pageUrl: typeof pageUrl === "string" ? pageUrl : "",
        pageTitle: typeof pageTitle === "string" ? pageTitle : "",
        startedAt: session.startedAt,
        nextCandidateSequence: 1,
        session,
      });
      activeTabId = tabId;
      return true;
    }

    function pendingFrom(details, existing = {}) {
      const requestId =
        typeof details.requestId === "string" ? details.requestId : "";
      return {
        tabId: details.tabId,
        requestId,
        url:
          typeof details.url === "string"
            ? details.url
            : typeof existing.url === "string"
              ? existing.url
              : "",
        method:
          typeof details.method === "string"
            ? details.method.toUpperCase()
            : typeof existing.method === "string"
              ? existing.method
              : "GET",
        requestHeaders: safeHeaderObject(
          Object.hasOwn(details, "requestHeaders")
            ? details.requestHeaders
            : existing.requestHeaders,
        ),
      };
    }

    function onBeforeRequest(details = {}) {
      if (!isActiveTab(details.tabId) || typeof details.requestId !== "string") {
        return false;
      }
      pendingRequests.set(details.requestId, pendingFrom(details));
      return true;
    }

    function onBeforeSendHeaders(details = {}) {
      if (!isActiveTab(details.tabId) || typeof details.requestId !== "string") {
        return false;
      }
      const existing = pendingRequests.get(details.requestId);
      if (existing && existing.tabId !== details.tabId) {
        return false;
      }
      pendingRequests.set(
        details.requestId,
        pendingFrom(details, existing || {}),
      );
      return true;
    }

    function responseContentType(responseHeaders) {
      if (!Array.isArray(responseHeaders)) {
        return "";
      }
      const contentType = responseHeaders.find(
        (header) =>
          header &&
          typeof header.name === "string" &&
          header.name.toLowerCase() === "content-type" &&
          typeof header.value === "string" &&
          !/[\r\n]/.test(header.value),
      );
      return contentType ? contentType.value : "";
    }

    function onHeadersReceived(details = {}) {
      if (!isActiveTab(details.tabId) || typeof details.requestId !== "string") {
        return false;
      }
      const existing = pendingRequests.get(details.requestId);
      if (existing && existing.tabId !== details.tabId) {
        return false;
      }
      const pending = pendingFrom(details, existing || {});
      pendingRequests.delete(details.requestId);
      const candidate = detector.buildCandidate({
        url: pending.url,
        contentType: responseContentType(details.responseHeaders),
        method: pending.method,
        requestHeaders: Object.entries(pending.requestHeaders).map(
          ([name, value]) => ({name, value}),
        ),
      });
      if (!candidate) {
        return false;
      }
      const record = sessions.get(details.tabId);
      const storedCandidates = store.listCandidates(record.session);
      const existingCandidate = storedCandidates.find(
        (item) => item.kind === candidate.kind && item.url === candidate.url,
      );
      let candidateId = existingCandidate && existingCandidate.id;
      if (!candidateId) {
        const usedIds = new Set(storedCandidates.map((item) => item.id));
        do {
          candidateId = `c${record.nextCandidateSequence}`;
          record.nextCandidateSequence += 1;
        } while (usedIds.has(candidateId));
      }
      const capturedAt = now();
      return store.upsertCandidate(record.session, {
        tabId: details.tabId,
        candidate: {
          id: candidateId,
          ...candidate,
          capturedAt,
        },
      });
    }

    function list(tabId = activeTabId) {
      const record = sessions.get(tabId);
      if (!record || tabId !== activeTabId) {
        return [];
      }
      return store.listCandidates(record.session);
    }

    function formatUtc(milliseconds) {
      const iso = new Date(milliseconds).toISOString();
      return iso.endsWith(".000Z") ? `${iso.slice(0, -5)}Z` : iso;
    }

    function toProtocolMessage(tabId, candidateId, requestId) {
      const record = sessions.get(tabId);
      if (!record || tabId !== activeTabId) {
        return null;
      }
      const candidate = list(tabId).find((item) => item.id === candidateId);
      if (!candidate) {
        return null;
      }
      return {
        protocol_version: 1,
        type: "media_candidate",
        request_id: requestId,
        captured_at: formatUtc(candidate.capturedAt),
        page: {
          url: record.pageUrl,
          title: record.pageTitle,
        },
        candidate: {
          url: candidate.url,
          kind: candidate.kind,
          content_type: candidate.contentType,
          method: candidate.method,
          headers: safeHeaderObject(candidate.headers),
        },
        sensitive_headers_included: false,
      };
    }

    function snapshot() {
      const serializedSessions = {};
      for (const [tabId, record] of sessions) {
        if (tabId !== activeTabId) {
          continue;
        }
        serializedSessions[tabId] = {
          tabId,
          pageUrl: record.pageUrl,
          pageTitle: record.pageTitle,
          startedAt: record.startedAt,
          nextCandidateSequence: record.nextCandidateSequence,
          candidates: list(tabId).map((candidate) => ({
            ...candidate,
            headers: safeHeaderObject(candidate.headers),
          })),
        };
      }

      const serializedPending = {};
      for (const [requestId, pending] of pendingRequests) {
        if (pending.tabId !== activeTabId) {
          continue;
        }
        serializedPending[requestId] = {
          tabId: pending.tabId,
          requestId,
          url: pending.url,
          method: pending.method,
          requestHeaders: safeHeaderObject(pending.requestHeaders),
        };
      }
      return {
        sessions: serializedSessions,
        pendingRequests: serializedPending,
      };
    }

    function restore(state = {}) {
      if (activeTabId !== null) {
        stop(activeTabId);
      }
      pendingRequests.clear();
      if (!state || typeof state !== "object") {
        return false;
      }

      const restoredSessions =
        state.sessions && typeof state.sessions === "object"
          ? Object.values(state.sessions)
          : [];
      const record = restoredSessions.find(
        (item) => item && Number.isInteger(item.tabId),
      );
      if (!record) {
        return true;
      }

      start({
        tabId: record.tabId,
        pageUrl: record.pageUrl,
        pageTitle: record.pageTitle,
      });
      const current = sessions.get(record.tabId);
      if (Number.isFinite(record.startedAt)) {
        current.startedAt = record.startedAt;
        current.session.startedAt = record.startedAt;
      }
      if (
        Number.isSafeInteger(record.nextCandidateSequence) &&
        record.nextCandidateSequence > 0
      ) {
        current.nextCandidateSequence = record.nextCandidateSequence;
      }
      if (Array.isArray(record.candidates)) {
        for (const persisted of record.candidates) {
          if (!persisted || typeof persisted !== "object") {
            continue;
          }
          const candidate = detector.buildCandidate({
            url: persisted.url,
            contentType: persisted.contentType,
            method: persisted.method,
            requestHeaders: Object.entries(
              safeHeaderObject(persisted.headers),
            ).map(([name, value]) => ({name, value})),
          });
          if (!candidate) {
            continue;
          }
          const persistedId =
            typeof persisted.id === "string" && /^c[1-9]\d*$/.test(persisted.id)
              ? persisted.id
              : `c${current.nextCandidateSequence}`;
          const numericId = Number(persistedId.slice(1));
          if (Number.isSafeInteger(numericId)) {
            current.nextCandidateSequence = Math.max(
              current.nextCandidateSequence,
              numericId + 1,
            );
          }
          store.upsertCandidate(current.session, {
            tabId: record.tabId,
            candidate: {
              id: persistedId,
              ...candidate,
              capturedAt: Number.isFinite(persisted.capturedAt)
                ? persisted.capturedAt
                : now(),
            },
          });
        }
      }

      if (state.pendingRequests && typeof state.pendingRequests === "object") {
        for (const [key, persisted] of Object.entries(state.pendingRequests)) {
          if (
            !persisted ||
            persisted.tabId !== record.tabId ||
            typeof persisted.requestId !== "string"
          ) {
            continue;
          }
          const pending = pendingFrom(
            {
              tabId: record.tabId,
              requestId: persisted.requestId,
              url: persisted.url,
              method: persisted.method,
              requestHeaders: persisted.requestHeaders,
            },
          );
          pendingRequests.set(key, pending);
        }
      }
      return true;
    }

    function expire(tabId = activeTabId) {
      if (!Number.isInteger(tabId)) {
        return false;
      }
      const record = sessions.get(tabId);
      if (record) {
        store.expireSession(record.session, tabId);
      }
      return stop(tabId);
    }

    function cleanup() {
      if (activeTabId === null) {
        return false;
      }
      const record = sessions.get(activeTabId);
      store.listCandidates(record.session);
      if (record.session.active === false) {
        const expiredTabId = activeTabId;
        sessions.delete(expiredTabId);
        clearPendingForTab(expiredTabId);
        activeTabId = null;
        return true;
      }
      return false;
    }

    return {
      start,
      stop,
      list,
      onBeforeRequest,
      onBeforeSendHeaders,
      onHeadersReceived,
      toProtocolMessage,
      snapshot,
      restore,
      expire,
      cleanup,
    };
  }

  return {createCaptureController};
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = EdgeCaptureController;
} else {
  globalThis.EdgeCaptureController = EdgeCaptureController;
}
