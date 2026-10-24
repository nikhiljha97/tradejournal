/**
 * TradeJournal Chrome Extension — Popup Script
 * All DOM event listeners are attached here (MV3 CSP blocks inline onclick).
 */

// ── State ────────────────────────────────────────────────────────────────────
let pipValues          = {};
let selectedDirection  = "";
let selectedOutcome    = "";
let currentUser        = null;
let capturedScreenshot = null;   // base64 dataUrl of the attached chart screenshot

// ── Boot ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  // Default date
  document.getElementById("logDate").value = todayStr();

  // Wire up all event listeners
  wireEvents();

  // Auth check
  const token = await getToken();
  if (!token) { showAuth(); return; }

  const me = await apiRequest("GET", "/api/ext/me");
  if (!me || me.status !== 200 || me.data.error) { showAuth(); return; }

  currentUser = me.data;
  const pill = document.getElementById("userPill");
  pill.textContent = currentUser.username;
  pill.style.display = "block";
  showMain();

  // Load instruments
  const ir = await apiRequest("GET", "/api/ext/instruments");
  if (ir && ir.status === 200) {
    pipValues = ir.data.pip_values || {};
    populateInstruments(ir.data.instruments || []);
  }

  // Restore saved prefs
  chrome.storage.local.get(["accountSize", "riskPct"], (p) => {
    if (p.accountSize) document.getElementById("accountSize").value = p.accountSize;
    if (p.riskPct)     document.getElementById("riskPct").value     = p.riskPct;
  });
});

// ── Wire all DOM events (no inline handlers) ──────────────────────────────────
function wireEvents() {
  // Tabs
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  // Auth buttons
  document.getElementById("retryBtn").addEventListener("click", retryAuth);
  document.getElementById("userPill").addEventListener("click", handleLogout);

  // Lot calc inputs
  ["calcInstrument","accountSize","riskPct","entryPrice","stopPrice"].forEach(id => {
    document.getElementById(id).addEventListener("input", onCalcChange);
    document.getElementById(id).addEventListener("change", onCalcChange);
  });

  // Direction buttons
  document.getElementById("btnBuy").addEventListener("click",  () => setDirection("Buy"));
  document.getElementById("btnSell").addEventListener("click", () => setDirection("Sell"));

  // Outcome buttons
  document.getElementById("outcomeWin").addEventListener("click",  () => setOutcome("win"));
  document.getElementById("outcomeLoss").addEventListener("click", () => setOutcome("loss"));
  document.getElementById("outcomeBE").addEventListener("click",   () => setOutcome("be"));
  document.getElementById("outcomeOpen").addEventListener("click", () => setOutcome("open"));

  // Submit trade
  document.getElementById("submitTradeBtn").addEventListener("click", submitTrade);

  // Screenshot: capture visible tab
  document.getElementById("captureBtn").addEventListener("click", captureChart);

  // Screenshot: file picker (mobile / desktop file)
  document.getElementById("screenshotFile").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => setScreenshot(ev.target.result);
    reader.readAsDataURL(file);
    e.target.value = "";   // reset so same file can be re-selected
  });

  // Remove screenshot
  document.getElementById("ssRemove").addEventListener("click", clearScreenshot);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function todayStr() {
  return new Date().toISOString().split("T")[0];
}

function getToken() {
  return new Promise(resolve => {
    chrome.runtime.sendMessage({ type: "GET_TOKEN" }, r => resolve(r ? r.token : null));
  });
}

function apiRequest(method, path, body) {
  return new Promise(resolve => {
    chrome.runtime.sendMessage(
      { type: "API_REQUEST", payload: { method, path, body } },
      r => resolve(r || { status: 0, data: { error: "No response" } })
    );
  });
}

// ── Visibility ────────────────────────────────────────────────────────────────
function showAuth() {
  const a = document.getElementById("authScreen");
  const m = document.getElementById("mainApp");
  a.style.display = "flex";
  m.style.display = "none";
}

function showMain() {
  const a = document.getElementById("authScreen");
  const m = document.getElementById("mainApp");
  a.style.display = "none";
  m.style.display = "flex";
}

