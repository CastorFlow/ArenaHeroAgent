(() => {
  "use strict";

  const CHANNEL = "arena-hero-route-overlay/v1";
  const OVERLAY_ATTRIBUTE = "data-arena-hero-agent-route-overlay";
  const MODE_LABELS = {
    develop: "发育",
    aggress: "侵略",
    beacon: "抢信标",
    migrate: "迁移",
  };
  const core = globalThis.ArenaHeroOverlayCore;
  if (!core) {
    return;
  }

  const state = {
    mapCanvas: null,
    overlay: null,
    context: null,
    camera: null,
    payload: { version: 2, tick: 0, routes: [], units: [], resources: [] },
    browserIntel: { version: 1, source: "browser", captured_at: null, resources: [] },
    stats: null,
    logs: core.normalizeLogs({}),
    control: {
      mode: "develop",
      recall: false,
      raid_enabled: false,
      raid_recall: false,
      raid_vanguards: 1,
      raid_rangers: 2,
      beacon_target_distance: 0,
    },
    settings: core.normalizeSettings({}),
    serviceOnline: false,
    pointer: null,
    pointerOverControls: false,
    selectedCell: null,
    distanceCard: null,
    distanceCardPosition: null,
    distanceCardDetail: null,
    clickMapCanvas: null,
    mapClickHandler: null,
    pageClickHandler: null,
    tickTiming: {
      lastTick: null,
      lastAt: 0,
      secondsPerTick: 5,
    },
    lastCanvasSearch: 0,
    lastBrowserIntelCapture: 0,
    browserEmptyCaptures: 0,
    toolbar: null,
    routeToggle: null,
    settingsButton: null,
    settingsPanel: null,
    settingsOpen: false,
    settingInputs: new Map(),
    statusBar: null,
    statusMetrics: null,
    statusActions: null,
    modeButton: null,
    recallButton: null,
    raidButton: null,
    raidRecallButton: null,
    statsButton: null,
    statsPanel: null,
    statsOpen: false,
    locatorButton: null,
    locatorPanel: null,
    locatorOpen: false,
    locatorTimer: null,
    logsButton: null,
    logsPanel: null,
    logsOpen: false,
    logsList: null,
    logsCategory: null,
    logsLevel: null,
    logsSearch: null,
    lastLogEventId: null,
    unreadLogs: 0,
    followUnit: null,
    followPositionKey: null,
    statusElements: new Map(),
    statsCounterContainers: new Map(),
  };

  function arenaPageVisible() {
    return location.hostname === "app.arenahero.io" && location.pathname.startsWith("/arena");
  }

  function officialDialogVisible() {
    const selector = [
      '[role="dialog"]',
      '[role="alertdialog"]',
      '[role="menu"]',
      '[role="listbox"]',
      '[aria-modal="true"]',
      '[data-radix-popper-content-wrapper]',
      '[data-state="open"][class*="dialog" i]',
      '[data-state="open"][class*="modal" i]',
      '[class*="modal" i]',
    ].join(",");
    for (const element of document.querySelectorAll(selector)) {
      if (!(element instanceof HTMLElement) || element.closest(`[${OVERLAY_ATTRIBUTE}]`)) {
        continue;
      }
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        Number(style.opacity || "1") > 0 &&
        rect.width > 40 &&
        rect.height > 30
      ) {
        return true;
      }
    }
    return false;
  }

  function applyButtonStyle(button) {
    Object.assign(button.style, {
      border: "1px solid rgba(255,255,255,0.2)",
      borderRadius: "7px",
      background: "rgba(8,11,18,0.88)",
      color: "#d9e1eb",
      font: "600 12px system-ui, -apple-system, Segoe UI, sans-serif",
      lineHeight: "28px",
      height: "30px",
      padding: "0 10px",
      cursor: "pointer",
      boxShadow: "0 2px 10px rgba(0,0,0,0.28)",
    });
  }

  function createOverlay() {
    if (state.overlay || !document.documentElement) {
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.setAttribute(OVERLAY_ATTRIBUTE, "true");
    Object.assign(canvas.style, {
      position: "fixed",
      left: "0",
      top: "0",
      width: "0",
      height: "0",
      pointerEvents: "none",
      zIndex: "4",
      display: "none",
    });
    document.documentElement.appendChild(canvas);
    state.overlay = canvas;
    state.context = canvas.getContext("2d");
    bindPageClick();
  }

  function controlContainer(tagName) {
    const element = document.createElement(tagName);
    element.setAttribute(OVERLAY_ATTRIBUTE, "control");
    element.addEventListener("pointerenter", () => {
      state.pointerOverControls = true;
    });
    element.addEventListener("pointerleave", () => {
      state.pointerOverControls = false;
    });
    for (const eventName of ["pointerdown", "click", "wheel"]) {
      element.addEventListener(eventName, (event) => event.stopPropagation());
    }
    return element;
  }

  function addCheckbox(panel, key, labelText) {
    const label = document.createElement("label");
    Object.assign(label.style, {
      display: "flex",
      alignItems: "center",
      gap: "8px",
      minHeight: "26px",
      cursor: "pointer",
    });
    const input = document.createElement("input");
    input.type = "checkbox";
    input.addEventListener("change", () => updateSettings({ [key]: input.checked }));
    const text = document.createElement("span");
    text.textContent = labelText;
    label.append(input, text);
    panel.appendChild(label);
    state.settingInputs.set(key, { input, kind: "checkbox" });
  }

  function addRange(panel, key, labelText, minimum, maximum, step, suffix) {
    const row = document.createElement("label");
    Object.assign(row.style, {
      display: "grid",
      gridTemplateColumns: "88px 1fr 42px",
      alignItems: "center",
      gap: "7px",
      minHeight: "30px",
    });
    const label = document.createElement("span");
    label.textContent = labelText;
    const input = document.createElement("input");
    input.type = "range";
    input.min = String(minimum);
    input.max = String(maximum);
    input.step = String(step);
    input.style.width = "100%";
    const value = document.createElement("span");
    value.style.textAlign = "right";
    value.style.fontFamily = "ui-monospace, SFMono-Regular, Consolas, monospace";
    input.addEventListener("input", () => {
      updateSettings({ [key]: Number(input.value) });
    });
    row.append(label, input, value);
    panel.appendChild(row);
    state.settingInputs.set(key, { input, value, kind: "range", suffix });
  }

  function addColor(panel, key, labelText) {
    const row = document.createElement("label");
    Object.assign(row.style, {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      minHeight: "28px",
    });
    const label = document.createElement("span");
    label.textContent = labelText;
    const input = document.createElement("input");
    input.type = "color";
    Object.assign(input.style, {
      width: "42px",
      height: "22px",
      padding: "0",
      border: "0",
      background: "transparent",
      cursor: "pointer",
    });
    input.addEventListener("input", () => updateSettings({ [key]: input.value }));
    row.append(label, input);
    panel.appendChild(row);
    state.settingInputs.set(key, { input, kind: "color" });
  }

  function addControlNumber(
    panel,
    key,
    labelText,
    hintText,
    { maximum = 300, step = 5 } = {},
  ) {
    const row = document.createElement("div");
    Object.assign(row.style, {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      minHeight: "28px",
    });
    const label = document.createElement("span");
    label.textContent = labelText;
    label.title = hintText;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = String(maximum);
    input.step = String(step);
    Object.assign(input.style, {
      width: "64px",
      padding: "2px 6px",
      border: "1px solid rgba(255,255,255,0.25)",
      borderRadius: "5px",
      background: "rgba(255,255,255,0.08)",
      color: "#d9e1eb",
      font: "12px system-ui, -apple-system, Segoe UI, sans-serif",
    });
    input.addEventListener("change", () => {
      const value = Math.min(
        maximum,
        Math.max(0, Number.parseInt(input.value, 10) || 0),
      );
      updateControl({ [key]: value });
      input.value = String(value);
    });
    row.append(label, input);
    panel.appendChild(row);
    state.settingInputs.set(key, { input, kind: "number" });
  }

  function createLocatorPanel() {
    if (state.locatorPanel || !document.documentElement) {
      return;
    }
    const panel = controlContainer("div");
    Object.assign(panel.style, {
      position: "fixed",
      display: "none",
      width: "min(330px, calc(100vw - 16px))",
      maxHeight: "72vh",
      overflowY: "auto",
      padding: "10px 12px 12px",
      border: "1px solid rgba(255,255,255,0.2)",
      borderRadius: "8px",
      background: "rgba(8,11,18,0.96)",
      color: "#d9e1eb",
      font: "12px system-ui, -apple-system, Segoe UI, sans-serif",
      boxShadow: "0 8px 28px rgba(0,0,0,0.42)",
      zIndex: "2147483001",
      pointerEvents: "auto",
      userSelect: "none",
      boxSizing: "border-box",
    });
    const header = document.createElement("div");
    Object.assign(header.style, {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      marginBottom: "8px",
      fontSize: "13px",
      fontWeight: 600,
    });
    header.textContent = "🎯 定位 · 点击条目聚焦地图";
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "✕";
    Object.assign(close.style, {
      background: "transparent",
      border: "none",
      color: "#9fb0c0",
      cursor: "pointer",
      fontSize: "14px",
      padding: "2px 6px",
    });
    close.addEventListener("click", () => {
      state.locatorOpen = false;
      syncControls();
    });
    header.append(close);
    panel.appendChild(header);

    const unitSection = document.createElement("div");
    const unitTitle = document.createElement("div");
    Object.assign(unitTitle.style, {
      color: "#8fbbae",
      fontWeight: 600,
      margin: "6px 0 4px",
      fontSize: "11px",
    });
    unitTitle.textContent = "单位";
    const unitList = document.createElement("div");
    unitSection.append(unitTitle, unitList);

    const eventSection = document.createElement("div");
    const eventTitle = document.createElement("div");
    Object.assign(eventTitle.style, {
      color: "#d9a05b",
      fontWeight: 600,
      margin: "10px 0 4px",
      fontSize: "11px",
    });
    eventTitle.textContent = "最近事件";
    const eventList = document.createElement("div");
    eventSection.append(eventTitle, eventList);

    panel.append(unitSection, eventSection);
    document.documentElement.appendChild(panel);
    state.locatorPanel = panel;
    state.locatorUnitList = unitList;
    state.locatorEventList = eventList;
  }

  function centerMapOn(position) {
    const mapCanvas = findMapCanvas(performance.now());
    if (!mapCanvas || !core.centerCameraOn(mapCanvas, position)) {
      return false;
    }
    if (state.camera) {
      state.camera = {
        ...state.camera,
        x: Number(position[0]),
        y: Number(position[1]),
      };
    }
    return true;
  }

  function focusTarget(position, label) {
    state.followUnit = null;
    state.followPositionKey = null;
    state.focusMarker = { position, label, until: performance.now() + 8000 };
    centerMapOn(position);
  }

  function followUnit(unitId) {
    state.focusMarker = null;
    state.followUnit = state.followUnit === unitId ? null : unitId;
    state.followPositionKey = null;
    if (state.followUnit) {
      const units = Array.isArray(state.stats?.units) ? state.stats.units : [];
      const unit = units.find((entry) => entry.id === state.followUnit);
      if (unit && Array.isArray(unit.position)) {
        centerMapOn(unit.position);
        state.followPositionKey = `${unit.position[0]},${unit.position[1]}`;
      }
    }
    renderLocator();
  }

  function centerFollowedUnit() {
    if (!state.followUnit) {
      return;
    }
    const units = Array.isArray(state.stats?.units) ? state.stats.units : [];
    const unit = units.find((entry) => entry.id === state.followUnit);
    if (!unit || !Array.isArray(unit.position)) {
      state.followUnit = null;
      state.followPositionKey = null;
      return;
    }
    const positionKey = `${unit.position[0]},${unit.position[1]}`;
    if (positionKey !== state.followPositionKey && centerMapOn(unit.position)) {
      state.followPositionKey = positionKey;
    }
  }

  function renderLocator() {
    if (!state.locatorPanel) {
      return;
    }
    const stats = state.stats || {};
    const units = Array.isArray(stats.units) ? stats.units : [];
    const events = Array.isArray(stats.recent_events) ? stats.recent_events : [];

    const unitList = state.locatorUnitList;
    unitList.innerHTML = "";
    if (!units.length) {
      unitList.textContent = "暂无数据";
    } else {
      for (const unit of units) {
        const row = document.createElement("div");
        Object.assign(row.style, {
          padding: "2px 4px",
          borderRadius: "4px",
          cursor: "pointer",
          display: "flex",
          gap: "8px",
          alignItems: "center",
        });
        const active = state.followUnit === unit.id;
        row.style.background = active ? "rgba(240,185,60,0.18)" : "transparent";
        const typeIcon =
          unit.type === "WORKER" ? "⚒️" : unit.type === "VANGUARD" ? "🛡️" : "🏹";
        const pos = Array.isArray(unit.position) ? `(${unit.position[0]}, ${unit.position[1]})` : "?";
        row.textContent = `${typeIcon} ${unit.type[0]}${unit.number ?? ""}  ${pos}  HP ${unit.hp}/2`;
        row.addEventListener("mouseenter", () => {
          row.style.background = "rgba(255,255,255,0.08)";
        });
        row.addEventListener("mouseleave", () => {
          row.style.background = active ? "rgba(240,185,60,0.18)" : "transparent";
        });
        row.addEventListener("click", () => followUnit(unit.id));
        unitList.appendChild(row);
      }
    }

    const eventList = state.locatorEventList;
    eventList.innerHTML = "";
    if (!events.length) {
      eventList.textContent = "暂无战斗事件";
    } else {
      const labels = {
        SHOT_HIT: "🔫 命中",
        SHOT_MISSED: "💨 未中",
        UNIT_DESTROYED: "💀 击杀",
        UNIT_DAMAGED: "🩸 受伤",
        CORE_RESOURCES_CAPTURED: "🏴 掠夺",
        SWEEP_RESOLVED: "⚔️ 横扫",
        UNIT_SELF_DESTRUCTED: "💥 自毁",
        CORE_DESTROYED: "🔥 敌核心毁灭",
      };
      for (let i = events.length - 1; i >= 0; i--) {
        const event = events[i];
        const row = document.createElement("div");
        Object.assign(row.style, {
          padding: "2px 4px",
          borderRadius: "4px",
          cursor: "pointer",
          fontSize: "11px",
          display: "flex",
          gap: "6px",
        });
        const label = labels[event.type] || event.type;
        const pos = Array.isArray(event.position)
          ? `(${event.position[0]}, ${event.position[1]})`
          : "(位置未知)";
        row.textContent = `t${event.tick ?? "?"} ${label} ${pos}`;
        if (i === events.length - 1) {
          row.style.color = "#ffd479";
          row.style.fontWeight = 600;
        }
        row.addEventListener("mouseenter", () => {
          row.style.background = "rgba(255,255,255,0.08)";
        });
        row.addEventListener("mouseleave", () => {
          row.style.background = "transparent";
        });
        row.addEventListener("click", () => {
          if (Array.isArray(event.position)) {
            focusTarget(event.position, label);
          }
        });
        eventList.appendChild(row);
      }
    }
  }

  function createControls() {
    if (state.toolbar || !document.documentElement) {
      return;
    }
    const toolbar = controlContainer("div");
    Object.assign(toolbar.style, {
      position: "static",
      display: "none",
      alignItems: "center",
      gap: "4px",
      pointerEvents: "auto",
      flexWrap: "wrap",
    });

    const routeToggle = document.createElement("button");
    routeToggle.type = "button";
    routeToggle.title = "显示或隐藏虚拟路线（Alt+Shift+R）";
    applyButtonStyle(routeToggle);
    routeToggle.addEventListener("click", toggleRoutes);

    const settingsButton = document.createElement("button");
    settingsButton.type = "button";
    settingsButton.textContent = "⚙ 设置";
    settingsButton.title = "调整路线和高亮样式";
    applyButtonStyle(settingsButton);
    settingsButton.addEventListener("click", () => {
      const opening = !state.settingsOpen;
      state.settingsOpen = opening;
      if (opening) {
        state.statsOpen = false;
        state.locatorOpen = false;
        state.logsOpen = false;
      }
      syncControls();
    });
    toolbar.append(routeToggle, settingsButton);

    const panel = controlContainer("div");
    Object.assign(panel.style, {
      position: "fixed",
      display: "none",
      width: "min(300px, calc(100vw - 16px))",
      maxHeight: "72vh",
      overflowY: "auto",
      padding: "11px 12px 12px",
      border: "1px solid rgba(255,255,255,0.2)",
      borderRadius: "8px",
      background: "rgba(8,11,18,0.94)",
      color: "#d9e1eb",
      font: "12px system-ui, -apple-system, Segoe UI, sans-serif",
      boxShadow: "0 8px 28px rgba(0,0,0,0.42)",
      zIndex: "2147483001",
      pointerEvents: "auto",
      userSelect: "none",
      boxSizing: "border-box",
    });
    const title = document.createElement("div");
    title.textContent = "Arena Hero 叠加层";
    Object.assign(title.style, {
      fontWeight: "700",
      fontSize: "13px",
      marginBottom: "4px",
    });
    const shortcut = document.createElement("div");
    shortcut.textContent = "快捷键：Alt+Shift+R 路线 · Alt+Shift+L 日志 · Alt+Shift+1 发育 · Alt+Shift+2 侵略 · Alt+Shift+C 召回";
    Object.assign(shortcut.style, {
      color: "#8f9cad",
      fontSize: "11px",
      marginBottom: "7px",
    });
    panel.append(title, shortcut);
    addCheckbox(panel, "showRoutes", "显示虚拟路线");
    addCheckbox(panel, "showUnitLabels", "显示兵种编号");
    addCheckbox(panel, "showResources", "高亮当前资源");
    addRange(panel, "lineWidth", "线条粗细", 0.5, 5, 0.1, "px");
    addRange(panel, "opacity", "路线透明度", 0.1, 1, 0.05, "");
    addColor(panel, "workerColor", "工人路线颜色");
    addColor(panel, "vanguardColor", "先锋路线颜色");
    addColor(panel, "rangerColor", "游侠路线颜色");
    addColor(panel, "resourceColor", "资源高亮颜色");
    addControlNumber(
      panel,
      "beacon_target_distance",
      "core↔信标目标距离",
      "core 与信标的目标距离（格）：0=关闭；距离大于设定时 core 向信标推进，小于则远离",
    );
    addControlNumber(
      panel,
      "raid_vanguards",
      "偷袭先锋人数",
      "独立偷袭编组的先锋数量；0 表示不抽调先锋",
      { maximum: 19, step: 1 },
    );
    addControlNumber(
      panel,
      "raid_rangers",
      "偷袭游侠人数",
      "独立偷袭编组的游侠数量；0 表示不抽调游侠",
      { maximum: 19, step: 1 },
    );

    const reset = document.createElement("button");
    reset.type = "button";
    reset.textContent = "恢复默认显示";
    applyButtonStyle(reset);
    Object.assign(reset.style, {
      width: "100%",
      marginTop: "8px",
      color: "#b9c4d2",
    });
    reset.addEventListener("click", () => {
      state.settings = core.normalizeSettings(core.DEFAULT_SETTINGS);
      persistSettings();
      syncControls();
    });
    panel.appendChild(reset);

    document.documentElement.append(toolbar, panel);
    state.toolbar = toolbar;
    state.routeToggle = routeToggle;
    state.settingsButton = settingsButton;
    state.settingsPanel = panel;
    createStatusBar();
    createDistanceCard();
    createStatsPanel();
    createLogsPanel();
    syncControls();
  }

  function createStatusBar() {
    if (state.statusBar || !document.documentElement) {
      return;
    }
    const bar = controlContainer("div");
    Object.assign(bar.style, {
      position: "fixed",
      display: "none",
      flexDirection: "column",
      alignItems: "stretch",
      gap: "5px",
      padding: "6px 8px",
      border: "1px solid rgba(255,255,255,0.2)",
      borderRadius: "7px",
      background: "rgba(8,11,18,0.92)",
      color: "#d9e1eb",
      font: "600 12px system-ui, -apple-system, Segoe UI, sans-serif",
      boxShadow: "0 2px 10px rgba(0,0,0,0.28)",
      zIndex: "2147483000",
      pointerEvents: "auto",
      userSelect: "none",
      boxSizing: "border-box",
      minWidth: "0",
    });

    const metrics = document.createElement("div");
    Object.assign(metrics.style, {
      display: "flex",
      alignItems: "center",
      flexWrap: "wrap",
      minHeight: "20px",
      rowGap: "2px",
      overflow: "hidden",
    });

    const actions = document.createElement("div");
    Object.assign(actions.style, {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      flexWrap: "wrap",
      gap: "4px 10px",
    });

    const primaryActions = document.createElement("div");
    Object.assign(primaryActions.style, {
      display: "flex",
      alignItems: "center",
      flexWrap: "wrap",
      gap: "4px",
    });

    const utilityActions = document.createElement("div");
    Object.assign(utilityActions.style, {
      display: "flex",
      alignItems: "center",
      flexWrap: "wrap",
      gap: "4px",
    });

    const modeButton = document.createElement("button");
    modeButton.type = "button";
    applyButtonStyle(modeButton);
    modeButton.title = "切换模式：发育 / 侵略 / 抢信标（Alt+Shift+1/2/3）";
    modeButton.addEventListener("click", () => {
      const order = ["develop", "aggress", "beacon"];
      const next =
        order[(order.indexOf(state.control.mode) + 1) % order.length];
      updateControl({ mode: next });
    });

    const recallButton = document.createElement("button");
    recallButton.type = "button";
    applyButtonStyle(recallButton);
    recallButton.title = "一键召回（Alt+Shift+C）：所有游侠/先锋回核心防守，再点一次解除";
    recallButton.addEventListener("click", () => {
      updateControl({ recall: !state.control.recall });
    });

    const raidButton = document.createElement("button");
    raidButton.type = "button";
    applyButtonStyle(raidButton);
    raidButton.title = "独立偷袭编组：无限扩圈寻找无人值守的敌方 Core";
    raidButton.addEventListener("click", () => {
      updateControl({ raid_enabled: !state.control.raid_enabled });
    });

    const raidRecallButton = document.createElement("button");
    raidRecallButton.type = "button";
    applyButtonStyle(raidRecallButton);
    raidRecallButton.title = "只召回偷袭编组；侵略模式和其他单位不受影响";
    raidRecallButton.addEventListener("click", () => {
      updateControl({ raid_recall: !state.control.raid_recall });
    });

    const makeStatus = (key, title) => {
      const span = document.createElement("span");
      span.title = title;
      Object.assign(span.style, {
        color: "#aeb9c6",
        padding: "0 8px",
        lineHeight: "18px",
        whiteSpace: "nowrap",
        borderRight: "1px solid rgba(255,255,255,0.12)",
      });
      metrics.appendChild(span);
      state.statusElements.set(`bar:${key}`, span);
      return span;
    };

    makeStatus("tick", "当前 Tick");
    makeStatus("resources", "资源 / 容量");
    makeStatus("population", "人口 工/先/游");
    makeStatus("enemies", "可见敌人数量");
    makeStatus("core", "核心 HP / 盾");
    makeStatus("beacon", "信标状态");

    const statsButton = document.createElement("button");
    statsButton.type = "button";
    statsButton.textContent = "统计";
    statsButton.title = "显示/隐藏统计面板";
    applyButtonStyle(statsButton);
    statsButton.addEventListener("click", () => {
      state.statsOpen = !state.statsOpen;
      if (state.statsOpen) {
        state.settingsOpen = false;
        state.locatorOpen = false;
        state.logsOpen = false;
      }
      syncControls();
    });

    const locatorButton = document.createElement("button");
    locatorButton.type = "button";
    locatorButton.textContent = "🎯 定位";
    locatorButton.title = "打开定位面板：单位/事件一键追踪（点击条目聚焦地图）";
    applyButtonStyle(locatorButton);
    locatorButton.addEventListener("click", () => {
      state.locatorOpen = !state.locatorOpen;
      if (state.locatorOpen) {
        state.settingsOpen = false;
        state.statsOpen = false;
        state.logsOpen = false;
      }
      syncControls();
      renderLocator();
    });

    const logsButton = document.createElement("button");
    logsButton.type = "button";
    logsButton.textContent = "日志";
    logsButton.title = "显示/隐藏中文事件日志（Alt+Shift+L）";
    applyButtonStyle(logsButton);
    logsButton.addEventListener("click", () => {
      state.logsOpen = !state.logsOpen;
      if (state.logsOpen) {
        state.settingsOpen = false;
        state.statsOpen = false;
        state.locatorOpen = false;
        state.unreadLogs = 0;
        renderLogs();
      }
      syncControls();
    });

    primaryActions.append(modeButton, recallButton, raidButton, raidRecallButton);
    if (state.toolbar) {
      utilityActions.appendChild(state.toolbar);
    }
    utilityActions.append(statsButton, locatorButton, logsButton);
    actions.append(primaryActions, utilityActions);
    bar.append(metrics, actions);

    for (const button of bar.querySelectorAll("button")) {
      Object.assign(button.style, {
        height: "28px",
        lineHeight: "26px",
        padding: "0 8px",
        whiteSpace: "nowrap",
        boxShadow: "none",
      });
    }

    document.documentElement.appendChild(bar);
    state.statusBar = bar;
    state.statusMetrics = metrics;
    state.statusActions = actions;
    state.modeButton = modeButton;
    state.recallButton = recallButton;
    state.raidButton = raidButton;
    state.raidRecallButton = raidRecallButton;
    state.statsButton = statsButton;
    state.locatorButton = locatorButton;
    state.logsButton = logsButton;
    syncControls();
  }

  function createDistanceCard() {
    if (state.distanceCard || !document.documentElement) {
      return;
    }
    const card = document.createElement("div");
    card.setAttribute(OVERLAY_ATTRIBUTE, "distance");
    Object.assign(card.style, {
      position: "fixed",
      display: "none",
      padding: "7px 10px",
      border: "1px solid rgba(240,185,60,0.78)",
      borderRadius: "7px",
      background: "rgba(7,10,16,0.94)",
      color: "#eef2f7",
      font: "600 12px system-ui, -apple-system, Segoe UI, sans-serif",
      lineHeight: "19px",
      boxShadow: "0 3px 14px rgba(0,0,0,0.38)",
      zIndex: "2147483002",
      pointerEvents: "none",
      userSelect: "none",
      boxSizing: "border-box",
      visibility: "visible",
      opacity: "1",
      transform: "translateZ(0)",
      contain: "layout paint",
    });
    card.style.setProperty("z-index", "2147483647", "important");
    const position = document.createElement("div");
    position.style.color = "#f0b93c";
    const detail = document.createElement("div");
    detail.style.color = "#eef2f7";
    card.append(position, detail);
    document.documentElement.appendChild(card);
    state.distanceCard = card;
    state.distanceCardPosition = position;
    state.distanceCardDetail = detail;
  }

  function createStatsPanel() {
    if (state.statsPanel || !document.documentElement) {
      return;
    }
    const panel = controlContainer("div");
    Object.assign(panel.style, {
      position: "fixed",
      display: "none",
      width: "min(390px, calc(100vw - 16px))",
      maxHeight: "72vh",
      overflowY: "auto",
      padding: "10px 12px 12px",
      border: "1px solid rgba(255,255,255,0.2)",
      borderRadius: "8px",
      background: "rgba(8,11,18,0.96)",
      color: "#d9e1eb",
      font: "12px system-ui, -apple-system, Segoe UI, sans-serif",
      boxShadow: "0 8px 28px rgba(0,0,0,0.42)",
      zIndex: "2147483001",
      pointerEvents: "auto",
      userSelect: "none",
      boxSizing: "border-box",
    });

    const header = document.createElement("div");
    Object.assign(header.style, {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      marginBottom: "8px",
    });
    const title = document.createElement("span");
    title.textContent = "战况统计";
    Object.assign(title.style, { fontWeight: "700", fontSize: "13px" });
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "✕";
    close.title = "关闭统计面板";
    applyButtonStyle(close);
    Object.assign(close.style, {
      height: "24px",
      lineHeight: "22px",
      padding: "0 8px",
    });
    close.addEventListener("click", () => {
      state.statsOpen = false;
      syncControls();
    });
    header.append(title, close);
    panel.appendChild(header);

    const sections = [
      {
        heading: "实时快照",
        rows: [
          ["tick", "Tick"],
          ["mode", "模式"],
          ["recall", "召回状态"],
          ["raid_enabled", "偷袭模式"],
          ["raid_recall", "偷袭召回"],
          ["raid_selected", "偷袭编组 (先/游)"],
          ["raid_target", "偷袭目标 Core"],
          ["raid_sweep_radius", "偷袭扫荡半径"],
          ["migration_candidate", "迁移候选点"],
          ["migration_target", "迁移目标"],
          ["migration_site_checked", "候选点已检查"],
          ["migration_site_score", "障碍半侧评分"],
          ["resources", "资源 / 容量"],
          ["population", "人口 (工/先/游)"],
          ["core", "核心 HP / 盾"],
          ["core_position", "核心坐标"],
          ["beacon_position", "信标坐标"],
          ["visible_enemies", "可见敌人"],
          ["core_threat_count", "Core 周边敌军"],
          ["core_reinforcement_active", "主力回援"],
          ["owns_beacon", "持有信标"],
          ["visible_resource_cells", "当前可见矿点"],
          ["known_resource_cells", "记忆矿点"],
          ["browser_resource_hints", "浏览器矿点参考"],
          ["browser_intel_online", "浏览器数据状态"],
          ["browser_intel_age_seconds", "浏览器数据延迟"],
          ["worker_cargo", "工人携带资源"],
          ["exploring_workers", "向外探索工人"],
          ["max_worker_search_radius", "最远探索半径"],
          ["active_routes", "规划路线 / 完整路线"],
          ["tick_interval", "回合间隔"],
        ],
      },
      {
        heading: "累计统计",
        rows: [
          ["total_resources_harvested", "累计采集资源"],
          ["total_resources_deposited", "累计提交资源"],
          ["total_resources_captured", "掠夺敌人资源"],
          ["enemy_cores_destroyed", "摧毁敌方核心"],
          ["units_built", "单位建造数"],
          ["units_lost", "单位损失数"],
          ["harvest_count", "采集次数"],
          ["deposit_count", "提交次数"],
          ["shoot_count", "射击次数"],
          ["move_failures", "移动失败"],
          ["manual_overrides", "Manual 覆盖"],
          ["observed_turns", "已观察回合"],
          ["core_events", "核心事件数"],
          ["up_time", "存活回合数"],
        ],
      },
    ];

    for (const section of sections) {
      const heading = document.createElement("div");
      heading.textContent = section.heading;
      Object.assign(heading.style, {
        fontWeight: "700",
        fontSize: "12px",
        color: "#8fbbae",
        margin: "8px 0 4px",
      });
      panel.appendChild(heading);
      for (const [key, label] of section.rows) {
        const row = document.createElement("div");
        Object.assign(row.style, {
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          minHeight: "22px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        });
        const name = document.createElement("span");
        name.textContent = label;
        name.style.color = "#aeb9c6";
        const value = document.createElement("span");
        value.style.fontFamily = "ui-monospace, SFMono-Regular, Consolas, monospace";
        value.style.color = "#eef2f7";
        value.textContent = "-";
        row.append(name, value);
        panel.appendChild(row);
        state.statusElements.set(`stats:${key}`, value);
      }
    }

    for (const [key, headingText] of [
      ["event_totals", "全部事件计数"],
      ["decision_totals", "全部策略决策计数"],
    ]) {
      const heading = document.createElement("div");
      heading.textContent = headingText;
      Object.assign(heading.style, {
        fontWeight: "700",
        fontSize: "12px",
        color: "#8fbbae",
        margin: "10px 0 4px",
      });
      const container = document.createElement("div");
      panel.append(heading, container);
      state.statsCounterContainers.set(key, container);
    }

    document.documentElement.appendChild(panel);
    state.statsPanel = panel;
    syncControls();
  }

  function createLogsPanel() {
    if (state.logsPanel || !document.documentElement) {
      return;
    }
    const panel = controlContainer("div");
    Object.assign(panel.style, {
      position: "fixed",
      display: "none",
      flexDirection: "column",
      width: "min(520px, calc(100vw - 16px))",
      maxHeight: "72vh",
      padding: "10px 12px 12px",
      border: "1px solid rgba(255,255,255,0.2)",
      borderRadius: "8px",
      background: "rgba(8,11,18,0.96)",
      color: "#d9e1eb",
      font: "12px system-ui, -apple-system, Segoe UI, sans-serif",
      boxShadow: "0 8px 28px rgba(0,0,0,0.42)",
      zIndex: "2147483001",
      pointerEvents: "auto",
      userSelect: "text",
      overflow: "hidden",
      boxSizing: "border-box",
    });

    const header = document.createElement("div");
    Object.assign(header.style, {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      marginBottom: "8px",
      flex: "0 0 auto",
    });
    const title = document.createElement("span");
    title.textContent = "中文事件日志";
    Object.assign(title.style, { fontWeight: "700", fontSize: "13px" });
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "✕";
    close.title = "关闭日志面板";
    applyButtonStyle(close);
    Object.assign(close.style, {
      height: "24px",
      lineHeight: "22px",
      padding: "0 8px",
    });
    close.addEventListener("click", () => {
      state.logsOpen = false;
      syncControls();
    });
    header.append(title, close);

    const filters = document.createElement("div");
    Object.assign(filters.style, {
      display: "grid",
      gridTemplateColumns: "105px 118px minmax(120px, 1fr)",
      gap: "6px",
      marginBottom: "8px",
      flex: "0 0 auto",
    });
    const styleFilter = (element) => {
      Object.assign(element.style, {
        minWidth: "0",
        height: "28px",
        padding: "2px 7px",
        border: "1px solid rgba(255,255,255,0.2)",
        borderRadius: "5px",
        background: "#121824",
        color: "#d9e1eb",
        font: "12px system-ui, -apple-system, Segoe UI, sans-serif",
      });
    };
    const category = document.createElement("select");
    for (const [value, label] of [
      ["", "全部分类"],
      ["战斗", "战斗"],
      ["资源", "资源"],
      ["生产", "生产"],
      ["信标", "信标"],
      ["Core", "Core"],
      ["移动", "移动"],
      ["单位", "单位"],
      ["系统", "系统"],
    ]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      category.appendChild(option);
    }
    styleFilter(category);
    const level = document.createElement("select");
    for (const [value, label] of [
      ["important", "重要事件"],
      ["all", "全部级别"],
      ["warning", "警告及以上"],
      ["danger", "仅严重"],
    ]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      level.appendChild(option);
    }
    styleFilter(level);
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "搜索事件";
    search.setAttribute("aria-label", "搜索事件日志");
    styleFilter(search);
    for (const input of [category, level, search]) {
      input.addEventListener("input", renderLogs);
      input.addEventListener("change", renderLogs);
    }
    filters.append(category, level, search);

    const list = document.createElement("div");
    Object.assign(list.style, {
      overflowY: "auto",
      minHeight: "120px",
      maxHeight: "calc(72vh - 92px)",
      overscrollBehavior: "contain",
    });
    panel.append(header, filters, list);
    document.documentElement.appendChild(panel);
    state.logsPanel = panel;
    state.logsList = list;
    state.logsCategory = category;
    state.logsLevel = level;
    state.logsSearch = search;
    renderLogs();
    syncControls();
  }

  function logTime(value) {
    if (typeof value !== "string") {
      return "";
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? ""
      : date.toLocaleTimeString("zh-CN", { hour12: false });
  }

  function renderLogs() {
    if (!state.logsList) {
      return;
    }
    const category = state.logsCategory?.value || "";
    const level = state.logsLevel?.value || "important";
    const query = (state.logsSearch?.value || "").trim().toLocaleLowerCase();
    const importantLevels = new Set(["info", "success", "warning", "danger"]);
    const warningLevels = new Set(["warning", "danger"]);
    const entries = Array.isArray(state.logs.entries)
      ? [...state.logs.entries].reverse().filter((entry) => {
          if (category && entry.category !== category) {
            return false;
          }
          if (level === "important" && !importantLevels.has(entry.level)) {
            return false;
          }
          if (level === "warning" && !warningLevels.has(entry.level)) {
            return false;
          }
          if (level === "danger" && entry.level !== "danger") {
            return false;
          }
          if (!query) {
            return true;
          }
          return [
            entry.title,
            entry.message,
            entry.category,
            entry.event_type,
            entry.reason_code,
            entry.actor,
            entry.target,
          ]
            .filter(Boolean)
            .join(" ")
            .toLocaleLowerCase()
            .includes(query);
        })
      : [];
    state.logsList.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.textContent = "暂无符合条件的事件";
      Object.assign(empty.style, { color: "#7f8996", padding: "12px 2px" });
      state.logsList.appendChild(empty);
      return;
    }
    const levelColors = {
      debug: "#7f8996",
      info: "#b9c4d2",
      success: "#8fc8a8",
      warning: "#e0b36b",
      danger: "#ee8c80",
    };
    for (const entry of entries) {
      const row = document.createElement("div");
      Object.assign(row.style, {
        padding: "7px 4px 8px",
        borderBottom: "1px solid rgba(255,255,255,0.07)",
        cursor: entry.position ? "pointer" : "default",
        overflowWrap: "anywhere",
      });
      row.title = [entry.event_type, entry.reason_code].filter(Boolean).join(" · ");
      const meta = document.createElement("div");
      Object.assign(meta.style, {
        display: "flex",
        alignItems: "center",
        gap: "7px",
        color: "#7f8996",
        font: "11px ui-monospace, SFMono-Regular, Consolas, monospace",
        marginBottom: "3px",
      });
      const badge = document.createElement("span");
      badge.textContent = entry.category;
      badge.style.color = levelColors[entry.level] || levelColors.info;
      badge.style.fontWeight = "700";
      const tick = document.createElement("span");
      tick.textContent = `Tick ${entry.tick}`;
      const time = document.createElement("span");
      time.textContent = logTime(entry.recorded_at);
      const position = document.createElement("span");
      position.textContent = entry.position ? formatPosition(entry.position) : "";
      meta.append(badge, tick, time, position);
      const title = document.createElement("div");
      title.textContent = entry.title;
      Object.assign(title.style, {
        color: levelColors[entry.level] || levelColors.info,
        fontWeight: "700",
        fontSize: "12px",
      });
      const message = document.createElement("div");
      message.textContent = entry.message;
      Object.assign(message.style, { color: "#c8d0da", marginTop: "2px" });
      row.append(meta, title, message);
      if (entry.position) {
        row.tabIndex = 0;
        const focus = () => focusTarget(entry.position, entry.title);
        row.addEventListener("click", focus);
        row.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            focus();
          }
        });
      }
      state.logsList.appendChild(row);
    }
  }

  function updateLogs(payload) {
    const next = core.normalizeLogs(payload);
    const latest = next.entries[next.entries.length - 1] || null;
    if (
      latest &&
      state.lastLogEventId &&
      latest.event_id !== state.lastLogEventId &&
      !state.logsOpen
    ) {
      const previousIndex = next.entries.findIndex(
        (entry) => entry.event_id === state.lastLogEventId,
      );
      state.unreadLogs += previousIndex >= 0
        ? next.entries.length - previousIndex - 1
        : 1;
      state.unreadLogs = Math.min(state.unreadLogs, 99);
    }
    if (latest) {
      state.lastLogEventId = latest.event_id;
    }
    state.logs = next;
    if (state.logsOpen) {
      state.unreadLogs = 0;
      renderLogs();
    }
    syncControls();
  }

  function renderStatusBar() {
    const stats = state.stats;
    const setText = (key, text) => {
      const element = state.statusElements.get(key);
      if (element) {
        element.textContent = text;
      }
    };
    if (!stats) {
      setText("bar:tick", "Tick -");
      setText("bar:resources", "资源 -");
      setText("bar:population", "人口 -");
      setText("bar:enemies", "敌 -");
      setText("bar:core", "HP -");
      setText("bar:beacon", "信标 -");
      return;
    }
    const mode = MODE_LABELS[stats.mode] || "发育";
    setText("bar:tick", `Tick ${stats.tick}`);
    setText("bar:resources", `资源 ${stats.resources}/${stats.capacity}`);
    setText(
      "bar:population",
      `人口 ${stats.workers}/${stats.vanguards}/${stats.rangers}`,
    );
    setText(
      "bar:enemies",
      `敌 ${stats.visible_enemies}${stats.core_reinforcement_active ? " · 回援" : ""}`,
    );
    setText("bar:core", `HP ${stats.core_hp}/${stats.core_shield}`);
    setText("bar:beacon", stats.owns_beacon ? "信标✓" : "信标✗");

    const pairs = {
      "stats:tick": String(stats.tick),
      "stats:mode": mode,
      "stats:recall": state.control.recall ? "已召回" : "正常",
      "stats:raid_enabled": stats.raid_enabled ? "开启" : "关闭",
      "stats:raid_recall": stats.raid_recall ? "召回中" : "正常",
      "stats:raid_selected": `${stats.raid_selected_vanguards ?? 0}/${stats.raid_selected_rangers ?? 0}`,
      "stats:raid_target": formatPosition(stats.raid_core_position),
      "stats:raid_sweep_radius": String(stats.raid_sweep_radius ?? 18),
      "stats:migration_candidate": formatPosition(stats.migration_candidate),
      "stats:migration_target": formatPosition(stats.migration_target),
      "stats:migration_site_checked": stats.migration_site_checked ? "是" : "否",
      "stats:migration_site_score": `${stats.migration_site_score}/9`,
      "stats:resources": `${stats.resources}/${stats.capacity}`,
      "stats:population": `${stats.workers}/${stats.vanguards}/${stats.rangers}`,
      "stats:core": `${stats.core_hp}/${stats.core_shield}`,
      "stats:core_position": formatPosition(stats.core_position),
      "stats:beacon_position": formatPosition(stats.beacon_position),
      "stats:visible_enemies": String(stats.visible_enemies),
      "stats:core_threat_count": String(stats.core_threat_count),
      "stats:core_reinforcement_active": stats.core_reinforcement_active
        ? "进行中"
        : "未触发",
      "stats:owns_beacon": stats.owns_beacon ? "是" : "否",
      "stats:visible_resource_cells": String(stats.visible_resource_cells),
      "stats:known_resource_cells": String(stats.known_resource_cells),
      "stats:browser_resource_hints": String(stats.browser_resource_hints),
      "stats:browser_intel_online": stats.browser_intel_online ? "在线" : "离线",
      "stats:browser_intel_age_seconds": stats.browser_intel_online
        ? `${stats.browser_intel_age_seconds} 秒`
        : "-",
      "stats:worker_cargo": String(stats.worker_cargo),
      "stats:exploring_workers": String(stats.exploring_workers),
      "stats:max_worker_search_radius": String(stats.max_worker_search_radius),
      "stats:active_routes": `${stats.active_routes}/${stats.complete_routes}`,
      "stats:tick_interval": `${stats.tick_interval} tick`,
      "stats:total_resources_harvested": String(stats.total_resources_harvested),
      "stats:total_resources_deposited": String(stats.total_resources_deposited),
      "stats:total_resources_captured": String(stats.total_resources_captured),
      "stats:enemy_cores_destroyed": String(stats.enemy_cores_destroyed),
      "stats:units_built": String(stats.units_built),
      "stats:units_lost": String(stats.units_lost),
      "stats:harvest_count": String(stats.harvest_count),
      "stats:deposit_count": String(stats.deposit_count),
      "stats:shoot_count": String(stats.shoot_count),
      "stats:move_failures": String(stats.move_failures),
      "stats:manual_overrides": String(stats.manual_overrides),
      "stats:observed_turns": String(stats.observed_turns),
      "stats:core_events": String(stats.core_events),
      "stats:up_time": String(stats.up_time),
    };
    for (const [key, text] of Object.entries(pairs)) {
      const element = state.statusElements.get(key);
      if (element) {
        element.textContent = text;
      }
    }
    renderCounterStats("event_totals", stats.event_totals);
    renderCounterStats("decision_totals", stats.decision_totals);
  }

  function formatPosition(value) {
    return Array.isArray(value) && value.length === 2
      ? `[${value[0]}, ${value[1]}]`
      : "-";
  }

  function renderCounterStats(key, values) {
    const container = state.statsCounterContainers.get(key);
    if (!container) {
      return;
    }
    container.replaceChildren();
    const entries = values && typeof values === "object"
      ? Object.entries(values).sort(([left], [right]) => left.localeCompare(right))
      : [];
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.textContent = "暂无数据";
      empty.style.color = "#7f8996";
      empty.style.padding = "4px 0";
      container.appendChild(empty);
      return;
    }
    for (const [label, count] of entries) {
      const row = document.createElement("div");
      Object.assign(row.style, {
        display: "flex",
        justifyContent: "space-between",
        gap: "12px",
        minHeight: "21px",
        borderBottom: "1px solid rgba(255,255,255,0.05)",
      });
      const name = document.createElement("span");
      name.textContent = label;
      name.style.color = "#aeb9c6";
      name.style.overflowWrap = "anywhere";
      const value = document.createElement("span");
      value.textContent = String(count);
      value.style.fontFamily = "ui-monospace, SFMono-Regular, Consolas, monospace";
      value.style.color = "#eef2f7";
      row.append(name, value);
      container.appendChild(row);
    }
  }

  function updateControl(payload) {
    window.postMessage({ channel: CHANNEL, kind: "control:update", payload }, "*");
  }

  function updateSettings(update) {
    state.settings = core.normalizeSettings({ ...state.settings, ...update });
    persistSettings();
    syncControls();
  }

  function persistSettings() {
    window.postMessage(
      { channel: CHANNEL, kind: "settings:update", payload: state.settings },
      "*",
    );
  }

  function toggleRoutes() {
    updateSettings({ showRoutes: !state.settings.showRoutes });
  }

  function syncControls() {
    if (state.routeToggle) {
      state.routeToggle.textContent = state.settings.showRoutes ? "路线 开" : "路线 关";
      state.routeToggle.style.color = state.settings.showRoutes ? "#9bcbbd" : "#8f9cad";
    }
    if (state.settingsPanel) {
      state.settingsPanel.style.display = state.settingsOpen ? "block" : "none";
    }
    if (state.settingsButton) {
      state.settingsButton.style.color = state.settingsOpen ? "#9bcbbd" : "#d9e1eb";
    }
    if (state.modeButton) {
      const mode = state.control.mode;
      state.modeButton.textContent =
        mode === "beacon"
          ? "🚩 抢信标"
          : mode === "aggress"
            ? "⚔ 侵略"
            : "🌱 发育";
      state.modeButton.style.color =
        mode === "beacon" ? "#c8a3e8" : mode === "aggress" ? "#d98a7a" : "#9bcbbd";
    }
    if (state.recallButton) {
      const recall = state.control.recall;
      state.recallButton.textContent = recall ? "解除召回" : "一键召回";
      state.recallButton.style.color = recall ? "#d98a7a" : "#9bcbbd";
    }
    if (state.raidButton) {
      const enabled = Boolean(state.control.raid_enabled);
      state.raidButton.textContent = enabled ? "偷袭 开" : "偷袭 关";
      state.raidButton.style.color = enabled ? "#e0b36b" : "#8f9cad";
    }
    if (state.raidRecallButton) {
      const recall = Boolean(state.control.raid_recall);
      state.raidRecallButton.textContent = recall ? "偷袭解除召回" : "偷袭召回";
      state.raidRecallButton.style.color = recall ? "#d98a7a" : "#9bcbbd";
    }
    if (state.settingInputs.has("beacon_target_distance")) {
      const entry = state.settingInputs.get("beacon_target_distance");
      entry.input.value = String(state.control.beacon_target_distance ?? 0);
    }
    for (const key of ["raid_vanguards", "raid_rangers"]) {
      const entry = state.settingInputs.get(key);
      if (entry) {
        entry.input.value = String(state.control[key] ?? (key === "raid_vanguards" ? 1 : 2));
      }
    }
    if (state.statsPanel) {
      state.statsPanel.style.display = state.statsOpen ? "block" : "none";
    }
    if (state.statsButton) {
      state.statsButton.style.color = state.statsOpen ? "#9bcbbd" : "#d9e1eb";
    }
    if (state.locatorButton) {
      state.locatorButton.style.color = state.locatorOpen ? "#9bcbbd" : "#d9e1eb";
    }
    if (state.logsButton) {
      state.logsButton.textContent = state.unreadLogs
        ? `日志 ${state.unreadLogs}`
        : "日志";
      state.logsButton.style.color = state.logsOpen
        ? "#9bcbbd"
        : state.unreadLogs
          ? "#e0b36b"
          : "#d9e1eb";
    }
    if (state.logsPanel) {
      state.logsPanel.style.display = state.logsOpen ? "flex" : "none";
    }
    for (const [key, binding] of state.settingInputs) {
      if (binding.kind === "number") {
        continue;
      }
      const value = state.settings[key];
      if (binding.kind === "checkbox") {
        binding.input.checked = Boolean(value);
      } else {
        binding.input.value = String(value);
      }
      if (binding.kind === "range") {
        binding.value.textContent = `${Number(value).toFixed(key === "opacity" ? 2 : 1)}${binding.suffix}`;
      }
    }
  }

  function setControlsVisible(visible) {
    if (state.toolbar) {
      state.toolbar.style.display = visible ? "flex" : "none";
    }
    if (state.settingsPanel) {
      state.settingsPanel.style.display = visible && state.settingsOpen ? "block" : "none";
    }
    if (state.statusBar) {
      state.statusBar.style.display = visible ? "flex" : "none";
    }
    if (state.statsPanel) {
      state.statsPanel.style.display = visible && state.statsOpen ? "block" : "none";
    }
    if (state.locatorPanel) {
      state.locatorPanel.style.display = visible && state.locatorOpen ? "block" : "none";
    }
    if (state.logsPanel) {
      state.logsPanel.style.display = visible && state.logsOpen ? "flex" : "none";
    }
  }

  function positionControls(rect) {
    if (!state.toolbar || !state.settingsPanel || !state.statusBar) {
      return;
    }
    const initial = core.calculateControlLayout(rect, 0, window.innerHeight);
    if (!initial) {
      return;
    }
    state.statusBar.style.left = `${initial.dock.left}px`;
    state.statusBar.style.top = `${initial.dock.top}px`;
    state.statusBar.style.width = `${initial.dock.width}px`;

    const dockHeight = Math.ceil(state.statusBar.getBoundingClientRect().height);
    const layout = core.calculateControlLayout(
      rect,
      dockHeight,
      window.innerHeight,
    );
    if (!layout) {
      return;
    }
    state.statusBar.style.left = `${layout.dock.left}px`;
    state.statusBar.style.top = `${layout.dock.top}px`;
    state.statusBar.style.width = `${layout.dock.width}px`;

    const placePanel = (panel, placement) => {
      if (!panel) {
        return;
      }
      panel.style.left = `${placement.left}px`;
      panel.style.top = `${placement.top}px`;
      panel.style.width = `${placement.width}px`;
      panel.style.maxHeight = `${placement.maxHeight}px`;
    };
    placePanel(state.settingsPanel, layout.settings);
    placePanel(state.statsPanel, layout.stats);
    placePanel(state.locatorPanel, layout.locator);
    placePanel(state.logsPanel, layout.logs);
  }

  function findMapCanvas(now) {
    if (
      state.mapCanvas &&
      state.mapCanvas.isConnected &&
      !state.mapCanvas.hasAttribute(OVERLAY_ATTRIBUTE)
    ) {
      const rect = state.mapCanvas.getBoundingClientRect();
      if (rect.width >= 300 && rect.height >= 220) {
        bindMapClick(state.mapCanvas);
        return state.mapCanvas;
      }
    }
    if (now - state.lastCanvasSearch < 500) {
      return null;
    }
    state.lastCanvasSearch = now;
    let best = null;
    let bestScore = 0;
    for (const canvas of document.querySelectorAll("canvas")) {
      if (canvas.hasAttribute(OVERLAY_ATTRIBUTE)) {
        continue;
      }
      const rect = canvas.getBoundingClientRect();
      if (rect.width < 300 || rect.height < 220) {
        continue;
      }
      const style = getComputedStyle(canvas);
      if (style.display === "none" || style.visibility === "hidden") {
        continue;
      }
      const hint = `${canvas.id} ${canvas.className} ${canvas.parentElement?.className || ""}`.toLowerCase();
      const score = rect.width * rect.height + (hint.includes("arena") || hint.includes("map") ? 1e9 : 0);
      if (score > bestScore) {
        best = canvas;
        bestScore = score;
      }
    }
    if (state.clickMapCanvas && state.clickMapCanvas !== best && state.mapClickHandler) {
      state.clickMapCanvas.removeEventListener(
        "click",
        state.mapClickHandler,
        true,
      );
      state.clickMapCanvas = null;
    }
    state.mapCanvas = best;
    state.camera = null;
    if (best) {
      bindMapClick(best);
    }
    return best;
  }

  function selectMapCell(event, mapCanvas) {
    if (event.button !== 0 || !arenaPageVisible() || !state.camera) {
      return;
    }
    const rect = mapCanvas.getBoundingClientRect();
    const localX = event.clientX - rect.left;
    const localY = event.clientY - rect.top;
    if (localX < 0 || localY < 0 || localX >= rect.width || localY >= rect.height) {
      return;
    }
    const cell = core.screenToGrid(
      localX,
      localY,
      state.camera,
      rect.width,
      rect.height,
    );
    if (cell) {
      state.selectedCell = cell;
    }
  }

  function bindPageClick() {
    if (state.pageClickHandler) {
      return;
    }
    state.pageClickHandler = (event) => {
      if (!arenaPageVisible() || event.button !== 0) {
        return;
      }
      const target = event.target;
      if (target instanceof Element && target.closest(`[${OVERLAY_ATTRIBUTE}]`)) {
        return;
      }
      const mapCanvas = state.mapCanvas && state.mapCanvas.isConnected
        ? state.mapCanvas
        : findMapCanvas(performance.now());
      if (!mapCanvas || !state.camera) {
        return;
      }
      const rect = mapCanvas.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX >= rect.right ||
        event.clientY < rect.top ||
        event.clientY >= rect.bottom
      ) {
        return;
      }
      selectMapCell(event, mapCanvas);
    };
    window.addEventListener("pointerdown", state.pageClickHandler, true);
  }

  function bindMapClick(mapCanvas) {
    if (state.clickMapCanvas === mapCanvas) {
      return;
    }
    if (state.clickMapCanvas && state.mapClickHandler) {
      state.clickMapCanvas.removeEventListener(
        "click",
        state.mapClickHandler,
        true,
      );
    }
    state.mapClickHandler = (event) => selectMapCell(event, mapCanvas);
    mapCanvas.addEventListener("click", state.mapClickHandler, true);
    state.clickMapCanvas = mapCanvas;
  }

  function resizeOverlay(rect) {
    const overlay = state.overlay;
    const context = state.context;
    if (!overlay || !context) {
      return false;
    }
    const dpr = Math.max(1, Math.min(3, devicePixelRatio || 1));
    const pixelWidth = Math.max(1, Math.round(rect.width * dpr));
    const pixelHeight = Math.max(1, Math.round(rect.height * dpr));
    if (overlay.width !== pixelWidth || overlay.height !== pixelHeight) {
      overlay.width = pixelWidth;
      overlay.height = pixelHeight;
    }
    overlay.style.left = `${rect.left}px`;
    overlay.style.top = `${rect.top}px`;
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;
    overlay.style.display = "block";
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);
    return true;
  }

  function routeColor(objectType) {
    switch (objectType) {
      case "WORKER":
        return state.settings.workerColor;
      case "VANGUARD":
        return state.settings.vanguardColor;
      case "RANGER":
        return state.settings.rangerColor;
      default:
        return "#9ca7b5";
    }
  }

  function typeLabel(objectType) {
    switch (objectType) {
      case "WORKER":
        return "工";
      case "VANGUARD":
        return "先";
      case "RANGER":
        return "游";
      default:
        return "兵";
    }
  }

  function unitNumber(route) {
    if (Number.isInteger(route.number) && route.number > 0) {
      return route.number;
    }
    const units = Array.isArray(state.payload.units) ? state.payload.units : [];
    const unit = units.find((candidate) => candidate.object_id === route.object_id);
    return unit && Number.isInteger(unit.number) ? unit.number : null;
  }

  function routeIdentifier(route) {
    const number = unitNumber(route);
    return number ? `${typeLabel(route.object_type)}#${number}` : typeLabel(route.object_type);
  }

  function screenPoint(position, width, height) {
    return core.gridToScreen(position, state.camera, width, height);
  }

  function observeTickTiming(stats) {
    const tick = Number(stats && stats.tick);
    const now = performance.now();
    if (
      !Number.isInteger(tick) ||
      tick < 0 ||
      state.tickTiming.lastTick === null ||
      tick <= state.tickTiming.lastTick ||
      !state.tickTiming.lastAt
    ) {
      if (Number.isInteger(tick) && tick >= 0) {
        state.tickTiming.lastTick = tick;
        state.tickTiming.lastAt = now;
      }
      return;
    }
    const tickDelta = tick - state.tickTiming.lastTick;
    const seconds = (now - state.tickTiming.lastAt) / 1000;
    const sample = seconds / tickDelta;
    if (Number.isFinite(sample) && sample >= 0.5 && sample <= 30) {
      state.tickTiming.secondsPerTick =
        state.tickTiming.secondsPerTick * 0.75 + sample * 0.25;
    }
    state.tickTiming.lastTick = tick;
    state.tickTiming.lastAt = now;
  }

  function selectedDistance() {
    const corePosition = core.normalizePosition(state.stats?.core_position);
    if (!state.selectedCell || !corePosition) {
      return null;
    }
    return core.gridDistance(state.selectedCell, corePosition);
  }

  function formatTravelMinutes(minutes) {
    if (!Number.isFinite(minutes)) {
      return "无法估算";
    }
    if (minutes < 0.1) {
      return "<0.1 分钟";
    }
    return `${minutes < 10 ? minutes.toFixed(1) : Math.round(minutes)} 分钟`;
  }

  function pointOnCanvas(point, width, height, margin = 40) {
    return (
      point &&
      point.x >= -margin &&
      point.y >= -margin &&
      point.x <= width + margin &&
      point.y <= height + margin
    );
  }

  function drawArrow(context, start, end, color, size) {
    const angle = Math.atan2(end.y - start.y, end.x - start.x);
    context.save();
    context.fillStyle = color;
    context.beginPath();
    context.moveTo(end.x, end.y);
    context.lineTo(
      end.x - Math.cos(angle - Math.PI / 6) * size,
      end.y - Math.sin(angle - Math.PI / 6) * size,
    );
    context.lineTo(
      end.x - Math.cos(angle + Math.PI / 6) * size,
      end.y - Math.sin(angle + Math.PI / 6) * size,
    );
    context.closePath();
    context.fill();
    context.restore();
  }

  function drawGoal(context, route, width, height, color) {
    const goal = core.normalizePosition(route.goal);
    if (!goal) {
      return;
    }
    const point = screenPoint(goal, width, height);
    if (!pointOnCanvas(point, width, height, 100)) {
      return;
    }
    const radius = Math.max(3.5, Math.min(7, state.camera.cell * 0.22));
    context.save();
    context.globalAlpha = Math.min(0.78, state.settings.opacity + 0.18);
    context.strokeStyle = color;
    context.fillStyle = "#0a0e16";
    context.lineWidth = Math.max(1, state.settings.lineWidth);
    context.setLineDash(route.complete ? [] : [3, 3]);
    context.beginPath();
    context.moveTo(point.x, point.y - radius);
    context.lineTo(point.x + radius, point.y);
    context.lineTo(point.x, point.y + radius);
    context.lineTo(point.x - radius, point.y);
    context.closePath();
    context.fill();
    context.stroke();
    context.setLineDash([]);
    context.font = "600 10px ui-monospace, SFMono-Regular, Consolas, monospace";
    const label = `${routeIdentifier(route)} [${goal[0]}, ${goal[1]}]`;
    const labelWidth = context.measureText(label).width;
    context.globalAlpha = 0.82;
    context.fillStyle = "#080b12";
    context.fillRect(point.x + radius + 3, point.y - 9, labelWidth + 7, 16);
    context.fillStyle = color;
    context.fillText(label, point.x + radius + 6, point.y + 2);
    context.restore();
  }

  function drawRoute(context, route, width, height) {
    const turns = core.pathTurnPoints(route.path);
    if (turns.length < 2) {
      drawGoal(context, route, width, height, routeColor(route.object_type));
      return;
    }
    const points = turns
      .map((position) => screenPoint(position, width, height))
      .filter((point) => point && Math.abs(point.x) < 1e7 && Math.abs(point.y) < 1e7);
    if (points.length < 2) {
      return;
    }
    const color = routeColor(route.object_type);
    const lineWidth = state.settings.lineWidth;
    context.save();
    context.lineCap = "round";
    context.lineJoin = "round";
    context.globalAlpha = state.settings.opacity * 0.45;
    context.strokeStyle = "#050810";
    context.lineWidth = lineWidth + 1.5;
    context.setLineDash([]);
    context.beginPath();
    context.moveTo(points[0].x, points[0].y);
    for (const point of points.slice(1)) {
      context.lineTo(point.x, point.y);
    }
    context.stroke();

    context.globalAlpha = state.settings.opacity;
    context.strokeStyle = color;
    context.lineWidth = lineWidth;
    context.setLineDash([7, 5]);
    context.beginPath();
    context.moveTo(points[0].x, points[0].y);
    for (const point of points.slice(1)) {
      context.lineTo(point.x, point.y);
    }
    context.stroke();
    context.setLineDash([]);

    const arrowSize = Math.max(3, Math.min(8, lineWidth * 2.4 + 2));
    const firstCell = core.normalizePosition(route.path[1]);
    const firstPoint = firstCell && screenPoint(firstCell, width, height);
    if (firstPoint) {
      context.lineWidth = lineWidth + 0.5;
      context.beginPath();
      context.moveTo(points[0].x, points[0].y);
      context.lineTo(firstPoint.x, firstPoint.y);
      context.stroke();
      drawArrow(context, points[0], firstPoint, color, arrowSize);
    }
    drawArrow(
      context,
      points[points.length - 2],
      points[points.length - 1],
      color,
      arrowSize,
    );
    context.restore();
    drawGoal(context, route, width, height, color);
  }

  function drawResources(context, width, height) {
    if (!state.settings.showResources) {
      return;
    }
    const resources = Array.isArray(state.payload.resources) ? state.payload.resources : [];
    const cell = state.camera.cell;
    for (const resource of resources) {
      const point = screenPoint(resource, width, height);
      if (!pointOnCanvas(point, width, height, cell)) {
        continue;
      }
      const half = Math.max(3, Math.min(10, cell * 0.34));
      const dot = Math.max(2, Math.min(5, cell * 0.16));
      context.save();
      context.globalAlpha = Math.min(0.7, state.settings.opacity + 0.16);
      context.strokeStyle = state.settings.resourceColor;
      context.lineWidth = Math.max(1, state.settings.lineWidth * 0.8);
      context.setLineDash([3, 3]);
      context.strokeRect(point.x - half, point.y - half, half * 2, half * 2);
      context.setLineDash([]);
      context.globalAlpha = Math.min(0.58, state.settings.opacity + 0.08);
      context.fillStyle = state.settings.resourceColor;
      context.beginPath();
      context.arc(point.x, point.y, dot, 0, Math.PI * 2);
      context.fill();
      context.restore();
    }
  }

  function drawBrowserResources(context, width, height) {
    if (!state.settings.showResources) {
      return;
    }
    const hints = Array.isArray(state.browserIntel.resources)
      ? state.browserIntel.resources
      : [];
    const confirmed = new Set(
      (Array.isArray(state.payload.resources) ? state.payload.resources : [])
        .map((position) => `${position[0]},${position[1]}`),
    );
    const cell = state.camera.cell;
    for (const resource of hints) {
      if (!Array.isArray(resource) || confirmed.has(`${resource[0]},${resource[1]}`)) {
        continue;
      }
      const point = screenPoint(resource, width, height);
      if (!pointOnCanvas(point, width, height, cell)) {
        continue;
      }
      const half = Math.max(3, Math.min(9, cell * 0.3));
      context.save();
      context.globalAlpha = Math.min(0.48, state.settings.opacity * 0.85);
      context.strokeStyle = "#e09b55";
      context.lineWidth = Math.max(1, state.settings.lineWidth * 0.7);
      context.setLineDash([2, 4]);
      context.strokeRect(point.x - half, point.y - half, half * 2, half * 2);
      context.setLineDash([]);
      context.fillStyle = "#e09b55";
      context.beginPath();
      context.arc(point.x, point.y, Math.max(1.5, Math.min(3.5, cell * 0.11)), 0, Math.PI * 2);
      context.fill();
      context.font = "600 9px ui-monospace, SFMono-Regular, Consolas, monospace";
      context.fillStyle = "rgba(224,155,85,0.9)";
      context.fillText(`[${resource[0]}, ${resource[1]}]`, point.x + half + 3, point.y - half - 2);
      context.restore();
    }
  }

  function captureBrowserResources(now, mapCanvas) {
    if (now - state.lastBrowserIntelCapture < 2000 || !mapCanvas) {
      return;
    }
    state.lastBrowserIntelCapture = now;
    const resources = core.findResourceCells(mapCanvas);
    if (!resources.length) {
      state.browserEmptyCaptures += 1;
      if (state.browserEmptyCaptures < 3) {
        return;
      }
    } else {
      state.browserEmptyCaptures = 0;
    }
    window.postMessage({
      channel: CHANNEL,
      kind: "browser-intel",
      payload: {
        version: 1,
        source: "browser",
        captured_at: new Date().toISOString(),
        resources,
      },
    }, "*");
  }

  function drawUnitLabels(context, width, height) {
    if (!state.settings.showUnitLabels) {
      return;
    }
    const units = Array.isArray(state.payload.units) ? state.payload.units : [];
    for (const unit of units) {
      if (!Number.isInteger(unit.number) || unit.number < 1) {
        continue;
      }
      const point = screenPoint(unit.position, width, height);
      if (!pointOnCanvas(point, width, height, 30)) {
        continue;
      }
      const label = `${typeLabel(unit.object_type)}#${unit.number}`;
      const color = routeColor(unit.object_type);
      context.save();
      context.font = "700 10px ui-monospace, SFMono-Regular, Consolas, monospace";
      const labelWidth = context.measureText(label).width + 7;
      const x = point.x + Math.max(4, Math.min(9, state.camera.cell * 0.24));
      const y = point.y - Math.max(8, Math.min(13, state.camera.cell * 0.34));
      context.globalAlpha = 0.86;
      context.fillStyle = "#080b12";
      context.strokeStyle = color;
      context.lineWidth = 1;
      context.fillRect(x, y, labelWidth, 15);
      context.strokeRect(x, y, labelWidth, 15);
      context.fillStyle = color;
      context.fillText(label, x + 3.5, y + 11);
      context.restore();
    }
  }

  function hoverCell(rect) {
    if (!state.pointer || state.pointerOverControls) {
      return null;
    }
    const localX = state.pointer.x - rect.left;
    const localY = state.pointer.y - rect.top;
    if (localX < 0 || localY < 0 || localX >= rect.width || localY >= rect.height) {
      return null;
    }
    return core.screenToGrid(localX, localY, state.camera, rect.width, rect.height);
  }

  function drawHover(context, rect, cell) {
    if (!cell) {
      return;
    }
    const center = screenPoint(cell, rect.width, rect.height);
    if (!center) {
      return;
    }
    const size = state.camera.cell;
    context.save();
    context.fillStyle = "rgba(255, 255, 255, 0.05)";
    context.strokeStyle = "rgba(255, 255, 255, 0.72)";
    context.lineWidth = 1;
    context.setLineDash([4, 3]);
    context.fillRect(center.x - size / 2, center.y - size / 2, size, size);
    context.strokeRect(center.x - size / 2, center.y - size / 2, size, size);
    context.setLineDash([]);

    const label = `[${cell[0]}, ${cell[1]}]`;
    context.font = "600 12px ui-monospace, SFMono-Regular, Consolas, monospace";
    const labelWidth = context.measureText(label).width + 12;
    let x = state.pointer.x - rect.left + 14;
    let y = state.pointer.y - rect.top - 28;
    if (x + labelWidth > rect.width - 4) {
      x = state.pointer.x - rect.left - labelWidth - 14;
    }
    if (y < 4) {
      y = state.pointer.y - rect.top + 14;
    }
    context.fillStyle = "rgba(7, 10, 16, 0.9)";
    context.fillRect(x, y, labelWidth, 22);
    context.strokeStyle = "rgba(255, 255, 255, 0.25)";
    context.strokeRect(x, y, labelWidth, 22);
    context.fillStyle = "#eef2f7";
    context.fillText(label, x + 6, y + 15);
    context.restore();
  }

  function drawSelection(context, rect) {
    if (!state.selectedCell) {
      return;
    }
    const center = screenPoint(state.selectedCell, rect.width, rect.height);
    const size = Math.max(8, state.camera.cell);
    if (center && pointOnCanvas(center, rect.width, rect.height, size)) {
      context.save();
      context.fillStyle = "rgba(240, 185, 60, 0.14)";
      context.strokeStyle = "rgba(240, 185, 60, 0.95)";
      context.lineWidth = 2;
      context.setLineDash([5, 3]);
      context.fillRect(center.x - size / 2, center.y - size / 2, size, size);
      context.strokeRect(center.x - size / 2, center.y - size / 2, size, size);
      context.setLineDash([]);
      context.restore();
    }

    const distance = selectedDistance();
    const eta = core.estimateTravelMinutes(
      distance,
      state.tickTiming.secondsPerTick,
    );
    const lines = [
      `选中 [${state.selectedCell[0]}, ${state.selectedCell[1]}]`,
      distance === null
        ? "Core 坐标暂不可用"
        : `距 Core ${distance} 格 · 预计 ${formatTravelMinutes(eta)}`,
    ];
    context.save();
    context.font = "600 12px system-ui, -apple-system, Segoe UI, sans-serif";
    const width = Math.max(...lines.map((line) => context.measureText(line).width)) + 22;
    const boxWidth = Math.min(rect.width - 20, Math.max(210, width));
    const x = Math.max(10, rect.width - boxWidth - 10);
    const y = Math.max(10, rect.height - 58);
    context.fillStyle = "rgba(7, 10, 16, 0.9)";
    context.fillRect(x, y, boxWidth, 48);
    context.strokeStyle = "rgba(240, 185, 60, 0.72)";
    context.lineWidth = 1;
    context.strokeRect(x, y, boxWidth, 48);
    context.fillStyle = "#f0b93c";
    context.fillText(lines[0], x + 11, y + 19);
    context.fillStyle = "#eef2f7";
    context.fillText(lines[1], x + 11, y + 38);
    context.restore();
  }

  function renderDistanceCard(rect) {
    const card = state.distanceCard;
    if (!card || !state.distanceCardPosition || !state.distanceCardDetail) {
      return;
    }
    if (!state.selectedCell) {
      card.style.display = "none";
      return;
    }
    const distance = selectedDistance();
    const eta = core.estimateTravelMinutes(
      distance,
      state.tickTiming.secondsPerTick,
    );
    state.distanceCardPosition.textContent =
      `选中 [${state.selectedCell[0]}, ${state.selectedCell[1]}]`;
    state.distanceCardDetail.textContent = distance === null
      ? "Core 坐标暂不可用"
      : `距 Core ${distance} 格 · 预计 ${formatTravelMinutes(eta)}`;
    const width = Math.min(290, Math.max(210, rect.width - 20));
    card.style.width = `${width}px`;
    card.style.setProperty("display", "block", "important");
    card.style.setProperty("visibility", "visible", "important");
    const height = Math.ceil(card.getBoundingClientRect().height);
    card.style.left = `${Math.max(8, rect.right - width - 10)}px`;
    card.style.top = `${Math.max(8, Math.min(window.innerHeight - height - 8, rect.bottom - height - 10))}px`;
  }

  function drawHud(context, rect, hover) {
    const routes = Array.isArray(state.payload.routes) ? state.payload.routes : [];
    const resources = Array.isArray(state.payload.resources) ? state.payload.resources : [];
    const units = Array.isArray(state.payload.units) ? state.payload.units : [];
    const complete = routes.filter((route) => route && route.complete).length;
    const routeStatus = state.settings.showRoutes
      ? `${routes.length} 条路线 · ${complete} 条完整 A*`
      : "路线已隐藏 · Alt+Shift+R";
    const status = state.serviceOnline
      ? `资源 ${resources.length} · 编号单位 ${units.length}`
      : "等待本地路线服务";
    const lines = [
      `Agent 叠加层  Tick ${Number(state.payload.tick) || 0}`,
      routeStatus,
      hover ? `格子 [${hover[0]}, ${hover[1]}]` : status,
    ];
    context.save();
    context.font = "600 12px system-ui, -apple-system, Segoe UI, sans-serif";
    const width = Math.max(...lines.map((line) => context.measureText(line).width)) + 22;
    const x = Math.max(8, rect.width - width - 10);
    const y = 10;
    context.fillStyle = "rgba(7, 10, 16, 0.8)";
    context.fillRect(x, y, width, 62);
    context.strokeStyle = state.serviceOnline ? "rgba(79,159,138,0.58)" : "rgba(189,135,84,0.58)";
    context.lineWidth = 1;
    context.strokeRect(x, y, width, 62);
    context.fillStyle = "#e8edf3";
    context.fillText(lines[0], x + 11, y + 18);
    context.fillStyle = "#aeb9c6";
    context.fillText(lines[1], x + 11, y + 37);
    context.fillStyle = hover ? "#eef2f7" : state.serviceOnline ? "#8fbbae" : "#c29a6d";
    context.fillText(lines[2], x + 11, y + 55);
    context.restore();
  }

  function drawRally(context, rect) {
    const rally = state.control.rally_point;
    if (!Array.isArray(rally) || rally.length !== 2) {
      return;
    }
    const center = screenPoint(rally, rect.width, rect.height);
    if (!center) {
      return;
    }
    const size = state.camera.cell;
    context.save();
    // 标记点
    context.fillStyle = "rgba(240, 185, 60, 0.85)";
    context.strokeStyle = "rgba(20, 16, 4, 0.9)";
    context.lineWidth = 2;
    context.beginPath();
    context.arc(center.x, center.y, Math.max(5, size * 0.32), 0, Math.PI * 2);
    context.fill();
    context.stroke();
    // 旗帜杆 + 旗帜
    const flagX = center.x + Math.max(4, size * 0.28);
    const flagY = center.y - Math.max(8, size * 0.5);
    context.strokeStyle = "rgba(240, 185, 60, 0.9)";
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(center.x, center.y);
    context.lineTo(center.x, flagY);
    context.stroke();
    context.fillStyle = "rgba(240, 185, 60, 0.9)";
    context.beginPath();
    context.moveTo(center.x, flagY);
    context.lineTo(flagX, flagY + Math.max(4, size * 0.22));
    context.lineTo(center.x, flagY + Math.max(8, size * 0.44));
    context.closePath();
    context.fill();
    context.restore();
  }

  function drawLocator(context, rect) {
    // 追踪目标：正在追踪的单位 或 临时标记
    let position = null;
    let label = "";
    if (state.followUnit) {
      const stats = state.stats || {};
      const units = Array.isArray(stats.units) ? stats.units : [];
      const unit = units.find((entry) => entry.id === state.followUnit);
      if (unit && Array.isArray(unit.position)) {
        position = unit.position;
        label = `${unit.type[0]}${unit.number ?? ""}`;
      }
    } else if (state.focusMarker && state.focusMarker.until > performance.now()) {
      position = state.focusMarker.position;
      label = state.focusMarker.label;
    }
    if (!position) {
      return;
    }
    const center = screenPoint(position, rect.width, rect.height);
    if (!center) {
      return;
    }
    const size = state.camera.cell;
    context.save();
    // 屏幕内：金色圈 + 标签
    if (
      center.x >= -20 &&
      center.y >= -20 &&
      center.x <= rect.width + 20 &&
      center.y <= rect.height + 20
    ) {
      const pulse = 1 + Math.sin(performance.now() / 200) * 0.12;
      context.strokeStyle = "rgba(240, 185, 60, 0.95)";
      context.fillStyle = "rgba(240, 185, 60, 0.15)";
      context.lineWidth = 2.5;
      context.beginPath();
      context.arc(center.x, center.y, Math.max(10, size * 0.7) * pulse, 0, Math.PI * 2);
      context.fill();
      context.stroke();
      context.fillStyle = "#f0b93c";
      context.font = "600 12px ui-monospace, Consolas, monospace";
      const labelWidth = context.measureText(label).width + 10;
      context.fillStyle = "rgba(7,10,16,0.9)";
      context.fillRect(center.x - labelWidth / 2, center.y - size - 22, labelWidth, 18);
      context.fillStyle = "#f0b93c";
      context.fillText(label, center.x - labelWidth / 2 + 5, center.y - size - 8);
    } else {
      // 屏幕外：边缘方向箭头 + 距离
      const cx = rect.width / 2;
      const cy = rect.height / 2;
      const dx = center.x - cx;
      const dy = center.y - cy;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const margin = 46;
      const bx = cx + (dx / dist) * (Math.min(rect.width, rect.height) / 2 - margin);
      const by = cy + (dy / dist) * (Math.min(rect.width, rect.height) / 2 - margin);
      // 限制在屏幕内
      const clampedX = Math.max(8, Math.min(rect.width - 8, bx));
      const clampedY = Math.max(8, Math.min(rect.height - 8, by));
      const angle = Math.atan2(center.y - cy, center.x - cx);
      context.translate(clampedX, clampedY);
      context.rotate(angle);
      context.fillStyle = "rgba(240, 185, 60, 0.95)";
      context.beginPath();
      context.moveTo(14, 0);
      context.lineTo(-6, -9);
      context.lineTo(-2, 0);
      context.lineTo(-6, 9);
      context.closePath();
      context.fill();
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.fillStyle = "#f0b93c";
      context.font = "600 11px ui-monospace, Consolas, monospace";
      context.fillText(`${label} 屏外→`, clampedX + 16, clampedY + 16);
    }
    context.restore();
  }

  function render(now) {
    createOverlay();
    createControls();
    createLocatorPanel();
    if (
      !state.overlay ||
      !state.context ||
      !arenaPageVisible() ||
      officialDialogVisible()
    ) {
      if (state.overlay) {
        state.overlay.style.display = "none";
      }
      setControlsVisible(false);
      if (state.distanceCard) {
        state.distanceCard.style.display = "none";
      }
      requestAnimationFrame(render);
      return;
    }
    const mapCanvas = findMapCanvas(now);
    if (!mapCanvas) {
      state.overlay.style.display = "none";
      setControlsVisible(false);
      if (state.distanceCard) {
        state.distanceCard.style.display = "none";
      }
      requestAnimationFrame(render);
      return;
    }
    const rect = mapCanvas.getBoundingClientRect();
    if (!resizeOverlay(rect)) {
      requestAnimationFrame(render);
      return;
    }
    setControlsVisible(true);
    positionControls(rect);
    state.camera = core.findCameraState(mapCanvas) || state.camera;
    if (!state.camera) {
      requestAnimationFrame(render);
      return;
    }

    captureBrowserResources(now, mapCanvas);

    drawResources(state.context, rect.width, rect.height);
    drawBrowserResources(state.context, rect.width, rect.height);
    if (state.settings.showRoutes) {
      const routes = Array.isArray(state.payload.routes) ? state.payload.routes : [];
      for (const route of routes) {
        if (route && Array.isArray(route.path)) {
          drawRoute(state.context, route, rect.width, rect.height);
        }
      }
    }
    drawUnitLabels(state.context, rect.width, rect.height);
      const hover = hoverCell(rect);
      drawHover(state.context, rect, hover);
      drawSelection(state.context, rect);
      renderDistanceCard(rect);
      drawRally(state.context, rect);
    drawLocator(state.context, rect);
    drawHud(state.context, rect, hover);
    renderStatusBar();
    if (state.locatorOpen && (state.locatorTimer === null || now - state.locatorTimer > 1000)) {
      state.locatorTimer = now;
      renderLocator();
    }
    requestAnimationFrame(render);
  }

  window.addEventListener(
    "message",
    (event) => {
      const message = event.data;
      if (event.source !== window || !message || message.channel !== CHANNEL) {
        return;
      }
      if (message.kind === "routes" && message.payload && typeof message.payload === "object") {
        state.payload = message.payload;
      } else if (
        (message.kind === "browser-intel" || message.kind === "browser-intel:server") &&
        message.payload &&
        typeof message.payload === "object"
      ) {
        state.browserIntel = {
          version: 1,
          source: "browser",
          captured_at: typeof message.payload.captured_at === "string" ? message.payload.captured_at : null,
          resources: Array.isArray(message.payload.resources) ? message.payload.resources : [],
        };
      } else if (message.kind === "stats" && message.payload && typeof message.payload === "object") {
        observeTickTiming(message.payload);
        state.stats = message.payload;
        centerFollowedUnit();
        renderStatusBar();
        if (state.locatorOpen) {
          renderLocator();
        }
      } else if (message.kind === "logs" && message.payload && typeof message.payload === "object") {
        updateLogs(message.payload);
      } else if (message.kind === "control" && message.payload && typeof message.payload === "object") {
        state.control = {
          mode: ["develop", "aggress", "beacon", "migrate"].includes(message.payload.mode)
            ? message.payload.mode
            : "develop",
          recall: Boolean(message.payload.recall),
          raid_enabled: Boolean(message.payload.raid_enabled),
          raid_recall: Boolean(message.payload.raid_recall),
          raid_vanguards:
            typeof message.payload.raid_vanguards === "number"
              ? message.payload.raid_vanguards
              : state.control.raid_vanguards ?? 1,
          raid_rangers:
            typeof message.payload.raid_rangers === "number"
              ? message.payload.raid_rangers
              : state.control.raid_rangers ?? 2,
          beacon_target_distance:
            typeof message.payload.beacon_target_distance === "number"
              ? message.payload.beacon_target_distance
              : state.control.beacon_target_distance ?? 0,
          rally_point:
            Array.isArray(message.payload.rally_point) &&
            message.payload.rally_point.length === 2
              ? [Number(message.payload.rally_point[0]), Number(message.payload.rally_point[1])]
              : null,
          aggress_vanguards:
            typeof message.payload.aggress_vanguards === "number"
              ? message.payload.aggress_vanguards
              : state.control.aggress_vanguards ?? 0,
          aggress_rangers:
            typeof message.payload.aggress_rangers === "number"
              ? message.payload.aggress_rangers
              : state.control.aggress_rangers ?? 0,
        };
        syncControls();
        renderStatusBar();
      } else if (message.kind === "status") {
        state.serviceOnline = Boolean(message.payload && message.payload.online);
      } else if (message.kind === "settings") {
        state.settings = core.normalizeSettings(message.payload);
        syncControls();
      }
    },
    false,
  );
  window.addEventListener(
    "pointermove",
    (event) => {
      state.pointer = { x: event.clientX, y: event.clientY };
    },
    { passive: true },
  );
  window.addEventListener("keydown", (event) => {
    const target = event.target;
    const editing =
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement ||
      (target instanceof HTMLElement && target.isContentEditable);
    if (!editing && event.altKey && event.shiftKey && event.code === "KeyR") {
      event.preventDefault();
      toggleRoutes();
    } else if (!editing && event.code === "Escape") {
      state.selectedCell = null;
    } else if (!editing && event.altKey && event.shiftKey && event.code === "KeyL") {
      event.preventDefault();
      state.logsOpen = !state.logsOpen;
      if (state.logsOpen) {
        state.statsOpen = false;
        state.locatorOpen = false;
        state.unreadLogs = 0;
        renderLogs();
      }
      syncControls();
    } else if (!editing && event.altKey && event.shiftKey && event.code === "Digit1") {
      event.preventDefault();
      updateControl({ mode: "develop" });
    } else if (!editing && event.altKey && event.shiftKey && event.code === "Digit2") {
      event.preventDefault();
      updateControl({ mode: "aggress" });
    } else if (!editing && event.altKey && event.shiftKey && event.code === "Digit3") {
      event.preventDefault();
      updateControl({ mode: "beacon" });
    } else if (!editing && event.altKey && event.shiftKey && event.code === "KeyC") {
      event.preventDefault();
      updateControl({ recall: !state.control.recall });
    } else if (!editing && event.altKey && event.shiftKey && event.code === "KeyM") {
      event.preventDefault();
      // Alt+Shift+M：在鼠标悬停处设置兵力集结标记
      const mapCanvas = findMapCanvas(performance.now());
      if (mapCanvas && state.pointer && state.camera) {
        const rect = mapCanvas.getBoundingClientRect();
        const localX = state.pointer.x - rect.left;
        const localY = state.pointer.y - rect.top;
        if (
          localX >= 0 &&
          localY >= 0 &&
          localX < rect.width &&
          localY < rect.height
        ) {
          const cell = core.screenToGrid(
            localX,
            localY,
            state.camera,
            rect.width,
            rect.height,
          );
          if (cell) {
            updateControl({ rally_point: [cell[0], cell[1]] });
          }
        }
      }
    } else if (!editing && event.altKey && event.shiftKey && event.code === "KeyU") {
      event.preventDefault();
      // Alt+Shift+U：清除集结标记
      updateControl({ rally_point: null });
    }
  });
  window.addEventListener("blur", () => {
    state.pointer = null;
  });
  requestAnimationFrame(render);
})();
