(() => {
  "use strict";

  const status = document.querySelector("#status");
  const statusDot = document.querySelector(".status-dot");
  const device = document.querySelector("#device");
  const refresh = document.querySelector("#refresh");
  const capture = document.querySelector("#capture");
  const recalcRoute = document.querySelector("#recalc-route");
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
  const consoleLatest = document.querySelector("#console-latest");
  const boardGrid = document.querySelector("#board-grid");
  const boardSize = document.querySelector("#board-size");
  const reviewStatus = document.querySelector("#review-status");
  const unknownCount = document.querySelector("#unknown-count");
  const selectedCellLabel = document.querySelector("#selected-cell");
  const executionGate = document.querySelector("#execution-gate");
  const orbPalette = document.querySelector("#orb-palette");
  const hazardOrbGroup = document.querySelector("#hazard-orb-group");
  const enhanced = document.querySelector("#enhanced");
  const locked = document.querySelector("#locked");
  const correct = document.querySelector("#correct");
  const protect = document.querySelector("#protect");
  const clearProtect = document.querySelector("#clear-protect");
  const planningControls = document.querySelector("#planning-controls");
  const planningPanel = planningControls.closest(".planning-panel");
  const conditionRows = planningControls.querySelectorAll(".condition-row");
  const conditionTriggers = planningControls.querySelectorAll("[data-condition-trigger]");
  const conditionMenus = planningControls.querySelectorAll("[data-condition-menu]");
  const conditionCombo = document.querySelector("#condition-combo");
  const conditionComboMenu = document.querySelector("#condition-combo-menu");
  const conditionOperator = document.querySelector("#condition-operator");
  const hazardPolicy = document.querySelector("#hazard-policy");
  const externalCondition = document.querySelector("#external-condition");
  const searchAttempts = document.querySelector("#search-attempts");
  const searchSteps = document.querySelector("#search-steps");
  const searchSeed = document.querySelector("#search-seed");
  const searchCascade = document.querySelector("#search-cascade");
  const startSearch = document.querySelector("#start-search");
  const cancelSearch = document.querySelector("#cancel-search");
  const addCondition = document.querySelector("#add-condition");
  const activeProfile = document.querySelector("#active-profile");
  const searchProgress = document.querySelector("#search-progress");
  const searchPhase = document.querySelector("#search-phase");
  const searchProgressLabel = document.querySelector("#search-progress-label");
  const searchProgressBar = document.querySelector("#search-progress-bar");
  const searchProgressTrack = searchProgress.querySelector("[role='progressbar']");
  const searchResult = document.querySelector("#search-result");
  const resultSummary = document.querySelector("#result-summary");
  const resultConditions = document.querySelector("#result-conditions");
  const resultRouteSteps = document.querySelector("#result-route-steps");
  const resultRouteStart = document.querySelector("#result-route-start");
  const executeRoute = document.querySelector("#execute-route");
  const executeContinuous = document.querySelector("#execute-continuous");
  const stopExecution = document.querySelector("#stop-execution");
  const executionStatus = document.querySelector("#execution-status");
  const moveDelay = document.querySelector("#move-delay");
  const learningEnabled = document.querySelector("#learning-enabled");
  const verifyAfterGesture = document.querySelector("#verify-after-gesture");
  const verifyStatus = document.querySelector("#verify-status");
  const learningStatus = document.querySelector("#learning-status");
  const settingsExpansion = document.querySelector("#settings-expansion");
  const settingsToggleButtons = document.querySelectorAll("[data-settings-toggle]");
  const settingsTargetButtons = document.querySelectorAll("[data-settings-target]");
  const settingButtons = document.querySelectorAll("[data-setting-button]");

  let pendingSnapshot = null;
  let renderFrame = null;
  let pollTimer = null;
  let currentSnapshot = null;
  let selectedOrb = "fire";
  let confirmedLearningEnabled = false;
  let learningChangePending = false;
  let settingsSaveTimer = null;
  let restoringSettings = false;
  let readyPromise = null;
  const commandBackedSettingElements = new Set([
    boardSize, conditionOperator, hazardPolicy, learningEnabled, verifyAfterGesture,
  ]);
  const persistedSettingActions = new Set([
    "set_board_size", "set_rule_profile", "set_learning_enabled",
    "set_verify_after_gesture",
  ]);
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

  const NO_CONDITION = "不限（以最大 Combo 為主）";

  const settingNames = {
    "hazard-policy": "危害珠策略",
    "search-cascade": "落珠連鎖",
    "condition-operator": "條件關係",
    "verify-after-gesture": "轉珠後確認",
    "learning-enabled": "AI 模型學習",
    "search-steps": "步數",
    "search-attempts": "嘗試",
    "move-delay": "MOVE 間隔",
    "external-condition": "外部條件",
    "search-seed": "種子",
    "board-size": "盤面",
  };

  function settingValues(element) {
    return element && element.dataset.values
      ? element.dataset.values.split("|")
      : [];
  }

  function settingValue(element) {
    return element ? (element.dataset.value ?? element.value ?? "") : "";
  }

  function settingLabels(element) {
    return element && element.dataset.labels
      ? element.dataset.labels.split("|")
      : settingValues(element);
  }

  function settingDisplayValue(element) {
    const values = settingValues(element);
    const index = values.indexOf(settingValue(element));
    return settingLabels(element)[index < 0 ? 0 : index] || settingValue(element);
  }

  function settingCaption(element) {
    if (!element) return null;
    if (element.id !== "condition-operator") return null;
    return settingDisplayValue(element) === "任一符合" ? "任一" : "全部";
  }

  function syncSettingTile(element) {
    if (!element) return;
    const display = element.querySelector(".setting-value");
    if (display) display.textContent = settingDisplayValue(element);
    const caption = element.querySelector("small");
    const captionText = settingCaption(element);
    if (caption && captionText) caption.textContent = captionText;
    const values = settingValues(element);
    const count = values.length;
    if (count) {
      element.setAttribute(
        "aria-label",
        `${settingNames[element.id] || "設定"}：${settingDisplayValue(element)}，共 ${count} 段`,
      );
    }
  }

  function cycleSetting(element, reverse = false) {
    const values = settingValues(element);
    if (values.length < 2) return false;
    const current = values.indexOf(settingValue(element));
    const index = current < 0 ? 0 : current;
    element.dataset.value = values[
      (index + (reverse ? -1 : 1) + values.length) % values.length
    ];
    syncSettingTile(element);
    return true;
  }

  function settingIsDefault(element) {
    const values = settingValues(element);
    return values.length > 0 && settingValue(element) === values[0];
  }

  function renderShapeMatrix(button) {
    const matrix = button.querySelector(".shape-matrix");
    if (!matrix) return;
    const shape = button.dataset.conditionValue || button.dataset.shapeValue || "";
    const boardColumns = Number(settingValue(boardSize).split("x")[0]) || 6;
    const columns = shape === "色珠一橫列"
      ? boardColumns : Math.max(1, Number(button.dataset.cols) || 1);
    const mask = shape === "色珠一橫列"
      ? "1".repeat(boardColumns) : String(button.dataset.mask || "0");
    // 一橫列畫成一整條實心線，不畫成珠子：6／7 顆點擠在 48px 磚裡沒有邊界，
    // 而且「整列」的意思與盤面寬度無關，一條線比數點正確。
    if (shape === "色珠一橫列") {
      matrix.style.gridTemplateColumns = "auto";
      matrix.dataset.cols = "1";
      const bar = document.createElement("i");
      bar.className = "shape-bar";
      matrix.replaceChildren(bar);
      return;
    }
    matrix.style.gridTemplateColumns = `repeat(${columns}, auto)`;
    matrix.dataset.cols = String(columns);
    matrix.replaceChildren(...Array.from(mask, (value) => {
      const dot = document.createElement("i");
      dot.className = value === "1" ? "shape-dot filled" : "shape-dot";
      return dot;
    }));
  }

  function refreshShapeMatrices() {
    for (const button of planningControls.querySelectorAll(
      "[data-condition-trigger='shape'], .shape-option"
    )) renderShapeMatrix(button);
  }

  function syncConditionRow(row) {
    const color = row.dataset.colorValue || "不指定";
    const shape = row.dataset.shapeValue || "";
    const colorNames = { "不指定": "none", "火": "fire", "水": "water", "木": "wood",
      "光": "light", "暗": "dark", "心": "heart" };
    for (const type of ["color", "shape"]) {
      const trigger = row.querySelector(`[data-condition-trigger="${type}"]`);
      const menu = row.querySelector(`[data-condition-menu="${type}"]`);
      if (!trigger || !menu) continue;
      const value = type === "color" ? color : shape;
      const selected = Array.from(menu.querySelectorAll("[data-condition-value]"))
        .find((option) => option.dataset.conditionValue === value);
      if (type === "color") {
        const swatch = trigger.querySelector(".condition-color-swatch");
        if (swatch) swatch.dataset.orbColor = colorNames[color] || "none";
        trigger.setAttribute("aria-label", `指定色珠：${color}`);
        trigger.setAttribute("aria-pressed", "true");
      } else {
        trigger.dataset.shapeValue = shape;
        trigger.dataset.cols = selected ? selected.dataset.cols : "1";
        trigger.dataset.mask = selected ? selected.dataset.mask : "0";
        renderShapeMatrix(trigger);
        trigger.setAttribute("aria-label", `消法：${shape || "未指定"}`);
        trigger.setAttribute("aria-pressed", String(Boolean(shape)));
      }
      for (const option of menu.querySelectorAll("[data-condition-value]")) {
        option.setAttribute("aria-pressed", String(option.dataset.conditionValue === value));
      }
    }
  }

  function syncConditionCombo() {
    if (!conditionCombo || !conditionComboMenu) return;
    const combo = conditionCombo.dataset.comboValue || "";
    const selected = Array.from(conditionComboMenu.querySelectorAll("[data-condition-value]"))
      .find((option) => option.dataset.conditionValue === combo);
    const match = combo.match(/^至少 (\d+)/);
    const glyph = conditionCombo.querySelector(".combo-glyph");
    if (glyph) glyph.textContent = match ? `${match[1]}+` : "—";
    conditionCombo.setAttribute(
      "aria-label",
      `Combo 門檻：${match ? `至少 ${match[1]}` : "不限"}`,
    );
    conditionCombo.setAttribute("aria-pressed", String(Boolean(combo)));
    for (const option of conditionComboMenu.querySelectorAll("[data-condition-value]")) {
      option.setAttribute("aria-pressed", String(option === selected));
    }
  }

  function populateConditionMenus() {
    const templates = {
      color: document.querySelector("#condition-color-options"),
      shape: document.querySelector("#condition-shape-options"),
      combo: document.querySelector("#condition-combo-options"),
    };
    for (const row of conditionRows) {
      for (const menu of row.querySelectorAll("[data-condition-menu]")) {
        const template = templates[menu.dataset.conditionMenu];
        if (!template) continue;
        menu.replaceChildren(template.content.cloneNode(true));
        for (const matrix of menu.querySelectorAll(".shape-matrix")) renderShapeMatrix(matrix.parentElement);
      }
      for (const trigger of row.querySelectorAll("[data-condition-trigger='shape']")) {
        renderShapeMatrix(trigger);
      }
      syncConditionRow(row);
    }
    if (conditionComboMenu && templates.combo) {
      conditionComboMenu.replaceChildren(templates.combo.content.cloneNode(true));
    }
    syncConditionCombo();
  }
  function renumberConditionRows() {
    let number = 0;
    for (const row of conditionRows) {
      const remove = row.querySelector("[data-condition-remove]");
      if (row.hidden) {
        if (remove) {
          remove.disabled = true;
          remove.setAttribute("aria-label", "移除未啟用條件");
        }
        continue;
      }
      number += 1;
      const label = row.querySelector(".condition-row-label");
      if (label) label.textContent = `條件 ${number}`;
      row.setAttribute("aria-label", `條件 ${number}`);
      if (remove) {
        remove.disabled = false;
        remove.setAttribute("aria-label", `移除條件 ${number}`);
      }
    }
    addCondition.disabled = number >= conditionRows.length;
    addCondition.textContent = addCondition.disabled ? `已加入 ${number} 組` : "＋ 條件";
  }

  function settingIsActive(button) {
    const target = document.querySelector(`#${button.dataset.settingButton}`);
    if (!target) return false;
    if (target === hazardPolicy) return settingValue(target) === "允許危害珠";
    if (target === searchCascade) return settingValue(target) === "true";
    if (target === conditionOperator) return settingValue(target) === "任一符合";
    if (target === verifyAfterGesture) return settingValue(target) === "true";
    if (target === learningEnabled) return settingValue(target) === "true";
    if ([searchSteps, searchAttempts, moveDelay, externalCondition, searchSeed].includes(target)) {
      return !settingIsDefault(target);
    }
    return false;
  }

  function syncSettingButtons() {
    for (const button of settingButtons) {
      syncSettingTile(button);
      button.setAttribute("aria-pressed", String(settingIsActive(button)));
    }
  }

  function setSettingsExpanded(expanded, targetId = "") {
    settingsExpansion.hidden = !expanded;
    if (hazardOrbGroup) hazardOrbGroup.hidden = !expanded;
    for (const button of settingsToggleButtons) {
      button.setAttribute("aria-expanded", String(expanded));
      button.setAttribute("aria-pressed", String(expanded));
    }
    for (const button of settingsTargetButtons) {
      const active = expanded && button.dataset.settingsTarget === targetId;
      button.setAttribute("aria-expanded", String(active));
      button.setAttribute("aria-pressed", String(active));
    }
    if (!targetId) return;
    const detail = document.querySelector(`#${targetId}`);
    if (detail && detail.tagName === "DETAILS") detail.open = true;
  }

  function settingIsBusy(element) {
    return element.disabled || Boolean(currentSnapshot && (
      currentSnapshot.busy || currentSnapshot.operational_busy
    ));
  }


  populateConditionMenus();

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

  function boardShape(snapshot) {
    const size = snapshot && snapshot.board_size;
    return {
      rows: Number.isInteger(size && size.rows) ? size.rows : 5,
      cols: Number.isInteger(size && size.cols) ? size.cols : 6,
      label: (size && size.label) || "6\u00d75",
      name: (size && size.name) || "6x5",
    };
  }

  function renderBoard(snapshot) {
    const entries = Array.isArray(snapshot.board) ? snapshot.board : [];
    const selected = cellParts(snapshot.selected_cell);
    const shape = boardShape(snapshot);
    boardGrid.style.gridTemplateColumns = `repeat(${shape.cols}, minmax(0, 1fr))`;
    routePreviewGrid.style.gridTemplateColumns = boardGrid.style.gridTemplateColumns;
    boardGrid.setAttribute("aria-label", `${shape.rows} 列 ${shape.cols} 欄盤面`);
    boardGrid.replaceChildren();
    if (entries.length === 0) {
      const empty = document.createElement("span");
      empty.className = "board-empty";
      empty.textContent = `擷取畫面後顯示 ${shape.label} 盤面`;
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
        if (row < 0 || row >= shape.rows || col < 0 || col >= shape.cols) return;
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
    const conditions = Array.from(conditionRows).flatMap((row) => {
      if (row.hidden) return [];
      const color = row.dataset.colorValue || "不指定";
      const shape = row.dataset.shapeValue || "";
      return shape && shape !== NO_CONDITION ? [{ label: shape, color }] : [];
    });
    const combo = conditionCombo && conditionCombo.dataset.comboValue
      ? conditionCombo.dataset.comboValue : "";
    if (combo && combo !== NO_CONDITION) {
      conditions.push({ label: combo, color: "不指定" });
    }
    return {
      conditions,
      operator: settingValue(conditionOperator),
      hazard_policy: settingValue(hazardPolicy),
      external: settingValue(externalCondition),
    };
  }

  function searchPayload() {
    return {
      attempts: Number(settingValue(searchAttempts)),
      max_steps: Number(settingValue(searchSteps)),
      seed: Number(settingValue(searchSeed)),
      cascade: settingValue(searchCascade) === "true",
    };
  }

  function settingsPayload() {
    return {
      version: 1,
      rule_profile: planningPayload(),
      search: searchPayload(),
      board_size: settingValue(boardSize),
      move_delay: Number(settingValue(moveDelay)),
      learning_enabled: settingValue(learningEnabled) === "true",
      verify_after_gesture: settingValue(verifyAfterGesture) === "true",
    };
  }

  function scheduleSettingsSave() {
    if (restoringSettings) return;
    if (settingsSaveTimer !== null) window.clearTimeout(settingsSaveTimer);
    settingsSaveTimer = window.setTimeout(() => {
      settingsSaveTimer = null;
      command("save_settings", { settings: settingsPayload() });
    }, 300);
  }

  function validSavedRuleProfile(profile) {
    return Boolean(
      profile
      && typeof profile === "object"
      && Array.isArray(profile.conditions)
      && typeof profile.operator === "string"
      && typeof profile.hazard_policy === "string"
      && typeof profile.external === "string"
      && profile.conditions.every((condition) => (
        condition
        && typeof condition === "object"
        && typeof condition.label === "string"
        && typeof condition.color === "string"
      )),
    );
  }

  function validSavedSearch(search) {
    return Boolean(
      search
      && typeof search === "object"
      && Number.isInteger(search.attempts)
      && Number.isInteger(search.max_steps)
      && Number.isInteger(search.seed)
      && typeof search.cascade === "boolean",
    );
  }

  function setSavedSetting(element, value, numeric = false) {
    const values = settingValues(element);
    const next = numeric
      ? values.find((candidate) => Number(candidate) === Number(value))
      : String(value);
    if (next === undefined || !values.includes(next)) return false;
    element.dataset.value = next;
    syncSettingTile(element);
    return true;
  }

  function applySavedRuleProfile(profile) {
    const conditions = profile.conditions.filter(
      (condition) => !/^至少 \d+ Combo/.test(condition.label),
    );
    const combo = profile.conditions.find(
      (condition) => /^至少 \d+ Combo/.test(condition.label),
    );
    for (const [index, row] of Array.from(conditionRows).entries()) {
      row.hidden = index > 0;
      row.removeAttribute("data-color-value");
      row.removeAttribute("data-shape-value");
    }
    for (const [index, condition] of conditions.entries()) {
      const row = conditionRows[index];
      if (!row) break;
      row.hidden = false;
      row.dataset.colorValue = condition.color;
      row.dataset.shapeValue = condition.label;
      syncConditionRow(row);
    }
    if (conditionCombo) conditionCombo.dataset.comboValue = combo ? combo.label : "";
    conditionOperator.dataset.value = profile.operator;
    hazardPolicy.dataset.value = profile.hazard_policy;
    externalCondition.dataset.value = profile.external;
    syncConditionCombo();
    renumberConditionRows();
    syncSettingButtons();
  }

  function waitForBackendIdle() {
    return new Promise((resolve) => {
      const started = Date.now();
      const check = () => {
        const snapshot = pendingSnapshot || currentSnapshot;
        if (
          !snapshot
          || (!snapshot.busy && !snapshot.operational_busy)
          || Date.now() - started >= 5000
        ) {
          resolve();
          return;
        }
        window.setTimeout(check, 50);
      };
      check();
    });
  }

  async function restoreSavedSettings() {
    let settings;
    try {
      settings = await command("load_settings");
    } catch (_error) {
      return;
    }
    if (!settings || typeof settings !== "object" || settings.version !== 1) return;
    restoringSettings = true;
    try {
      const savedBoardSize = settings.board_size;
      if (savedBoardSize === "6x5" || savedBoardSize === "7x6") {
        const previous = settingValue(boardSize);
        if (previous !== savedBoardSize && setSavedSetting(boardSize, savedBoardSize)) {
          const reply = await command("set_board_size", { size: savedBoardSize });
          if (reply) await waitForBackendIdle();
          else setSavedSetting(boardSize, previous);
        }
      }

      if (validSavedRuleProfile(settings.rule_profile)) {
        const reply = await command("set_rule_profile", settings.rule_profile);
        if (reply) {
          applySavedRuleProfile(settings.rule_profile);
          await waitForBackendIdle();
        }
      }

      if (validSavedSearch(settings.search)) {
        setSavedSetting(searchAttempts, settings.search.attempts, true);
        setSavedSetting(searchSteps, settings.search.max_steps, true);
        setSavedSetting(searchSeed, settings.search.seed, true);
        setSavedSetting(searchCascade, settings.search.cascade);
      }
      if (Number.isFinite(settings.move_delay) && settings.move_delay >= 0) {
        setSavedSetting(moveDelay, settings.move_delay, true);
      }
      if (
        typeof settings.learning_enabled === "boolean"
        && setSavedSetting(learningEnabled, settings.learning_enabled)
      ) {
        const previous = confirmedLearningEnabled;
        const reply = await command("set_learning_enabled", {
          enabled: settings.learning_enabled,
        });
        if (reply) confirmedLearningEnabled = settings.learning_enabled;
        else setSavedSetting(learningEnabled, previous);
      }
      if (
        typeof settings.verify_after_gesture === "boolean"
        && setSavedSetting(verifyAfterGesture, settings.verify_after_gesture)
      ) {
        const previous = settingValue(verifyAfterGesture);
        const reply = await command("set_verify_after_gesture", {
          enabled: settings.verify_after_gesture,
        });
        if (!reply) setSavedSetting(verifyAfterGesture, previous);
      }
      syncSettingButtons();
    } finally {
      restoringSettings = false;
    }
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
    const canSearch = !busy && hasBoard && Boolean(profile) && !searching;
    startSearch.disabled = !canSearch;
    recalcRoute.disabled = !canSearch;
    cancelSearch.disabled = search.status !== "running";

    const progress = search.progress;
    const completed = progress && Number.isFinite(Number(progress.completed))
      ? Math.max(0, Number(progress.completed)) : 0;
    const total = progress && Number.isFinite(Number(progress.total))
      ? Math.max(0, Number(progress.total)) : 0;
    let phaseText = "搜尋尚未開始。";
    if (search.status === "running") {
      phaseText = progress
        ? `搜尋階段：${progress.phase || "處理中"}`
        : "搜尋已開始，等待後端階段…";
    } else if (search.status === "cancelling") {
      phaseText = "正在取消搜尋；等待目前後端階段安全結束。";
    } else if (search.status === "cancelled") {
      phaseText = "搜尋已取消。";
    } else if (search.status === "stale") {
      phaseText = "舊搜尋結果已失效，未套用。";
    } else if (search.status === "failed") {
      phaseText = "搜尋失敗；請查看事件主控台。";
    } else if (search.status === "complete") {
      phaseText = "搜尋完成。";
    }
    searchPhase.textContent = phaseText;
    searchProgressLabel.textContent = total > 0 ? `${completed} / ${total}` : "等待中";
    const percentage = total > 0
      ? Math.min(100, Math.round((completed / total) * 100))
      : search.status === "complete" ? 100 : 0;
    searchProgressBar.style.width = `${percentage}%`;
    searchProgressTrack.setAttribute("aria-valuenow", String(percentage));

    const result = search.result;
    const selected = result && result.selected;
    const candidate = selected === "qualifying"
      ? result.qualifying_candidate
      : selected === "diagnostic" ? result.diagnostic_candidate : null;
    if (!candidate) {
      searchResult.hidden = true;
      resultSummary.textContent = "";
      resultConditions.replaceChildren();
      resultRouteSteps.textContent = "— 步";
      resultRouteStart.textContent = "起點：—";
      return;
    }
    searchResult.hidden = false;
    const executable = Boolean(candidate.execution_eligible);
    searchResult.className = `result-card ${executable ? "qualifying" : "diagnostic"}`;
    const label = executable
      ? "符合條件候選（可進入後續 Python 安全流程）"
      : "診斷預覽（不可執行）";
    const diagnostic = candidate.diagnostic || candidate.diagnostic_status || "";
    resultSummary.textContent = diagnostic ? `${label}；${diagnostic}` : label;
    resultConditions.replaceChildren();
    const conditions = Array.isArray(candidate.conditions) ? candidate.conditions : [];
    if (conditions.length === 0) {
      const item = document.createElement("li");
      item.className = "muted";
      item.textContent = "尚無條件核對結果";
      resultConditions.append(item);
    } else {
      for (const condition of conditions) {
        const item = document.createElement("li");
        item.className = condition.satisfied ? "satisfied" : "unsatisfied";
        item.textContent = `${condition.satisfied ? "通過" : "未通過"}：${
          condition.message || condition.identifier || "條件"
        }`;
        resultConditions.append(item);
      }
    }
    const route = Array.isArray(candidate.route) ? candidate.route : [];
    resultRouteSteps.textContent = `${route.length} 步`;
    resultRouteStart.textContent = `起點：${route.length ? cellText(route[0]) : "—"}`;
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
    const nextBoardSize = boardShape(snapshot).name;
    if (settingValue(boardSize) !== nextBoardSize) {
      boardSize.dataset.value = nextBoardSize;
      syncSettingTile(boardSize);
      refreshShapeMatrices();
    }
    boardSize.disabled = busy || operationalBusy;
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
    const executable = Boolean(routeResult && routeResult.execution_eligible && snapshot.confirmed);
    executionGate.textContent = !hasBoard
      ? "尚未載入盤面。"
      : unknown > 0
        ? `含 ${unknown} 個未知格；不可確認或執行。`
        : routeResult && !routeResult.execution_eligible
          ? "目前候選是診斷預覽；不可執行。"
          : routeResult
            ? "目前路徑符合條件；可執行安全手勢流程。"
              : "尚無目前可執行候選。";
    correct.disabled = primaryMutationBusy || !hasSelection;
    protect.disabled = primaryMutationBusy || !hasSelection;
    const verify = snapshot.verify_after_gesture !== false;
    verifyAfterGesture.dataset.value = String(verify);
    syncSettingTile(verifyAfterGesture);
    verifyAfterGesture.disabled = executionBusy;
    verifyStatus.textContent = verify ? "轉珠後：停手確認盤面" : "轉珠後：直接放手（不確認）";
    if (!learningChangePending) confirmedLearningEnabled = Boolean(snapshot.learning_enabled);
    learningEnabled.dataset.value = String(confirmedLearningEnabled);
    syncSettingTile(learningEnabled);
    learningEnabled.disabled = learningChangePending;
    learningStatus.textContent = learningChangePending
      ? "AI 模型學習：更新中…"
      : `AI 模型學習：${confirmedLearningEnabled ? "開啟" : "關閉"}`;
    for (const button of orbPalette.querySelectorAll("button[data-orb]")) {
      button.disabled = primaryMutationBusy || !hasSelection;
      button.setAttribute("aria-pressed", String(button.dataset.orb === selectedOrb));
    }
    enhanced.checked = Boolean(selectedEntry && selectedEntry.enhanced);
    locked.checked = Boolean(selectedEntry && selectedEntry.locked);
    syncOrbFlags(hasSelection, primaryMutationBusy);
    syncSettingButtons();
    renderSearch(snapshot, hasBoard, busy);
    executeRoute.disabled = primaryMutationBusy || !executable;
    executeRoute.hidden = executionBusy;
    executeContinuous.disabled = primaryMutationBusy || !executable;
    executeContinuous.hidden = executionBusy;
    stopExecution.disabled = !executionBusy;
    stopExecution.hidden = !executionBusy;
    moveDelay.disabled = primaryMutationBusy || executionBusy;
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
    consoleLatest.textContent = latestConsole
      ? latestConsole.message || "無訊息"
      : "尚未有事件";
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
      if (action !== "load_settings") applyReply(reply);
      if (reply && persistedSettingActions.has(action)) scheduleSettingsSave();
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
    if (!window.pywebview || !window.pywebview.api || readyPromise) return readyPromise;
    readyPromise = (async () => {
      if (pollTimer === null) pollTimer = window.setInterval(pollEvents, 200);
      const snapshot = await command("snapshot");
      if (!snapshot) return;
      await new Promise((resolve) => window.requestAnimationFrame(resolve));
      await restoreSavedSettings();
    })();
    return readyPromise;
  }

  function attachSettingCycler(element, onChange = null) {
    const advance = (event) => {
      if (settingIsBusy(element)) return;
      const changed = cycleSetting(element, Boolean(event.shiftKey));
      if (!changed) return;
      syncSettingButtons();
      if (onChange) onChange(element);
      if (!commandBackedSettingElements.has(element)) scheduleSettingsSave();
    };
    element.addEventListener("click", advance);
    element.addEventListener("wheel", (event) => {
      event.preventDefault();
      advance({ shiftKey: event.deltaY < 0 });
    }, { passive: false });
    element.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      advance({ shiftKey: event.key === "ArrowLeft" });
    });
  }

  async function toggleLearning() {
    if (learningChangePending) return;
    const enabled = settingValue(learningEnabled) === "true";
    learningChangePending = true;
    learningEnabled.disabled = true;
    learningStatus.textContent = "AI 模型學習：更新中…";
    const reply = await command("set_learning_enabled", { enabled });
    learningChangePending = false;
    if (reply) {
      renderSnapshot(reply.snapshot || reply);
      return;
    }
    learningEnabled.dataset.value = String(confirmedLearningEnabled);
    learningEnabled.disabled = false;
    learningStatus.textContent = `AI 模型學習：${confirmedLearningEnabled ? "開啟" : "關閉"}`;
    syncSettingButtons();
  }

  for (const button of settingsToggleButtons) {
    button.addEventListener("click", () => setSettingsExpanded(settingsExpansion.hidden));
  }
  for (const button of settingsTargetButtons) {
    button.addEventListener("click", () => setSettingsExpanded(true, button.dataset.settingsTarget));
  }
  for (const button of settingButtons) {
    const target = document.querySelector(`#${button.dataset.settingButton}`);
    if (!target) continue;
    attachSettingCycler(target, () => {
      if (target === learningEnabled) {
        toggleLearning();
      } else if (target === verifyAfterGesture) {
        command("set_verify_after_gesture", {
          enabled: settingValue(verifyAfterGesture) === "true",
        });
      } else if ([conditionOperator, hazardPolicy, externalCondition].includes(target)) {
        updateProfile();
      }
    });
  }
  addCondition.addEventListener("click", () => {
    const next = Array.from(conditionRows).find((row) => row.hidden);
    if (!next) return;
    next.removeAttribute("data-color-value");
    next.removeAttribute("data-shape-value");
    next.hidden = false;
    syncConditionRow(next);
    renumberConditionRows();
    updateProfile();
  });

  renumberConditionRows();
  hazardOrbGroup.hidden = true;
  syncSettingButtons();


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
  attachSettingCycler(boardSize, () => {
    refreshShapeMatrices();
    command("set_board_size", { size: settingValue(boardSize) });
  });
  function positionConditionMenu(menu, trigger) {
    if (!planningPanel || !menu || !trigger) return;
    const choice = trigger.closest(".condition-choice");
    if (!choice) return;
    const choiceRect = choice.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const panelRect = planningPanel.getBoundingClientRect();
    const panelLeft = panelRect.left + planningPanel.clientLeft;
    const panelTop = panelRect.top + planningPanel.clientTop;
    const panelRight = panelLeft + planningPanel.clientWidth;
    const panelBottom = panelTop + planningPanel.clientHeight;
    const centeredLeft = choiceRect.left + (choiceRect.width - menuRect.width) / 2;
    const left = Math.max(panelLeft, Math.min(centeredLeft, panelRight - menuRect.width));
    menu.style.left = `${left - choiceRect.left}px`;
    menu.style.right = "auto";
    menu.style.transform = "none";
    const belowTop = choiceRect.bottom + 14;
    const aboveBottom = choiceRect.top - 14;
    if (belowTop + menuRect.height <= panelBottom
        || aboveBottom - menuRect.height < panelTop) {
      menu.style.top = `${choiceRect.height + 14}px`;
      menu.style.bottom = "auto";
    } else {
      menu.style.top = "auto";
      menu.style.bottom = `${choiceRect.height + 14}px`;
    }
  }

  function repositionOpenConditionMenus() {
    for (const menu of conditionMenus) {
      if (menu.hidden) continue;
      positionConditionMenu(
        menu,
        menu.parentElement.querySelector("[data-condition-trigger]"),
      );
    }
  }

  function closeConditionMenus(except = null) {
    for (const menu of conditionMenus) {
      if (menu !== except) menu.hidden = true;
    }
  }
  for (const trigger of conditionTriggers) {
    trigger.addEventListener("click", () => {
      const menu = trigger.parentElement.querySelector(".condition-menu");
      if (!menu) return;
      const opening = menu.hidden;
      closeConditionMenus(menu);
      menu.hidden = !opening;
      if (opening) positionConditionMenu(menu, trigger);
    });
  }
  planningControls.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-condition-remove]");
    if (remove) {
      const row = remove.closest(".condition-row");
      if (!row || row.hidden) return;
      row.hidden = true;
      row.removeAttribute("data-color-value");
      row.removeAttribute("data-shape-value");
      closeConditionMenus();
      renumberConditionRows();
      updateProfile();
      return;
    }
    const option = event.target.closest(".condition-option");
    if (!option) return;
    const menu = option.closest(".condition-menu");
    const row = option.closest(".condition-row");
    if (!menu) return;
    const value = option.dataset.conditionValue || "";
    if (row) {
      row.dataset[`${menu.dataset.conditionMenu}Value`] = value;
      syncConditionRow(row);
    } else if (menu === conditionComboMenu) {
      conditionCombo.dataset.comboValue = value;
      syncConditionCombo();
    } else {
      return;
    }
    closeConditionMenus();
    updateProfile();
  });
  startSearch.addEventListener("click", () => command("search_route", searchPayload()));
  recalcRoute.addEventListener("click", () => command("search_route", searchPayload()));
  cancelSearch.addEventListener("click", () => command("cancel_search"));
  executeRoute.addEventListener("click", () => {
    const serial = currentSnapshot && currentSnapshot.selected_device;
    if (serial) command("execute_route", { serial, delay: Number(settingValue(moveDelay)) });
  });
  executeContinuous.addEventListener("click", () => {
    const serial = currentSnapshot && currentSnapshot.selected_device;
    if (serial) command("execute_continuously", { serial, delay: Number(settingValue(moveDelay)) });
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
    repositionOpenConditionMenus();
  });
  window.addEventListener("pywebviewready", ready);
  if (window.pywebview && window.pywebview.api) ready();
})();
