(() => {
  "use strict";

  const CHANNEL = "arena-hero-route-overlay/v1";
  const OVERLAY_ATTRIBUTE = "data-arena-hero-agent-route-overlay";
  const POLL_INTERVAL_MS = 1500;
  const BROWSER_INTEL_MIN_INTERVAL_MS = 1200;
  const SETTINGS_KEY = "arenaHeroRouteOverlaySettingsV1";
  const CONTROL_SHORTCUTS = new Set([
    "KeyC",
    "KeyM",
    "Digit1",
    "Digit2",
    "Digit3",
  ]);
  let trustedControlUntil = 0;
  let lastBrowserIntelSentAt = 0;

  function publish(kind, payload) {
    window.postMessage({ channel: CHANNEL, kind, payload }, "*");
  }

  function send(message, callback) {
    try {
      chrome.runtime.sendMessage(message, (response) => {
        const failed =
          Boolean(chrome.runtime.lastError) || !response || !response.ok;
        callback(failed, response && response.payload);
      });
    } catch (error) {
      callback(true, undefined);
    }
  }

  function poll() {
    send({ type: "ARENA_HERO_OVERLAY_GET_ROUTES" }, (failed, payload) => {
      if (failed) {
        publish("status", { online: false });
      } else {
        publish("routes", payload);
        publish("status", { online: true });
      }
    });
    send({ type: "ARENA_HERO_OVERLAY_GET_STATS" }, (failed, payload) => {
      if (!failed) {
        publish("stats", payload);
      }
    });
    send({ type: "ARENA_HERO_OVERLAY_GET_LOGS" }, (failed, payload) => {
      if (!failed) {
        publish("logs", payload);
      }
    });
    send({ type: "ARENA_HERO_OVERLAY_GET_CONTROL" }, (failed, payload) => {
      if (!failed) {
        publish("control", payload);
      }
    });
    send({ type: "ARENA_HERO_OVERLAY_GET_BROWSER_INTEL" }, (failed, payload) => {
      if (!failed) {
        publish("browser-intel:server", payload);
      }
    });
    window.setTimeout(poll, POLL_INTERVAL_MS);
  }

  function loadSettings() {
    chrome.storage.local.get([SETTINGS_KEY], (result) => {
      if (chrome.runtime.lastError) {
        publish("settings", {});
        return;
      }
      publish("settings", result[SETTINGS_KEY] || {});
    });
  }

  function handleControlUpdate(message) {
    if (
      !message.payload ||
      typeof message.payload !== "object" ||
      message.kind !== "control:update"
    ) {
      return;
    }
    if (performance.now() > trustedControlUntil) {
      return;
    }
    trustedControlUntil = 0;
    const payload = message.payload;
    const update = {};
    if (["develop", "aggress", "beacon", "migrate"].includes(payload.mode)) {
      update.mode = payload.mode;
    }
    if (typeof payload.recall === "boolean") {
      update.recall = payload.recall;
    }
    for (const key of ["raid_enabled", "raid_recall"]) {
      if (typeof payload[key] === "boolean") {
        update[key] = payload[key];
      }
    }
    if (
      typeof payload.beacon_target_distance === "number" &&
      Number.isFinite(payload.beacon_target_distance)
    ) {
      update.beacon_target_distance = payload.beacon_target_distance;
    }
    if (payload.rally_point === null) {
      update.rally_point = null;
    } else if (
      Array.isArray(payload.rally_point) &&
      payload.rally_point.length === 2 &&
      payload.rally_point.every(Number.isFinite)
    ) {
      update.rally_point = payload.rally_point;
    }
    for (const key of ["aggress_vanguards", "aggress_rangers"]) {
      if (typeof payload[key] === "number" && Number.isFinite(payload[key])) {
        update[key] = payload[key];
      }
    }
    for (const key of ["raid_vanguards", "raid_rangers"]) {
      if (typeof payload[key] === "number" && Number.isFinite(payload[key])) {
        update[key] = payload[key];
      }
    }
    if (!Object.keys(update).length) {
      return;
    }
    send(
      {
        type: "ARENA_HERO_OVERLAY_SET_CONTROL",
        update,
      },
      (failed, payload) => {
        if (!failed && payload) {
          publish("control", payload);
        }
      },
    );
  }

  document.addEventListener(
    "pointerdown",
    (event) => {
      if (
        event.isTrusted &&
        event.target instanceof Element &&
        event.target.closest(`[${OVERLAY_ATTRIBUTE}="control"]`)
      ) {
        trustedControlUntil = performance.now() + 5000;
      }
    },
    true,
  );

  document.addEventListener(
    "keydown",
    (event) => {
      const overlayInput =
        event.target instanceof Element &&
        Boolean(event.target.closest(`[${OVERLAY_ATTRIBUTE}="control"]`));
      const shortcut =
        event.altKey && event.shiftKey && CONTROL_SHORTCUTS.has(event.code);
      if (event.isTrusted && (overlayInput || shortcut)) {
        trustedControlUntil = performance.now() + 5000;
      }
    },
    true,
  );

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (
      event.source !== window ||
      !message ||
      message.channel !== CHANNEL ||
      !message.payload ||
      typeof message.payload !== "object"
    ) {
      return;
    }
    if (message.kind === "browser-intel") {
      const now = performance.now();
      if (now - lastBrowserIntelSentAt < BROWSER_INTEL_MIN_INTERVAL_MS) {
        return;
      }
      const payload = message.payload;
      if (!payload || typeof payload !== "object" || !Array.isArray(payload.resources)) {
        return;
      }
      const resources = [];
      const seen = new Set();
      for (const value of payload.resources.slice(0, 4096)) {
        if (!Array.isArray(value) || value.length !== 2) {
          continue;
        }
        const x = Number(value[0]);
        const y = Number(value[1]);
        if (!Number.isInteger(x) || !Number.isInteger(y)) {
          continue;
        }
        const key = `${x},${y}`;
        if (!seen.has(key)) {
          seen.add(key);
          resources.push([x, y]);
        }
      }
      lastBrowserIntelSentAt = now;
      send({
        type: "ARENA_HERO_OVERLAY_SET_BROWSER_INTEL",
        payload: {
          version: 1,
          source: "browser",
          captured_at:
            typeof payload.captured_at === "string"
              ? payload.captured_at.slice(0, 64)
              : new Date().toISOString(),
          resources,
        },
      }, () => {});
    } else if (message.kind === "settings:update") {
      chrome.storage.local.set({ [SETTINGS_KEY]: message.payload });
    } else if (message.kind === "control:update") {
      handleControlUpdate(message);
    }
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "local" && changes[SETTINGS_KEY]) {
      publish("settings", changes[SETTINGS_KEY].newValue || {});
    }
  });

  loadSettings();
  poll();
})();
