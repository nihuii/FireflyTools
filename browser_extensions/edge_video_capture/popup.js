"use strict";

(() => {
  const startButton = document.querySelector("#start-button");
  const stopButton = document.querySelector("#stop-button");
  const copyButton = document.querySelector("#copy-button");
  const status = document.querySelector("#status");
  const candidateList = document.querySelector("#candidate-list");
  let activeTab = null;
  let selectedCandidateId = "";

  function showStatus(code) {
    status.textContent = EdgeCapturePopupModel.userMessage(code);
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

  startButton.addEventListener("click", async () => {
    let granted = false;
    try {
      granted = await chrome.permissions.request({
        origins: ["http://*/*", "https://*/*"],
      });
    } catch (_error) {
      granted = false;
    }
    if (!granted) {
      showStatus("permission_denied");
      return;
    }

    activeTab = await currentTab();
    if (!activeTab || !Number.isInteger(activeTab.id)) {
      return;
    }
    await chrome.runtime.sendMessage({
      type: "capture:start",
      tabId: activeTab.id,
      pageUrl: typeof activeTab.url === "string" ? activeTab.url : "",
      pageTitle: typeof activeTab.title === "string" ? activeTab.title : "",
    });
    selectedCandidateId = "";
    showStatus("capturing");
    await refreshCandidates();
  });

  stopButton.addEventListener("click", async () => {
    activeTab = await currentTab();
    if (activeTab && Number.isInteger(activeTab.id)) {
      await chrome.runtime.sendMessage({
        type: "capture:stop",
        tabId: activeTab.id,
      });
    }
    selectedCandidateId = "";
    renderCandidates([]);
    showStatus("stopped");
  });

  copyButton.addEventListener("click", async () => {
    if (!activeTab || !Number.isInteger(activeTab.id) || !selectedCandidateId) {
      showStatus("select_candidate");
      return;
    }
    try {
      const response = await chrome.runtime.sendMessage({
        type: "capture:protocol",
        tabId: activeTab.id,
        candidateId: selectedCandidateId,
        requestId: crypto.randomUUID(),
      });
      if (!response || !response.ok || !response.message) {
        throw new Error("candidate unavailable");
      }
      await navigator.clipboard.writeText(JSON.stringify(response.message));
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
