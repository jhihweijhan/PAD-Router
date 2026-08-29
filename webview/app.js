(() => {
  "use strict";

  const status = document.querySelector("#status");
  const statusDot = document.querySelector(".status-dot");
  const device = document.querySelector("#device");
  const refresh = document.querySelector("#refresh");
  const capture = document.querySelector("#capture");
  const sourceImage = document.querySelector("#source-image");
  const sourceEmpty = document.querySelector("#source-empty");
  const sourceMeta = document.querySelector("#source-meta");
  const sourceName = document.querySelector("#source-name");
  const sourceSize = document.querySelector("#source-size");
  const selectedDevice = document.querySelector("#selected-device");
  const busyLabel = document.querySelector("#busy-label");
  const consoleList = document.querySelector("#console-list");
  const consoleCount = document.querySelector("#console-count");

  let pendingSnapshot = null;
  let renderFrame = null;
  let pollTimer = null;

  function queueRender(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;
    pendingSnapshot = snapshot;
    if (renderFrame !== null) return;
    renderFrame = window.requestAnimationFrame(() => {
      renderFrame = null;
      const next = pendingSnapshot;
      pendingSnapshot = null;
      renderSnapshot(next);
    });
  }

  function renderSnapshot(snapshot) {
    const busy = Boolean(snapshot.busy);
    const devices = Array.isArray(snapshot.devices) ? snapshot.devices : [];
    const selected = snapshot.selected_device || "";
    const source = snapshot.source;

    status.textContent = snapshot.status || "尚未載入來源";
    statusDot.style.background = busy ? "var(--warning)" : "var(--accent)";
    busyLabel.textContent = busy ? "處理中" : "閒置";
    refresh.disabled = busy;
    device.disabled = busy || devices.length === 0;
    capture.disabled = busy || !selected;

    const current = device.value;
    device.replaceChildren();
    if (devices.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "沒有可用裝置";
      device.append(option);
    } else {
      for (const serial of devices) {
        const option = document.createElement("option");
        option.value = serial;
        option.textContent = serial;
        option.selected = serial === selected || (!selected && serial === current);
        device.append(option);
      }
      if (selected) device.value = selected;
    }

    selectedDevice.textContent = selected || "尚未選取";
    if (source) {
      sourceImage.hidden = false;
      sourceImage.src = source.image;
      sourceImage.alt = `裝置 ${source.name || "source"} 的目前截圖`;
      sourceEmpty.hidden = true;
      sourceName.textContent = source.name || "未命名來源";
      sourceSize.textContent = `${source.width} × ${source.height}`;
      sourceMeta.textContent = `${source.width} × ${source.height}`;
    } else {
      sourceImage.hidden = true;
      sourceImage.removeAttribute("src");
      sourceEmpty.hidden = false;
      sourceName.textContent = "尚未載入";
      sourceSize.textContent = "—";
      sourceMeta.textContent = "尚未擷取";
    }

    const entries = Array.isArray(snapshot.console) ? snapshot.console : [];
    consoleCount.textContent = String(entries.length);
    const fragment = document.createDocumentFragment();
    for (const entry of entries) {
      const item = document.createElement("li");
      const phase = entry.phase ? `[${entry.phase}] ` : "";
      item.className = entry.level || "info";
      item.textContent = `${phase}${entry.message || ""}`;
      fragment.append(item);
    }
    consoleList.replaceChildren(fragment);
    consoleList.parentElement.scrollTop = consoleList.parentElement.scrollHeight;
  }

  function applyReply(reply) {
    if (Array.isArray(reply)) {
      for (const event of reply) {
        if (event && event.snapshot) queueRender(event.snapshot);
      }
      return;
    }
    if (reply && reply.snapshot) {
      queueRender(reply.snapshot);
    } else {
      queueRender(reply);
    }
  }

  async function command(action, extra = {}) {
    try {
      const reply = await window.pywebview.api.command({ action, ...extra });
      applyReply(reply);
    } catch (error) {
      status.textContent = error.message || String(error);
      statusDot.style.background = "var(--error)";
    }
  }

  async function pollEvents() {
    try {
      const events = await window.pywebview.api.drain_events();
      applyReply(events);
    } catch (_error) {
      // The window can close while a poll is in flight.
    }
  }

  function ready() {
    if (!window.pywebview || !window.pywebview.api) return;
    window.pywebview.api.command({ action: "snapshot" }).then(applyReply);
    if (pollTimer === null) pollTimer = window.setInterval(pollEvents, 50);
  }

  refresh.addEventListener("click", () => command("refresh_devices"));
  device.addEventListener("change", () => command("select_device", { serial: device.value }));
  capture.addEventListener("click", () => command("capture_screen"));
  window.addEventListener("pywebviewready", ready);
  if (window.pywebview && window.pywebview.api) ready();
})();
