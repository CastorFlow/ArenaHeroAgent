(() => {
  "use strict";

  const CHANNEL = "arena-hero-route-overlay/v1";
  const OVERLAY_ATTRIBUTE = "data-arena-hero-agent-route-overlay";
  const POLL_INTERVAL_MS = 1500;
  const BROWSER_INTEL_MIN_INTERVAL_MS = 1200;
  const SETTINGS_KEY = "arenaHeroRouteOverlaySettingsV1";
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
      if (event.isTrusted && overlayInput) {
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
