/**
 * TradeJournal Chrome Extension — Service Worker (background.js)
 * Handles token storage and message routing between content scripts and popup.
 */

const API_BASE = "https://backtesting-journalmytrades.com";

// ── Message dispatcher ──────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const { type, payload } = msg;

  if (type === "STORE_TOKEN") {
    // Sent by content.js when it detects the ext token on the dashboard page
    chrome.storage.local.set({ ext_token: payload.token, stored_at: Date.now() }, () => {
      sendResponse({ ok: true });
    });
    return true; // keep channel open for async sendResponse
  }

  if (type === "GET_TOKEN") {
    chrome.storage.local.get(["ext_token", "stored_at"], (data) => {
      const age = (Date.now() - (data.stored_at || 0)) / 1000;
      // Token is valid for 24h on server side; refresh after 12h to be safe
      if (data.ext_token && age < 43200) {
        sendResponse({ token: data.ext_token });
      } else {
        sendResponse({ token: null });
      }
    });
    return true;
  }

  if (type === "CLEAR_TOKEN") {
    chrome.storage.local.remove(["ext_token", "stored_at"], () => {
      sendResponse({ ok: true });
    });
    return true;
  }

  if (type === "API_REQUEST") {
    // Proxy API calls from popup through background to avoid CORS issues
    const { method, path, body } = payload;
    chrome.storage.local.get("ext_token", (data) => {
      if (!data.ext_token) {
        sendResponse({ error: "not_authenticated" });
        return;
      }
      const url = API_BASE + path;
      const opts = {
        method: method || "GET",
        headers: {
          "Authorization": "Bearer " + data.ext_token,
          "Content-Type": "application/json",
          "X-Extension": "1",
        },
      };
      if (body) opts.body = JSON.stringify(body);

      fetch(url, opts)
        .then(r => r.json().then(d => ({ status: r.status, data: d })))
        .catch(e => ({ status: 0, data: { error: e.message } }))
        .then(result => sendResponse(result));
    });
    return true;
  }
});

// ── Context menu (future): open dashboard ────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  console.log("[TradeJournal] Extension installed.");
});
