"use strict";

const ROUTES_URL = "http://127.0.0.1:8765/routes";
const STATS_URL = "http://127.0.0.1:8765/stats";
const LOGS_URL = "http://127.0.0.1:8765/logs";
const CONTROL_URL = "http://127.0.0.1:8765/control";
const BROWSER_INTEL_URL = "http://127.0.0.1:8765/browser-intel";

async function fetchJson(url, options) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1200);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      signal: controller.signal,
      ...options,
    });
    if (!response.ok) {
      throw new Error(`route service returned ${response.status}`);
    }
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

async function setControl(update) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1200);
  try {
    const response = await fetch(CONTROL_URL, {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    });
    if (!response.ok) {
      throw new Error(`control service returned ${response.status}`);
    }
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

async function setBrowserIntel(payload) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1200);
  try {
    const response = await fetch(BROWSER_INTEL_URL, {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (!response.ok) {
      throw new Error(`browser intel service returned ${response.status}`);
    }
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message.type !== "string") {
    return false;
  }
  if (message.type === "ARENA_HERO_OVERLAY_GET_ROUTES") {
    fetchJson(ROUTES_URL).then(
      (payload) => sendResponse({ ok: true, payload }),
      () => sendResponse({ ok: false }),
    );
    return true;
  }
  if (message.type === "ARENA_HERO_OVERLAY_GET_STATS") {
    fetchJson(STATS_URL).then(
      (payload) => sendResponse({ ok: true, payload }),
      () => sendResponse({ ok: false }),
    );
    return true;
  }
  if (message.type === "ARENA_HERO_OVERLAY_GET_LOGS") {
    fetchJson(LOGS_URL).then(
      (payload) => sendResponse({ ok: true, payload }),
      () => sendResponse({ ok: false }),
    );
    return true;
  }
  if (message.type === "ARENA_HERO_OVERLAY_GET_CONTROL") {
    fetchJson(CONTROL_URL).then(
      (payload) => sendResponse({ ok: true, payload }),
      () => sendResponse({ ok: false }),
    );
    return true;
  }
  if (message.type === "ARENA_HERO_OVERLAY_GET_BROWSER_INTEL") {
    fetchJson(BROWSER_INTEL_URL).then(
      (payload) => sendResponse({ ok: true, payload }),
      () => sendResponse({ ok: false }),
    );
    return true;
  }
  if (message.type === "ARENA_HERO_OVERLAY_SET_CONTROL") {
    setControl(message.update || {}).then(
      (payload) => sendResponse({ ok: true, payload }),
      () => sendResponse({ ok: false }),
    );
    return true;
  }
  if (message.type === "ARENA_HERO_OVERLAY_SET_BROWSER_INTEL") {
    setBrowserIntel(message.payload || {}).then(
      (payload) => sendResponse({ ok: true, payload }),
      () => sendResponse({ ok: false }),
    );
    return true;
  }
  return false;
});