function retryAuth() {
  chrome.runtime.sendMessage({ type: "CLEAR_TOKEN" }, () => window.close());
}

function handleLogout() {
  chrome.runtime.sendMessage({ type: "CLEAR_TOKEN" }, () => {
    document.getElementById("userPill").style.display = "none";
    currentUser = null;
    showAuth();
  });
}

// ── Tabs ──────────────────────────────────────────────────────────────────────
function switchTab(tabId) {
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("active", t.dataset.tab === tabId)
  );
  document.querySelectorAll(".panel").forEach(p =>
    p.classList.toggle("active", p.id === "panel-" + tabId)
  );
}

// ── Instruments ───────────────────────────────────────────────────────────────
function populateInstruments(list) {
  ["calcInstrument", "logInstrument"].forEach(id => {
    const sel = document.getElementById(id);
    sel.innerHTML = '<option value="">Select instrument...</option>';
    list.forEach(sym => {
      const opt = document.createElement("option");
      opt.value = sym; opt.textContent = sym;
      sel.appendChild(opt);
    });
  });
}

// ── Lot Calculator ────────────────────────────────────────────────────────────
function onCalcChange() {
  const instrument  = document.getElementById("calcInstrument").value;
  const accountSize = parseFloat(document.getElementById("accountSize").value);
  const riskPct     = parseFloat(document.getElementById("riskPct").value);
  const entryPrice  = parseFloat(document.getElementById("entryPrice").value);
  const stopPrice   = parseFloat(document.getElementById("stopPrice").value);

  if (!isNaN(accountSize)) chrome.storage.local.set({ accountSize });
  if (!isNaN(riskPct))     chrome.storage.local.set({ riskPct });

  const result = document.getElementById("calcResult");
  if (!instrument || isNaN(accountSize) || isNaN(riskPct) || isNaN(entryPrice) || isNaN(stopPrice)) {
    result.style.display = "none";
    return;
  }

  const pipVal     = pipValues[instrument] || 10.0;
  const dollarRisk = accountSize * (riskPct / 100);
  const priceDiff  = Math.abs(entryPrice - stopPrice);

  let pipDistance;
  if (["US30","NAS100","UK100","GER40","US500"].includes(instrument)) {
    pipDistance = priceDiff;
  } else if (instrument.endsWith("JPY")) {
    pipDistance = priceDiff * 100;
  } else if (["XAUUSD","XAGUSD","BTCUSD","ETHUSD"].includes(instrument)) {
    pipDistance = priceDiff * 100;
  } else {
    pipDistance = priceDiff * 10000;
  }

  if (!pipDistance) { result.style.display = "none"; return; }

  const lots = dollarRisk / (pipDistance * pipVal);
  result.style.display = "block";
  document.getElementById("resultLots").textContent  = lots.toFixed(2);
  document.getElementById("resultRisk").textContent  = "$" + dollarRisk.toFixed(0);
  document.getElementById("resultPips").textContent  = pipDistance.toFixed(1);
  document.getElementById("resultPipVal").textContent = "$" + pipVal.toFixed(2);
  document.getElementById("resultMini").textContent  = (lots * 10).toFixed(2);

  // Auto-fill log form
  if (lots > 0 && lots < 10000) {
    document.getElementById("logLots").value      = lots.toFixed(2);
    document.getElementById("logInstrument").value = instrument;
    document.getElementById("logEntry").value      = entryPrice;
    document.getElementById("logStop").value       = stopPrice;
  }
}

// ── Direction ─────────────────────────────────────────────────────────────────
function setDirection(dir) {
  selectedDirection = dir;
  document.getElementById("btnBuy").className  = "dir-btn" + (dir === "Buy"  ? " active-buy"  : "");
  document.getElementById("btnSell").className = "dir-btn" + (dir === "Sell" ? " active-sell" : "");
}

// ── Outcome ───────────────────────────────────────────────────────────────────
function setOutcome(outcome) {
  selectedOutcome = outcome;
  const map = { win: "win", loss: "loss", be: "be", open: "open-active" };
  document.getElementById("outcomeWin").className  = "outcome-btn" + (outcome === "win"  ? " win"         : "");
  document.getElementById("outcomeLoss").className = "outcome-btn" + (outcome === "loss" ? " loss"        : "");
  document.getElementById("outcomeBE").className   = "outcome-btn" + (outcome === "be"   ? " be"          : "");
  document.getElementById("outcomeOpen").className = "outcome-btn" + (outcome === "open" ? " open-active" : "");
}

