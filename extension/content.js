/**
 * TradeJournal Chrome Extension — Content Script (content.js)
 * Runs on backtesting-journalmytrades.com pages.
 * Reads the ext-token injected by Flask and forwards it to the service worker.
 */

(function () {
  // Read token from meta tag injected by Flask
  const meta = document.querySelector('meta[name="ext-token"]');
  if (meta && meta.content) {
    chrome.runtime.sendMessage(
      { type: "STORE_TOKEN", payload: { token: meta.content } },
      (resp) => {
        if (chrome.runtime.lastError) return;
        // Token stored silently
      }
    );
  }
})();
