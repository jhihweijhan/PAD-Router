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
  const boardGrid = document.querySelector("#board-grid");
  const reviewStatus = document.querySelector("#review-status");
  const unknownCount = document.querySelector("#unknown-count");
  const selectedCellLabel = document.querySelector("#selected-cell");
  const executionGate = document.querySelector("#execution-gate");
  const orbPalette = document.querySelector("#orb-palette");
  const enhanced = document.querySelector("#enhanced");
  const locked = document.querySelector("#locked");
  const correct = document.querySelector("#correct");
  const protect = document.querySelector("#protect");
  const clearProtect = document.querySelector("#clear-protect");

  let pendingSnapshot = null;
  let renderFrame = null;
  let pollTimer = null;
  let currentSnapshot = null;
  let selectedOrb = "fire";
  const hazardOrbs = new Set(["jammer", "poison", "mortal_poison", "bomb"]);

  function cellParts(cell) {
    return Array.isArray(cell) && cell.length === 2 ? cell : null;
  }

  function cellEquals(left, right) {
    const a = cellParts(left);
    const b = cellParts(right);
    return Boolean(a && b && a[0] === b[0] && a[1] === b[1]);
  }

  function cellText(cell) {
    const parts = cellParts(cell);
    return parts ? `第 ${parts[0] + 1} 列、第 ${parts[1] + 1} 行` : "尚未選取盤面格";
  }

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

  function renderBoard(snapshot) {
    const entries = Array.isArray(snapshot.board) ? snapshot.board : [];
    const selected = cellParts(snapshot.selected_cell);
    boardGrid.replaceChildren();
    if (entries.length === 0) {
      const empty = document.createElement("span");
      empty.className = "board-empty";
      empty.textContent = "擷取畫面後顯示 5 × 6 盤面";
      boardGrid.append(empty);
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const entry of entries) {
      const cell = cellParts(entry.cell);
      if (!cell) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "board-cell";
      if (entry.unknown) button.classList.add("unknown");
      if (entry.selected || cellEquals(cell, selected)) button.classList.add("selected");
      if (entry.protected) button.classList.add("protected");
      button.dataset.row = String(cell[0]);
      button.dataset.col = String(cell[1]);
      button.dataset.kind = entry.kind || "unknown";
      if (entry.color !== null && entry.color !== undefined) {
        button.dataset.color = String(entry.color);
      }
      button.setAttribute("aria-selected", String(Boolean(entry.selected || cellEquals(cell, selected))));
      button.setAttribute("aria-keyshortcuts", "ArrowUp ArrowDown ArrowLeft ArrowRight");
      const state = entry.unknown ? "未知" : entry.label || "未命名";
      const flags = [entry.enhanced ? "強化" : "", entry.locked ? "鎖定" : "", entry.protected ? "保護" : ""]
        .filter(Boolean).join("、");
      button.setAttribute("aria-label", `${cellText(cell)}：${state}${flags ? `（${flags}）` : ""}`);
      button.textContent = entry.unknown ? "?" : entry.label || "?";
      if (entry.enhanced) {
        const badge = document.createElement("span");
        badge.className = "cell-badge plus";
        badge.setAttribute("aria-hidden", "true");
        badge.textContent = "+";
        button.append(badge);
      }
      if (entry.locked) {
        const badge = document.createElement("span");
        badge.className = "cell-badge locked";
        badge.setAttribute("aria-hidden", "true");
        badge.textContent = "L";
        button.append(badge);
      }
      button.addEventListener("click", () => command("select_cell", { cell }));
      button.addEventListener("keydown", (event) => {
        const movement = {
          ArrowUp: [-1, 0], ArrowDown: [1, 0],
          ArrowLeft: [0, -1], ArrowRight: [0, 1],
        }[event.key];
        if (!movement) return;
        const row = cell[0] + movement[0];
        const col = cell[1] + movement[1];
        if (row < 0 || row >= 5 || col < 0 || col >= 6) return;
        event.preventDefault();
        const target = boardGrid.querySelector(`[data-row="${row}"][data-col="${col}"]`);
        if (target) target.focus();
        command("select_cell", { cell: [row, col] });
      });
      fragment.append(button);
    }
    boardGrid.append(fragment);
  }

  function syncOrbFlags(hasSelection, busy) {
    const hazard = hazardOrbs.has(selectedOrb);
    if (hazard) {
      enhanced.checked = false;
      locked.checked = false;
    }
    enhanced.disabled = busy || !hasSelection || hazard;
    locked.disabled = busy || !hasSelection || hazard;
  }

  function renderSnapshot(snapshot) {
    currentSnapshot = snapshot;
    const busy = Boolean(snapshot.busy);
    const devices = Array.isArray(snapshot.devices) ? snapshot.devices : [];
    const selected = snapshot.selected_device || "";
    const source = snapshot.source;
    const entries = Array.isArray(snapshot.board) ? snapshot.board : [];
    const selectedCell = cellParts(snapshot.selected_cell);
    const selectedEntry = entries.find((entry) => cellEquals(entry.cell, selectedCell));
    const unknown = Number.isInteger(snapshot.unknown_count)
      ? snapshot.unknown_count : entries.filter((entry) => entry.unknown).length;
    const hasBoard = entries.length > 0;
    const hasSelection = Boolean(selectedCell);

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
      if (sourceImage.src !== source.image) sourceImage.src = source.image;
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

    renderBoard(snapshot);
    unknownCount.textContent = `未知 ${unknown}`;
    selectedCellLabel.textContent = hasSelection
      ? `${cellText(selectedCell)}${selectedEntry && selectedEntry.protected ? "（已保護）" : ""}`
      : "尚未選取盤面格";
    reviewStatus.textContent = !hasBoard
      ? "尚未載入盤面。"
      : unknown > 0
        ? `模型無法判斷 ${unknown} 格；選取後修正，修正未知格會前往下一格。`
        : "盤面已辨識；可選取任一格檢查或修正。";
    executionGate.textContent = !hasBoard
      ? "尚未載入盤面。"
      : unknown > 0
        ? `含 ${unknown} 個未知格；不可確認或執行。`
        : "未知格為 0；目前 review 不提供執行按鈕。";
    correct.disabled = busy || !hasSelection;
    protect.disabled = busy || !hasSelection;
    clearProtect.disabled = busy || !snapshot.protected_cell;
    for (const button of orbPalette.querySelectorAll("button[data-orb]")) {
      button.disabled = busy || !hasSelection;
      button.setAttribute("aria-pressed", String(button.dataset.orb === selectedOrb));
    }
    enhanced.checked = Boolean(selectedEntry && selectedEntry.enhanced);
    locked.checked = Boolean(selectedEntry && selectedEntry.locked);
    syncOrbFlags(hasSelection, busy);

    const entriesForConsole = Array.isArray(snapshot.console) ? snapshot.console : [];
    consoleCount.textContent = String(entriesForConsole.length);
    const fragment = document.createDocumentFragment();
    for (const entry of entriesForConsole) {
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

  function correctionValue() {
    const suffix = `${locked.checked ? "*" : ""}${enhanced.checked ? "+" : ""}`;
    return `${selectedOrb}${suffix}`;
  }

  function ready() {
    if (!window.pywebview || !window.pywebview.api) return;
    window.pywebview.api.command({ action: "snapshot" }).then(applyReply);
    if (pollTimer === null) pollTimer = window.setInterval(pollEvents, 200);
  }

  orbPalette.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-orb]");
    if (!button) return;
    selectedOrb = button.dataset.orb;
    for (const option of orbPalette.querySelectorAll("button[data-orb]")) {
      option.setAttribute("aria-pressed", String(option === button));
    }
    syncOrbFlags(Boolean(currentSnapshot && currentSnapshot.selected_cell), Boolean(currentSnapshot && currentSnapshot.busy));
  });
  correct.addEventListener("click", () => {
    const cell = currentSnapshot && currentSnapshot.selected_cell;
    if (cellParts(cell)) command("correct_cell", { cell, value: correctionValue() });
  });
  protect.addEventListener("click", () => {
    const cell = currentSnapshot && currentSnapshot.selected_cell;
    if (cellParts(cell)) command("set_protected_cell", { cell });
  });
  clearProtect.addEventListener("click", () => command("set_protected_cell", { cell: null }));
  refresh.addEventListener("click", () => command("refresh_devices"));
  device.addEventListener("change", () => command("select_device", { serial: device.value }));
  capture.addEventListener("click", () => command("capture_screen"));
  window.addEventListener("pywebviewready", ready);
  if (window.pywebview && window.pywebview.api) ready();
})();