// ── Screenshot helpers ────────────────────────────────────────────────────────
async function captureChart() {
  const btn = document.getElementById("captureBtn");
  btn.textContent = "Capturing...";
  btn.disabled    = true;

  const resp = await new Promise(resolve =>
    chrome.runtime.sendMessage({ type: "CAPTURE_TAB" }, resolve)
  );

  btn.innerHTML  = '<span class="ss-icon">📷</span> Capture Chart';
  btn.disabled   = false;

  if (resp?.dataUrl) {
    setScreenshot(resp.dataUrl);
  } else {
    showToast(resp?.error || "Capture failed", true);
  }
}

function setScreenshot(dataUrl) {
  capturedScreenshot = dataUrl;
  document.getElementById("ssThumb").src    = dataUrl;
  document.getElementById("ssPreview").style.display = "block";
}

function clearScreenshot() {
  capturedScreenshot = null;
  document.getElementById("ssPreview").style.display = "none";
  document.getElementById("ssThumb").src = "";
}

// ── Submit trade ──────────────────────────────────────────────────────────────
async function submitTrade() {
  const instrument = document.getElementById("logInstrument").value;
  const direction  = selectedDirection;
  const entry      = parseFloat(document.getElementById("logEntry").value);
  const stop       = parseFloat(document.getElementById("logStop").value);
  const target     = parseFloat(document.getElementById("logTarget").value);
  const lots       = parseFloat(document.getElementById("logLots").value);
  const tradeDate  = document.getElementById("logDate").value;
  const exitPrice  = parseFloat(document.getElementById("logExit").value);
  const pnl        = parseFloat(document.getElementById("logPnl").value);
  const notes      = document.getElementById("logNotes").value.trim();

  if (!instrument)            return showToast("Select an instrument", true);
  if (!direction)             return showToast("Select Long or Short", true);
  if (isNaN(entry) || isNaN(stop)) return showToast("Enter entry and stop prices", true);
  if (isNaN(lots) || lots <= 0)    return showToast("Enter a valid lot size", true);

  const payload = {
    instrument, direction,
    entry_price:  entry,
    stop_price:   isNaN(stop)      ? null : stop,
    target_price: isNaN(target)    ? null : target,
    exit_price:   isNaN(exitPrice) ? null : exitPrice,
    lots,
    trade_date:   tradeDate || todayStr(),
    realized_pnl:    isNaN(pnl)         ? null : pnl,
    outcome:         selectedOutcome    || null,
    notes:           notes              || "",
    source:          "extension",
    screenshot_b64:  capturedScreenshot || null,
  };

  const btn = document.getElementById("submitTradeBtn");
  btn.textContent = "Logging...";
  btn.disabled    = true;

  const resp = await apiRequest("POST", "/api/ext/trades", payload);

  btn.textContent = "Log Trade";
  btn.disabled    = false;

  if (resp && resp.status === 201) {
    showToast("Trade logged!");
    resetLogForm();
  } else {
    const errMsg = resp?.data?.error || "Failed to log — check connection";
    showToast(errMsg, true);
  }
}

function resetLogForm() {
  ["logInstrument","logEntry","logStop","logTarget","logLots","logExit","logPnl","logNotes"].forEach(id => {
    document.getElementById(id).value = "";
  });
  document.getElementById("logDate").value = todayStr();
  selectedDirection = ""; selectedOutcome = "";
  clearScreenshot();
  document.getElementById("btnBuy").className  = "dir-btn";
  document.getElementById("btnSell").className = "dir-btn";
  ["outcomeWin","outcomeLoss","outcomeBE","outcomeOpen"].forEach(id => {
    document.getElementById(id).className = "outcome-btn";
  });
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer;
function showToast(msg, isError) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className   = "toast" + (isError ? " error" : "");
  requestAnimationFrame(() => el.classList.add("show"));
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), 2800);
}
