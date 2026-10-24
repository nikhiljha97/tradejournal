/**
 * TradeJournal Chrome Extension — Popup Script (popup.js)
 * Handles lot calculator, trade logging, and auth flow.
 */

// ── State ────────────────────────────────────────────────────────────────────
let pipValues = {};
let instruments = [];
let selectedDirection = "";
let selectedOutcome = "";
let currentUser = null;

// ── Boot ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  // Set today's date as default
  document.getElementById("logDate").value = new Date().toISOString().split("T")[0];

  // Check auth
  const token = await getToken();
  if (!token) {
    showAuth();
    return;
  }

  const me = await apiRequest("GET", "/api/ext/me");
  if (me.status !== 200 || me.data.error) {
    showAuth();
    return;
  }

  currentUser = me.data;
  document.getElementById("userPill").textContent = currentUser.username;
  document.getElementById("userPill").style.display = "block";
  showMain();

  // Load instruments
  const instResp = await apiRequest("GET", "/api/ext/instruments");
  if (instResp.status === 200) {
    instruments = instResp.data.instruments || [];
    pipValues = instResp.data.pip_values || {};
    populateInstrumentSelects(instruments);
  }

  // Load saved settings
  chrome.storage.local.get(["accountSize", "riskPct"], (prefs) => {
    if (prefs.accountSize) document.getElementById("accountSize").value = prefs.accountSize;
    if (prefs.riskPct)    document.getElementById("riskPct").value    = prefs.riskPct;
  });
});

// ── Auth helpers ─────────────────────────────────────────────────────────────
function getToken() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "GET_TOKEN" }, (resp) => {
      resolve(resp ? resp.token : null);
    });
  });
}

function showAuth() {
  document.getElementById("authScreen").style.display = "block";
  document.getElementById("mainApp").style.display    = "none";
}

function showMain() {
  document.getElementById("authScreen").style.display = "none";
  document.getElementById("mainApp").style.display    = "block";
}

function retryAuth() {
  chrome.runtime.sendMessage({ type: "CLEAR_TOKEN" }, () => {
    window.close();
  });
}

function handleLogout() {
  chrome.runtime.sendMessage({ type: "CLEAR_TOKEN" }, () => {
    showAuth();
    document.getElementById("userPill").style.display = "none";
    currentUser = null;
  });
}

// ── API wrapper ───────────────────────────────────────────────────────────────
function apiRequest(method, path, body) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { type: "API_REQUEST", payload: { method, path, body } },
      (resp) => {
        resolve(resp || { status: 0, data: { error: "No response" } });
      }
    );
  });
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
function switchTab(tabId) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tabId));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === "panel-" + tabId));
}

