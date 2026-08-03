"use strict";

const ROUTES_URL = "http://127.0.0.1:8765/routes";

async function fetchRoutes() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1200);
  try {
    const response = await fetch(ROUTES_URL, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`route service returned ${response.status}`);
    }
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "ARENA_HERO_OVERLAY_GET_ROUTES") {
    return false;
  }
  fetchRoutes().then(
    (payload) => sendResponse({ ok: true, payload }),
    () => sendResponse({ ok: false }),
  );
  return true;
});
