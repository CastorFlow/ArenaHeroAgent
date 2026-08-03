(() => {
  "use strict";

  const CHANNEL = "arena-hero-route-overlay/v1";
  const OVERLAY_ATTRIBUTE = "data-arena-hero-agent-route-overlay";
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
    stats: null,
    control: { mode: "develop", recall: false },
    settings: core.normalizeSettings({}),
    serviceOnline: false,
    pointer: null,
    pointerOverControls: false,
    lastCanvasSearch: 0,
    toolbar: null,
    routeToggle: null,
    settingsButton: null,
    settingsPanel: null,
    settingsOpen: false,
    settingInputs: new Map(),
    statusBar: null,
    modeButton: null,
    recallButton: null,
    statsButton: null,
    statsPanel: null,
    statsOpen: false,
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
      zIndex: "80",
      display: "none",
    });
    document.documentElement.appendChild(canvas);
    state.overlay = canvas;
    state.context = canvas.getContext("2d");
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

  function createControls() {
    if (state.toolbar || !document.documentElement) {
      return;
    }
    const toolbar = controlContainer("div");
    Object.assign(toolbar.style, {
      position: "fixed",
      display: "none",
      alignItems: "center",
      gap: "6px",
      zIndex: "90",
      pointerEvents: "auto",
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
      state.settingsOpen = !state.settingsOpen;
      syncControls();
    });
    toolbar.append(routeToggle, settingsButton);

    const panel = controlContainer("div");
    Object.assign(panel.style, {
      position: "fixed",
      display: "none",
      width: "252px",
      padding: "11px 12px 12px",
      border: "1px solid rgba(255,255,255,0.2)",
      borderRadius: "9px",
      background: "rgba(8,11,18,0.94)",
      color: "#d9e1eb",
      font: "12px system-ui, -apple-system, Segoe UI, sans-serif",
      boxShadow: "0 8px 28px rgba(0,0,0,0.42)",
      zIndex: "90",
      pointerEvents: "auto",
      userSelect: "none",
    });
    const title = document.createElement("div");
    title.textContent = "Arena Hero 叠加层";
    Object.assign(title.style, {
      fontWeight: "700",
      fontSize: "13px",
      marginBottom: "4px",
    });
    const shortcut = document.createElement("div");
    shortcut.textContent = "快捷键：Alt+Shift+R 路线 · Alt+Shift+1 发育 · Alt+Shift+2 侵略 · Alt+Shift+C 召回";
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
    createStatsPanel();
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
      alignItems: "center",
      gap: "8px",
      padding: "4px 10px",
      border: "1px solid rgba(255,255,255,0.2)",
      borderRadius: "7px",
      background: "rgba(8,11,18,0.92)",
      color: "#d9e1eb",
      font: "600 12px system-ui, -apple-system, Segoe UI, sans-serif",
      boxShadow: "0 2px 10px rgba(0,0,0,0.28)",
      zIndex: "90",
      pointerEvents: "auto",
      userSelect: "none",
      flexWrap: "wrap",
      maxWidth: "calc(100vw - 16px)",
    });

    const modeButton = document.createElement("button");
    modeButton.type = "button";
    applyButtonStyle(modeButton);
    modeButton.title = "切换发展模式：发育（Alt+Shift+1）/ 侵略（Alt+Shift+2）";
    modeButton.addEventListener("click", () => {
      const next = state.control.mode === "aggress" ? "develop" : "aggress";
      updateControl({ mode: next });
    });

    const recallButton = document.createElement("button");
    recallButton.type = "button";
    applyButtonStyle(recallButton);
    recallButton.title = "一键召回（Alt+Shift+C）：所有游侠/先锋回核心防守，再点一次解除";
    recallButton.addEventListener("click", () => {
      updateControl({ recall: !state.control.recall });
    });

    const makeStatus = (key, title) => {
      const span = document.createElement("span");
      span.title = title;
      span.style.color = "#aeb9c6";
      bar.appendChild(span);
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
      syncControls();
    });

    bar.append(modeButton, recallButton);
    bar.appendChild(statsButton);

    document.documentElement.appendChild(bar);
    state.statusBar = bar;
    state.modeButton = modeButton;
    state.recallButton = recallButton;
    state.statsButton = statsButton;
    syncControls();
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
      borderRadius: "9px",
      background: "rgba(8,11,18,0.96)",
      color: "#d9e1eb",
      font: "12px system-ui, -apple-system, Segoe UI, sans-serif",
      boxShadow: "0 8px 28px rgba(0,0,0,0.42)",
      zIndex: "90",
      pointerEvents: "auto",
      userSelect: "none",
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
          ["resources", "资源 / 容量"],
          ["population", "人口 (工/先/游)"],
          ["core", "核心 HP / 盾"],
          ["core_position", "核心坐标"],
          ["beacon_position", "信标坐标"],
          ["visible_enemies", "可见敌人"],
          ["owns_beacon", "持有信标"],
          ["visible_resource_cells", "当前可见矿点"],
          ["known_resource_cells", "记忆矿点"],
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
    const mode = stats.mode === "aggress" ? "侵略" : "发育";
    setText("bar:tick", `Tick ${stats.tick}`);
    setText("bar:resources", `资源 ${stats.resources}/${stats.capacity}`);
    setText(
      "bar:population",
      `人口 ${stats.workers}/${stats.vanguards}/${stats.rangers}`,
    );
    setText("bar:enemies", `敌 ${stats.visible_enemies}`);
    setText("bar:core", `HP ${stats.core_hp}/${stats.core_shield}`);
    setText("bar:beacon", stats.owns_beacon ? "信标✓" : "信标✗");

    const pairs = {
      "stats:tick": String(stats.tick),
      "stats:mode": mode,
      "stats:recall": state.control.recall ? "已召回" : "正常",
      "stats:resources": `${stats.resources}/${stats.capacity}`,
      "stats:population": `${stats.workers}/${stats.vanguards}/${stats.rangers}`,
      "stats:core": `${stats.core_hp}/${stats.core_shield}`,
      "stats:core_position": formatPosition(stats.core_position),
      "stats:beacon_position": formatPosition(stats.beacon_position),
      "stats:visible_enemies": String(stats.visible_enemies),
      "stats:owns_beacon": stats.owns_beacon ? "是" : "否",
      "stats:visible_resource_cells": String(stats.visible_resource_cells),
      "stats:known_resource_cells": String(stats.known_resource_cells),
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
    if (state.modeButton) {
      const mode = state.control.mode;
      state.modeButton.textContent = mode === "aggress" ? "侵略模式" : "发育模式";
      state.modeButton.style.color = mode === "aggress" ? "#d98a7a" : "#9bcbbd";
    }
    if (state.recallButton) {
      const recall = state.control.recall;
      state.recallButton.textContent = recall ? "召回中" : "一键召回";
      state.recallButton.style.color = recall ? "#e0b25c" : "#8f9cad";
    }
    if (state.statsPanel) {
      state.statsPanel.style.display = state.statsOpen ? "block" : "none";
    }
    for (const [key, binding] of state.settingInputs) {
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
  }

  function positionControls(rect) {
    if (!state.toolbar || !state.settingsPanel || !state.statusBar) {
      return;
    }
    const left = Math.max(8, rect.left + 10);
    const top = Math.max(8, rect.top + 10);
    state.toolbar.style.left = `${left}px`;
    state.toolbar.style.top = `${top}px`;
    state.settingsPanel.style.left = `${left}px`;
    state.settingsPanel.style.top = `${top + 37}px`;
    // 状态栏放在右上角
    const statusBarWidth = Math.min(720, Math.max(260, rect.width - 20));
    const barLeft = Math.max(8, rect.right - statusBarWidth - 10);
    state.statusBar.style.left = `${barLeft}px`;
    state.statusBar.style.top = `${Math.max(8, rect.top + 10)}px`;
    // 统计面板紧跟状态栏下方
    const panelLeft = Math.max(8, rect.right - Math.min(390, rect.width - 20) - 10);
    if (state.statsPanel) {
      state.statsPanel.style.left = `${panelLeft}px`;
      state.statsPanel.style.top = `${Math.max(8, rect.top + 46)}px`;
    }
  }

  function findMapCanvas(now) {
    if (
      state.mapCanvas &&
      state.mapCanvas.isConnected &&
      !state.mapCanvas.hasAttribute(OVERLAY_ATTRIBUTE)
    ) {
      const rect = state.mapCanvas.getBoundingClientRect();
      if (rect.width >= 300 && rect.height >= 220) {
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
    state.mapCanvas = best;
    state.camera = null;
    return best;
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

  function render(now) {
    createOverlay();
    createControls();
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
      requestAnimationFrame(render);
      return;
    }
    const mapCanvas = findMapCanvas(now);
    if (!mapCanvas) {
      state.overlay.style.display = "none";
      setControlsVisible(false);
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

    drawResources(state.context, rect.width, rect.height);
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
    drawHud(state.context, rect, hover);
    renderStatusBar();
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
      } else if (message.kind === "stats" && message.payload && typeof message.payload === "object") {
        state.stats = message.payload;
        renderStatusBar();
      } else if (message.kind === "control" && message.payload && typeof message.payload === "object") {
        state.control = {
          mode: message.payload.mode === "aggress" ? "aggress" : "develop",
          recall: Boolean(message.payload.recall),
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
    } else if (!editing && event.altKey && event.shiftKey && event.code === "Digit1") {
      event.preventDefault();
      updateControl({ mode: "develop" });
    } else if (!editing && event.altKey && event.shiftKey && event.code === "Digit2") {
      event.preventDefault();
      updateControl({ mode: "aggress" });
    } else if (!editing && event.altKey && event.shiftKey && event.code === "KeyC") {
      event.preventDefault();
      updateControl({ recall: !state.control.recall });
    }
  });
  window.addEventListener("blur", () => {
    state.pointer = null;
  });
  requestAnimationFrame(render);
})();
