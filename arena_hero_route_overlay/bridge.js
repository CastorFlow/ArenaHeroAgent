(() => {
  "use strict";

  const CHANNEL = "arena-hero-route-overlay/v1";
  const POLL_INTERVAL_MS = 1500;
  const SETTINGS_KEY = "arenaHeroRouteOverlaySettingsV1";

  function publish(kind, payload) {
    window.postMessage({ channel: CHANNEL, kind, payload }, "*");
  }

  function poll() {
    chrome.runtime.sendMessage(
      { type: "ARENA_HERO_OVERLAY_GET_ROUTES" },
      (response) => {
        const failed = Boolean(chrome.runtime.lastError) || !response || !response.ok;
        if (failed) {
          publish("status", { online: false });
        } else {
          publish("routes", response.payload);
          publish("status", { online: true });
        }
        window.setTimeout(poll, POLL_INTERVAL_MS);
      },
    );
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

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (
      event.source !== window ||
      !message ||
      message.channel !== CHANNEL ||
      message.kind !== "settings:update" ||
      !message.payload ||
      typeof message.payload !== "object"
    ) {
      return;
    }
    chrome.storage.local.set({ [SETTINGS_KEY]: message.payload });
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "local" && changes[SETTINGS_KEY]) {
      publish("settings", changes[SETTINGS_KEY].newValue || {});
    }
  });

  loadSettings();
  poll();
})();