// ── Instruments ───────────────────────────────────────────────────────────────
function populateInstrumentSelects(list) {
  [document.getElementById("calcInstrument"), document.getElementById("logInstrument")].forEach(sel => {
    sel.innerHTML = "";
    const blank = document.createElement("option");
    blank.value = ""; blank.textContent = "Select instrument...";
    sel.appendChild(blank);
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

  // Save prefs
  if (!isNaN(accountSize)) chrome.storage.local.set({ accountSize });
  if (!isNaN(riskPct))     chrome.storage.local.set({ riskPct });

  if (!instrument || isNaN(accountSize) || isNaN(riskPct) || isNaN(entryPrice) || isNaN(stopPrice)) {
    document.getElementById("calcResult").style.display = "none";
    return;
  }

  const pipVal       = pipValues[instrument] || 10.0;
  const dollarRisk   = accountSize * (riskPct / 100);
  const priceDiff    = Math.abs(entryPrice - stopPrice);

  // Pip calculation: for JPY pairs and indices, scale accordingly
  let pipDistance;
  if (instrument.endsWith("JPY") || ["US30","NAS100","UK100","GER40"].includes(instrument)) {
    pipDistance = priceDiff; // 1 pip = 1 point for indices; JPY uses 0.01 = 1 pip
    if (instrument.endsWith("JPY")) pipDistance = priceDiff * 100;
  } else {
    pipDistance = priceDiff * 10000; // standard forex
    if (["XAUUSD","BTCUSD","ETHUSD","XAGUSD"].includes(instrument)) {
      pipDistance = priceDiff * 100; // metals/crypto use different pip scale
    }
  }

  if (pipDistance === 0) {
    document.getElementById("calcResult").style.display = "none";
    return;
  }

  const lots = dollarRisk / (pipDistance * pipVal);

  // Display
  document.getElementById("calcResult").style.display = "block";
  document.getElementById("resultLots").textContent  = lots.toFixed(2);
  document.getElementById("resultRisk").textContent  = "$" + dollarRisk.toFixed(0);
  document.getElementById("resultPips").textContent  = pipDistance.toFixed(1);
  document.getElementById("resultPipVal").textContent = "$" + pipVal.toFixed(2);
  document.getElementById("resultMini").textContent  = (lots * 10).toFixed(2);

  // Auto-fill lot size in log trade tab
  if (lots > 0 && lots < 1000) {
    document.getElementById("logLots").value = lots.toFixed(2);
    document.getElementById("logInstrument").value = instrument;
    document.getElementById("logEntry").value = entryPrice;
    document.getElementById("logStop").value  = stopPrice;
  }
}

// ── Log Trade ─────────────────────────────────────────────────────────────────
function setDirection(dir) {
  selectedDirection = dir;
  document.getElementById("btnBuy").className  = "dir-btn" + (dir === "Buy"  ? " active-buy"  : "");
  document.getElementById("btnSell").className = "dir-btn" + (dir === "Sell" ? " active-sell" : "");
}

function setOutcome(outcome) {
  selectedOutcome = outcome;
  ["Win","Loss","BE","Open"].forEach(o => {
    const el = document.getElementById("outcome" + o);
    if (!el) return;
    const key = o.toLowerCase();
    el.classList.toggle("win",  key === outcome && outcome === "win");
    el.classList.toggle("loss", key === outcome && outcome === "loss");
    el.classList.toggle("be",   key === outcome && outcome === "be");
  });
}

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

  // Validation
  if (!instrument) return showToast("Select an instrument", true);
  if (!direction)  return showToast("Select direction (Long/Short)", true);
  if (isNaN(entry) || isNaN(stop)) return showToast("Enter entry and stop prices", true);
  if (isNaN(lots) || lots <= 0)    return showToast("Enter valid lot size", true);

  const payload = {
    instrument,
    direction,
    entry_price:  entry,
    stop_price:   isNaN(stop)   ? null : stop,
    target_price: isNaN(target) ? null : target,
    exit_price:   isNaN(exitPrice) ? null : exitPrice,
    lots,
    trade_date:   tradeDate || new Date().toISOString().split("T")[0],
    realized_pnl: isNaN(pnl)   ? null : pnl,
    outcome:      selectedOutcome || null,
    notes:        notes || "",
    source:       "extension",
  };

  const btn = document.querySelector('#panel-log .btn-primary');
  btn.textContent = "Logging...";
  btn.disabled    = true;

  const resp = await apiRequest("POST", "/api/ext/trades", payload);

  btn.textContent = "Log Trade";
  btn.disabled    = false;

  if (resp.status === 201) {
    showToast("Trade logged!");
    resetLogForm();
  } else {
    const errMsg = (resp.data && resp.data.error) ? resp.data.error : "Failed to log trade";
    showToast(errMsg, true);
  }
}

function resetLogForm() {
  document.getElementById("logInstrument").value = "";
  document.getElementById("logEntry").value      = "";
  document.getElementById("logStop").value       = "";
  document.getElementById("logTarget").value     = "";
  document.getElementById("logLots").value       = "";
  document.getElementById("logExit").value       = "";
  document.getElementById("logPnl").value        = "";
  document.getElementById("logNotes").value      = "";
  document.getElementById("logDate").value       = new Date().toISOString().split("T")[0];
  selectedDirection = ""; selectedOutcome = "";
  ["btnBuy","btnSell"].forEach(id => document.getElementById(id).className = "dir-btn");
  ["outcomeWin","outcomeLoss","outcomeBE","outcomeOpen"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = "outcome-btn";
  });
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer;
function showToast(msg, isError) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className   = "toast" + (isError ? " error" : "");
  requestAnimationFrame(() => { el.classList.add("show"); });
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.classList.remove("show"); }, 2800);
}
