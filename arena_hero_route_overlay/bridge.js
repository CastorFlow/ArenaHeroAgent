(() => {
  "use strict";

  const CHANNEL = "arena-hero-route-overlay/v1";
  const POLL_INTERVAL_MS = 1500;
  const SETTINGS_KEY = "arenaHeroRouteOverlaySettingsV1";

  function publish(kind, payload) {
    window.postMessage({ channel: CHANNEL, kind, payload }, "*");
  }

  function send(message, callback) {
    chrome.runtime.sendMessage(message, (response) => {
      const failed = Boolean(chrome.runtime.lastError) || !response || !response.ok;
      callback(failed, response && response.payload);
    });
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
    send({ type: "ARENA_HERO_OVERLAY_GET_CONTROL" }, (failed, payload) => {
      if (!failed) {
        publish("control", payload);
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
    send(
      {
        type: "ARENA_HERO_OVERLAY_SET_CONTROL",
        update: {
          mode: message.payload.mode,
          recall: message.payload.recall,
          beacon_target_distance: message.payload.beacon_target_distance,
          rally_point: message.payload.rally_point,
        },
      },
      (failed, payload) => {
        if (!failed && payload) {
          publish("control", payload);
        }
      },
    );
  }

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
    if (message.kind === "settings:update") {
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
