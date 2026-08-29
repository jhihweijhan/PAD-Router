(() => {
  "use strict";

  const status = document.querySelector("#status");
  const statusDot = document.querySelector(".status-dot");
  const device = document.querySelector("#device");
  const refresh = document.querySelector("#refresh");
  const capture = document.querySelector("#capture");
  const sourceImage = document.querySelector("#source-image");
  const sourceStage = document.querySelector("#source-stage");
  const sourceViewport = document.querySelector(".source-viewport");
  const routeOverlay = document.querySelector("#route-overlay");
  const routePreview = document.querySelector("#route-preview");
  const routePreviewGrid = document.querySelector("#route-preview-grid");
  const projectedCombo = document.querySelector("#projected-combo");
  const routePreviewStatus = document.querySelector("#route-preview-status");
  const sourceEmpty = document.querySelector("#source-empty");
  const sourceMeta = document.querySelector("#source-meta");
  const sourceName = document.querySelector("#source-name");
  const sourceSize = document.querySelector("#source-size");
  const selectedDevice = document.querySelector("#selected-device");
  const deviceStatus = document.querySelector("#device-status");
  const calibrationLeft = document.querySelector("#calibration-left");
  const calibrationTop = document.querySelector("#calibration-top");
  const calibrationCell = document.querySelector("#calibration-cell");
  const applyCalibration = document.querySelector("#apply-calibration");
  const autoCalibration = document.querySelector("#auto-calibration");
  const calibrationStatus = document.querySelector("#calibration-status");
  const profileFile = document.querySelector("#profile-file");
  const importProfile = document.querySelector("#import-profile");
  const exportProfile = document.querySelector("#export-profile");
  const profileStatus = document.querySelector("#profile-status");
  const debugSource = document.querySelector("#debug-source");
  const debugGeneration = document.querySelector("#debug-generation");
  const debugPending = document.querySelector("#debug-pending");
  const debugSearchGeneration = document.querySelector("#debug-search-generation");
  const debugExecutionPhase = document.querySelector("#debug-execution-phase");
  const debugConfirmed = document.querySelector("#debug-confirmed");
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
  const planningControls = document.querySelector("#planning-controls");
  const conditionBoxes = planningControls.querySelectorAll("[data-condition]");
  const colorBoxes = planningControls.querySelectorAll("[data-color]");
  const conditionOperator = document.querySelector("#condition-operator");
  const hazardPolicy = document.querySelector("#hazard-policy");
  const externalCondition = document.querySelector("#external-condition");
  const searchAttempts = document.querySelector("#search-attempts");
  const searchSteps = document.querySelector("#search-steps");
  const searchSeed = document.querySelector("#search-seed");
  const searchCascade = document.querySelector("#search-cascade");
  const startSearch = document.querySelector("#start-search");
  const cancelSearch = document.querySelector("#cancel-search");
  const activeProfile = document.querySelector("#active-profile");
  const searchProgress = document.querySelector("#search-progress");
  const searchResult = document.querySelector("#search-result");
  const approveRoute = document.querySelector("#approve-route");
  const executeRoute = document.querySelector("#execute-route");
  const stopExecution = document.querySelector("#stop-execution");
  const executionStatus = document.querySelector("#execution-status");

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

  function renderRouteOverlay(snapshot, source) {
    routeOverlay.replaceChildren();
    const points = Array.isArray(snapshot.route_overlay) ? snapshot.route_overlay : [];
    if (!source || points.length === 0) {
      routeOverlay.setAttribute("hidden", "");
      return;
    }
    routeOverlay.setAttribute("viewBox", `0 0 ${source.width} ${source.height}`);
    const namespace = "http\u003a//www.w3.org/2000/svg";
    if (points.length > 1) {
      const line = document.createElementNS(namespace, "polyline");
      line.setAttribute("points", points.map((point) => `${point.x},${point.y}`).join(" "));
      line.setAttribute("class", "route-line");
      routeOverlay.append(line);
    }
    for (const point of points) {
      const marker = document.createElementNS(namespace, "circle");
      marker.setAttribute("cx", String(point.x));
      marker.setAttribute("cy", String(point.y));
      marker.setAttribute("r", String(Math.max(5, Math.min(source.width, source.height) / 35)));
      marker.setAttribute("class", "route-marker");
      marker.setAttribute("aria-label", `路徑第 ${point.step} 步`);
      routeOverlay.append(marker);
    }
    routeOverlay.removeAttribute("hidden");
  }

  function renderRoutePreview(snapshot) {
    const preview = snapshot.route_preview;
    const entries = preview && Array.isArray(preview.board) ? preview.board : [];
    routePreviewGrid.replaceChildren();
    if (!preview || entries.length === 0) {
      routePreview.hidden = true;
      projectedCombo.textContent = "— Combo";
      routePreviewStatus.textContent = "尚未有選定路徑。";
      return;
    }
    routePreview.hidden = false;
    projectedCombo.textContent = `預計 ${preview.projected_combo ?? "未知"} Combo`;
    const fragment = document.createDocumentFragment();
    for (const entry of entries) {
      const cell = cellParts(entry.cell);
      if (!cell) continue;
      const orb = document.createElement("span");
      orb.className = "board-cell preview-cell";
      if (entry.unknown) orb.classList.add("unknown");
      if (entry.protected) orb.classList.add("protected");
      orb.dataset.kind = entry.kind || "unknown";
      if (entry.color !== null && entry.color !== undefined) {
        orb.dataset.color = String(entry.color);
      }
      const state = entry.unknown ? "未知" : entry.label || "未命名";
      const flags = [entry.enhanced ? "強化" : "", entry.locked ? "鎖定" : "", entry.protected ? "保護" : ""]
        .filter(Boolean).join("、");
      orb.setAttribute("aria-label", `${cellText(cell)}：${state}${flags ? `（${flags}）` : ""}`);
      orb.textContent = entry.unknown ? "?" : entry.label || "?";
      fragment.append(orb);
    }
    routePreviewGrid.append(fragment);
    const label = preview.execution_eligible ? "可執行候選" : "診斷預覽（不可執行）";
    routePreviewStatus.textContent = `拖曳後、匹配前（未移除珠子或套用天降）；${label}。`;
  }

  function sizeSourceStage(source) {
    if (!source || !source.width || !source.height) return;
    const scale = Math.min(
      1,
      sourceViewport.clientWidth / source.width,
      sourceViewport.clientHeight / source.height,
    );
    sourceStage.style.width = `${Math.max(1, Math.floor(source.width * scale))}px`;
    sourceStage.style.height = `${Math.max(1, Math.floor(source.height * scale))}px`;
    sourceImage.style.width = "100%";
    sourceImage.style.height = "100%";
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

  function planningPayload() {
    return {
      conditions: Array.from(conditionBoxes).map((box, index) => ({
        label: box.value,
        color: colorBoxes[index].value,
      })),
      operator: conditionOperator.value,
      hazard_policy: hazardPolicy.value,
      external: externalCondition.value,
    };
  }

  function searchPayload() {
    return {
      attempts: Number(searchAttempts.value),
      max_steps: Number(searchSteps.value),
      seed: Number(searchSeed.value),
      cascade: searchCascade.value === "true",
    };
  }

  function latestEvent(snapshot, phase) {
    const entries = Array.isArray(snapshot.console) ? snapshot.console : [];
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      if (entries[index] && entries[index].phase === phase) return entries[index];
    }
    return null;
  }
  function renderSearch(snapshot, hasBoard, busy) {
    const profile = snapshot.rule_profile;
    activeProfile.textContent = profile
      ? `目前規則：${profile.name}`
      : "尚未套用規則";
    const search = snapshot.search || {};
    const searching = search.status === "running" || search.status === "cancelling";
    startSearch.disabled = busy || !hasBoard || !profile || searching;
    cancelSearch.disabled = search.status !== "running";
    const progress = search.progress;
    if (search.status === "running") {
      searchProgress.textContent = progress
        ? `搜尋階段：${progress.phase}（${progress.completed}/${progress.total}）`
        : "搜尋已開始，等待後端階段…";
    } else if (search.status === "cancelling") {
      searchProgress.textContent = "正在取消搜尋；等待目前後端階段安全結束。";
    } else if (search.status === "cancelled") {
      searchProgress.textContent = "搜尋已取消。";
    } else if (search.status === "stale") {
      searchProgress.textContent = "舊搜尋結果已失效，未套用。";
    } else if (search.status === "failed") {
      searchProgress.textContent = "搜尋失敗；請查看事件主控台。";
    } else if (search.status === "complete") {
      searchProgress.textContent = "搜尋完成。";
    } else {
      searchProgress.textContent = "搜尋尚未開始。";
    }

    const result = search.result;
    const selected = result && result.selected;
    const candidate = selected === "qualifying"
      ? result.qualifying_candidate
      : selected === "diagnostic" ? result.diagnostic_candidate : null;
    if (!candidate) {
      searchResult.hidden = true;
      searchResult.textContent = "";
      return;
    }
    searchResult.hidden = false;
    const executable = Boolean(candidate.execution_eligible);
    searchResult.className = `result-card ${executable ? "qualifying" : "diagnostic"}`;
    const label = executable
      ? "符合條件候選（可進入後續 Python 安全流程）"
      : "診斷預覽（不可核准或執行）";
    const route = Array.isArray(candidate.route)
      ? candidate.route.map((cell) => cellText(cell)).join(" → ")
      : "無路徑";
    searchResult.textContent = `${label}\n${candidate.diagnostic || candidate.diagnostic_status || ""}\n${route}`;
  }

  function renderSnapshot(snapshot) {
    currentSnapshot = snapshot;
    const busy = Boolean(snapshot.busy);
    const operationalBusy = Boolean(snapshot.operational_busy);
    const operationalMutationBusy = Boolean(snapshot.operational_mutation_busy);
    const primaryMutationBusy = busy || operationalMutationBusy;
    const devices = Array.isArray(snapshot.devices) ? snapshot.devices : [];
    const selected = snapshot.selected_device || "";
    const source = snapshot.source;
    const routeResult = snapshot.route_result;
    const execution = snapshot.execution || {};
    const executionBusy = Boolean(execution.busy);
    const entries = Array.isArray(snapshot.board) ? snapshot.board : [];
    const selectedCell = cellParts(snapshot.selected_cell);
    const selectedEntry = entries.find((entry) => cellEquals(entry.cell, selectedCell));
    const unknown = Number.isInteger(snapshot.unknown_count)
      ? snapshot.unknown_count : entries.filter((entry) => entry.unknown).length;
    const hasBoard = entries.length > 0;
    const hasSelection = Boolean(selectedCell);

    status.textContent = snapshot.status || "尚未載入來源";
    statusDot.style.background = busy || operationalBusy ? "var(--warning)" : "var(--accent)";
    busyLabel.textContent = executionBusy ? "執行中"
      : operationalBusy ? "裝置作業中" : busy ? "處理中" : "閒置";
    refresh.disabled = busy || operationalBusy;
    device.disabled = busy || operationalBusy || devices.length === 0;
    capture.disabled = busy || operationalBusy || !selected;

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
    const deviceEvent = latestEvent(snapshot, "devices");
    deviceStatus.textContent = deviceEvent
      ? deviceEvent.message
      : devices.length ? `目前有 ${devices.length} 個可用裝置。` : "尚未更新裝置。";
    const calibration = snapshot.calibration;
    if (calibration) {
      if (document.activeElement !== calibrationLeft) calibrationLeft.value = String(calibration.left);
      if (document.activeElement !== calibrationTop) calibrationTop.value = String(calibration.top);
      if (document.activeElement !== calibrationCell) calibrationCell.value = String(calibration.cell);
    }
    const calibrationEvent = latestEvent(snapshot, "calibration");
    calibrationStatus.textContent = calibrationEvent
      ? calibrationEvent.message
      : calibration ? `目前校正：${calibration.left}, ${calibration.top}, ${calibration.cell}` : "尚未校正。";
    applyCalibration.disabled = busy || operationalBusy || !source;
    autoCalibration.disabled = busy || operationalBusy || !source;
    const profileEvent = latestEvent(snapshot, "rules");
    profileStatus.textContent = profileEvent
      ? profileEvent.message
      : "沿用目前規則設定格式。";
    importProfile.disabled = primaryMutationBusy;
    exportProfile.disabled = busy || !snapshot.rule_profile;
    const debug = snapshot.debug || {};
    debugSource.textContent = debug.source_name || "—";
    debugGeneration.textContent = String(debug.generation === undefined ? "—" : debug.generation);
    debugPending.textContent = String(debug.pending_operations === undefined ? "—" : debug.pending_operations);
    debugSearchGeneration.textContent = String(
      debug.search_generation === null || debug.search_generation === undefined
        ? "—" : debug.search_generation,
    );
    debugExecutionPhase.textContent = debug.execution_phase || "idle";
    debugConfirmed.textContent = debug.confirmed ? "是" : "否";
    if (source) {
      sourceStage.hidden = false;
      sourceImage.hidden = false;
      if (sourceImage.src !== source.image) sourceImage.src = source.image;
      sourceImage.alt = `裝置 ${source.name || "source"} 的目前截圖`;
      sourceEmpty.hidden = true;
      sourceName.textContent = source.name || "未命名來源";
      sourceSize.textContent = `${source.width} × ${source.height}`;
      sourceMeta.textContent = `${source.width} × ${source.height}`;
      sizeSourceStage(source);
    } else {
      sourceStage.hidden = true;
      sourceImage.hidden = true;
      sourceImage.removeAttribute("src");
      routeOverlay.setAttribute("hidden", "");
      sourceEmpty.hidden = false;
      sourceName.textContent = "尚未載入";
      sourceSize.textContent = "—";
      sourceMeta.textContent = "尚未擷取";
    }
    renderRouteOverlay(snapshot, source);
    if (source) window.requestAnimationFrame(() => sizeSourceStage(source));
    renderRoutePreview(snapshot);

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
    const approved = Boolean(snapshot.route_approved);
    const executable = Boolean(routeResult && routeResult.execution_eligible && snapshot.confirmed);
    executionGate.textContent = !hasBoard
      ? "尚未載入盤面。"
      : unknown > 0
        ? `含 ${unknown} 個未知格；不可確認或執行。`
        : routeResult && !routeResult.execution_eligible
          ? "目前候選是診斷預覽；不可核准或執行。"
          : routeResult && !approved
            ? "目前候選符合條件；請先核准後執行。"
            : routeResult && approved
              ? "目前路徑已核准；可執行安全手勢流程。"
              : "尚無目前可執行候選。";
    correct.disabled = primaryMutationBusy || !hasSelection;
    protect.disabled = primaryMutationBusy || !hasSelection;
    clearProtect.disabled = primaryMutationBusy || !snapshot.protected_cell;
    for (const button of orbPalette.querySelectorAll("button[data-orb]")) {
      button.disabled = primaryMutationBusy || !hasSelection;
      button.setAttribute("aria-pressed", String(button.dataset.orb === selectedOrb));
    }
    enhanced.checked = Boolean(selectedEntry && selectedEntry.enhanced);
    locked.checked = Boolean(selectedEntry && selectedEntry.locked);
    syncOrbFlags(hasSelection, primaryMutationBusy);
    renderSearch(snapshot, hasBoard, busy);
    approveRoute.disabled = primaryMutationBusy || !executable || approved;
    executeRoute.disabled = primaryMutationBusy || !executable || !approved;
    stopExecution.disabled = !executionBusy;
    stopExecution.textContent = executionBusy && execution.stop_requested
      ? "等待安全放手…"
      : "停止（安全放手後生效）";
    if (executionBusy) {
      executionStatus.textContent = `執行階段：${execution.phase || "processing"}`;
    } else if (execution.status === "success") {
      executionStatus.textContent = "執行成功。";
    } else if (execution.status === "failed") {
      executionStatus.textContent = "執行失敗；請查看驗證結果與事件主控台。";
    } else if (execution.status === "stopped") {
      executionStatus.textContent = "已停止；目前手勢已安全放手。";
    } else {
      executionStatus.textContent = "尚未執行路徑。";
    }
    if (execution.verification) {
      const report = execution.verification;
      const mismatch = report.mismatches === null || report.mismatches === undefined
        ? "未知" : report.mismatches;
      executionStatus.textContent += `\n手勢後驗證：${report.success ? "成功" : "失敗"}（${mismatch} 格不符）`;
    }

    const entriesForConsole = Array.isArray(snapshot.console) ? snapshot.console : [];
    const latestConsole = entriesForConsole[entriesForConsole.length - 1];
    const consolePanel = consoleList.parentElement;
    const followTail = consolePanel.scrollTop + consolePanel.clientHeight
      >= consolePanel.scrollHeight - 8;
    statusDot.style.background = busy || operationalBusy
      ? "var(--warning)"
      : latestConsole && latestConsole.level === "error" ? "var(--error)" : "var(--accent)";
    consoleCount.textContent = String(entriesForConsole.length);
    const fragment = document.createDocumentFragment();
    const levelLabels = { info: "資訊", success: "成功", warning: "警告", error: "錯誤" };
    for (const entry of entriesForConsole) {
      const item = document.createElement("li");
      const level = entry.level || "info";
      const levelLabel = levelLabels[level] || level;
      const phase = entry.phase ? `[${entry.phase}] ` : "";
      item.className = level;
      item.dataset.level = level;
      item.textContent = `[${levelLabel}] ${phase}${entry.message || ""}`;
      fragment.append(item);
    }
    consoleList.replaceChildren(fragment);
    if (followTail) consolePanel.scrollTop = consolePanel.scrollHeight;
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
      return reply;
    } catch (error) {
      const message = error.message || String(error);
      status.textContent = message;
      statusDot.style.background = "var(--error)";
      if (action.includes("calibr")) calibrationStatus.textContent = message;
      if (action.includes("profile")) profileStatus.textContent = message;
      return null;
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

  function calibrationPayload() {
    return {
      left: Number(calibrationLeft.value),
      top: Number(calibrationTop.value),
      cell: Number(calibrationCell.value),
    };
  }

  async function downloadProfile() {
    const reply = await command("export_rule_profile");
    if (!reply || typeof reply.profile_json !== "string") return;
    const blob = new Blob([reply.profile_json], { type: "application/json" });
    const link = document.createElement("a");
    const profileName = reply.profile && reply.profile.name
      ? reply.profile.name : "rule-profile";
    link.download = `${profileName.replace(/[^\w.-]+/g, "_").slice(0, 80) || "rule-profile"}.json`;
    link.href = URL.createObjectURL(blob);
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 0);
    profileStatus.textContent = `已匯出：${profileName}`;
  }

  function importProfileFile(file) {
    const reader = new FileReader();
    reader.onload = () => command("import_rule_profile", {
      profile_json: String(reader.result || ""),
    });
    reader.onerror = () => {
      profileStatus.textContent = "無法讀取規則設定檔。";
      status.textContent = profileStatus.textContent;
      statusDot.style.background = "var(--error)";
    };
    reader.readAsText(file);
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
  const updateProfile = () => command("set_rule_profile", planningPayload());
  for (const control of [...conditionBoxes, ...colorBoxes, conditionOperator, hazardPolicy, externalCondition]) {
    control.addEventListener("change", updateProfile);
  }
  startSearch.addEventListener("click", () => command("search_route", searchPayload()));
  cancelSearch.addEventListener("click", () => command("cancel_search"));
  approveRoute.addEventListener("click", () => command("approve_route"));
  executeRoute.addEventListener("click", () => {
    const serial = currentSnapshot && currentSnapshot.selected_device;
    if (serial) command("execute_route", { serial });
  });
  stopExecution.addEventListener("click", () => command("stop_execution"));
  refresh.addEventListener("click", () => command("refresh_devices"));
  device.addEventListener("change", () => command("select_device", { serial: device.value }));
  capture.addEventListener("click", () => command("capture_screen", { search: searchPayload() }));
  applyCalibration.addEventListener("click", () => command("calibrate", calibrationPayload()));
  autoCalibration.addEventListener("click", () => command("auto_calibrate"));
  importProfile.addEventListener("click", () => profileFile.click());
  profileFile.addEventListener("change", () => {
    const [file] = profileFile.files || [];
    if (!file) return;
    profileStatus.textContent = `正在讀取：${file.name}`;
    importProfileFile(file);
    profileFile.value = "";
  });
  exportProfile.addEventListener("click", () => downloadProfile());
  window.addEventListener("resize", () => {
    if (currentSnapshot && currentSnapshot.source) sizeSourceStage(currentSnapshot.source);
  });
  window.addEventListener("pywebviewready", ready);
  if (window.pywebview && window.pywebview.api) ready();
})();
