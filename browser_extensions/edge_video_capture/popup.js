"use strict";

(() => {
  const startButton = document.querySelector("#start-button");
  const startReloadButton = document.querySelector("#start-reload-button");
  const stopButton = document.querySelector("#stop-button");
  const sendButton = document.querySelector("#send-button");
  const copyButton = document.querySelector("#copy-button");
  const status = document.querySelector("#status");
  const candidateList = document.querySelector("#candidate-list");
  let activeTab = null;
  let selectedCandidateId = "";
  let startPending = false;

  function showStatus(code, fallbackCode) {
    status.textContent = EdgeCapturePopupModel.userMessage(code, fallbackCode);
  }

  async function currentTab() {
    const tabs = await chrome.tabs.query({active: true, currentWindow: true});
    return tabs[0] || null;
  }

  function renderCandidates(candidates) {
    candidateList.replaceChildren();
    if (!Array.isArray(candidates) || candidates.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "尚未发现候选项。";
      candidateList.append(empty);
      selectedCandidateId = "";
      return;
    }

    const availableIds = new Set(candidates.map((candidate) => candidate.id));
    if (!availableIds.has(selectedCandidateId)) {
      selectedCandidateId = "";
    }
    for (const candidate of candidates) {
      const row = EdgeCapturePopupModel.candidateRow(candidate);
      const label = document.createElement("label");
      const radio = document.createElement("input");
      const summary = document.createElement("span");
      const url = document.createElement("span");

      radio.type = "radio";
      radio.name = "candidate";
      radio.value = row.id;
      radio.checked = row.id === selectedCandidateId;
      radio.addEventListener("change", () => {
        selectedCandidateId = row.id;
      });
      summary.className = "candidate-summary";
      summary.textContent = `${row.type} · ${row.hostname} · ${row.discoveredAt}`;
      url.className = "candidate-url";
      url.textContent = row.redactedUrl;
      label.append(radio, summary, url);
      candidateList.append(label);
    }
  }

  async function refreshCandidates() {
    activeTab = await currentTab();
    if (!activeTab || !Number.isInteger(activeTab.id)) {
      renderCandidates([]);
      return;
    }
    const response = await chrome.runtime.sendMessage({
      type: "capture:list",
      tabId: activeTab.id,
    });
    renderCandidates(response && response.ok ? response.candidates : []);
  }

  async function selectedProtocolMessage() {
    if (!activeTab || !Number.isInteger(activeTab.id) || !selectedCandidateId) {
      showStatus("select_candidate");
      return null;
    }
    const response = await chrome.runtime.sendMessage({
      type: "capture:protocol",
      tabId: activeTab.id,
      candidateId: selectedCandidateId,
      requestId: crypto.randomUUID(),
    });
    if (!response || !response.ok || !response.message) {
      throw new Error("candidate unavailable");
    }
    return response.message;
  }

  function setStartDisabled(disabled) {
    startButton.disabled = disabled;
    startReloadButton.disabled = disabled;
  }

  async function beginCapture({reload = false} = {}) {
    if (startPending) {
      return;
    }
    startPending = true;
    setStartDisabled(true);
    try {
      const captureTab = await currentTab();
      activeTab = captureTab;
      if (!captureTab || !Number.isInteger(captureTab.id)) {
        showStatus("capture_start_failed");
        return;
      }
      const response = await chrome.runtime.sendMessage({
        type: "capture:start",
        tabId: captureTab.id,
        pageUrl: typeof captureTab.url === "string" ? captureTab.url : "",
        pageTitle: typeof captureTab.title === "string" ? captureTab.title : "",
      });
      if (!response || !response.ok) {
        showStatus("capture_start_failed");
        return;
      }
      selectedCandidateId = "";
      renderCandidates([]);
      if (!reload) {
        showStatus("capturing");
        return;
      }
      try {
        await chrome.tabs.reload(captureTab.id, {bypassCache: true});
        showStatus("capturing_reloaded");
      } catch (_error) {
        showStatus("reload_failed");
      }
    } catch (_error) {
      showStatus("capture_start_failed");
    } finally {
      startPending = false;
      setStartDisabled(false);
    }
  }

  startButton.addEventListener("click", () => beginCapture());
  startReloadButton.addEventListener("click", () =>
    beginCapture({reload: true}),
  );

  stopButton.addEventListener("click", async () => {
    let response = null;
    try {
      response = await chrome.runtime.sendMessage({type: "capture:stop"});
    } catch (_error) {
      response = null;
    }
    if (!response || !response.ok) {
      showStatus("stop_failed");
      return;
    }
    selectedCandidateId = "";
    renderCandidates([]);
    showStatus("stopped");
  });

  sendButton.addEventListener("click", async () => {
    try {
      const message = await selectedProtocolMessage();
      if (!message) {
        return;
      }
      const response = await chrome.runtime.sendMessage({
        type: "send_candidate",
        message,
      });
      if (response && response.ok) {
        showStatus("sent");
      } else {
        showStatus(response && response.code, "send_failed");
      }
    } catch (_error) {
      showStatus("send_failed");
    }
  });

  copyButton.addEventListener("click", async () => {
    try {
      const message = await selectedProtocolMessage();
      if (!message) {
        return;
      }
      await navigator.clipboard.writeText(JSON.stringify(message));
      showStatus("copied");
    } catch (_error) {
      showStatus("copy_failed");
    }
  });

  void refreshCandidates();
  setInterval(() => {
    void refreshCandidates();
  }, 1000);
})();
