/**
 * TradeJournal Chrome Extension — Popup Script
 * All DOM event listeners are attached here (MV3 CSP blocks inline onclick).
 */

// ── State ────────────────────────────────────────────────────────────────────
let pipValues          = {};
let selectedDirection  = "";
let selectedOutcome    = "";
let currentUser        = null;
let capturedScreenshots = [];   // up to 3 dataUrls
let riskMode           = "pct";   // "pct" | "fixed"
let _sentTimer         = null;

// Pip size per instrument (price movement = 1 pip)
const PIP_SIZE = {
  XAUUSD: 0.1,   XAGUSD: 0.01,
  BTCUSD: 1.0,   ETHUSD: 0.1,
  US30:   1.0,   NAS100: 1.0,   US500: 0.1,   UK100: 1.0,   GER40: 1.0,
};
function getPipSize(sym) {
  if (PIP_SIZE[sym] !== undefined) return PIP_SIZE[sym];
  if (sym.endsWith("JPY")) return 0.01;
  return 0.0001;
}

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
  chrome.storage.local.get(["accountSize","riskPct","riskFixed","riskMode"], (p) => {
    if (p.accountSize) document.getElementById("accountSize").value = p.accountSize;
    if (p.riskPct)     document.getElementById("riskPct").value     = p.riskPct;
    if (p.riskFixed)   document.getElementById("riskFixed").value   = p.riskFixed;
    if (p.riskMode === "fixed") {
      document.getElementById("riskFixedMode").click();
    }
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
  ["calcInstrument","accountSize","riskPct","riskFixed","entryPrice","stopPrice","tpPrice"].forEach(id => {
    document.getElementById(id).addEventListener("input", onCalcChange);
    document.getElementById(id).addEventListener("change", onCalcChange);
  });

  // Risk mode toggle
  document.getElementById("riskPctMode").addEventListener("click", () => {
    riskMode = "pct";
    document.getElementById("riskPctMode").classList.add("active");
    document.getElementById("riskFixedMode").classList.remove("active");
    document.getElementById("riskPctRow").style.display  = "";
    document.getElementById("riskFixedRow").style.display = "none";
    onCalcChange();
  });
  document.getElementById("riskFixedMode").addEventListener("click", () => {
    riskMode = "fixed";
    document.getElementById("riskFixedMode").classList.add("active");
    document.getElementById("riskPctMode").classList.remove("active");
    document.getElementById("riskFixedRow").style.display = "";
    document.getElementById("riskPctRow").style.display   = "none";
    onCalcChange();
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

  // Live sentiment on notes input (debounced 1.2s)
  document.getElementById("logNotes").addEventListener("input", () => {
    clearTimeout(_sentTimer);
    const text = document.getElementById("logNotes").value.trim();
    if (text.length < 10) { hideSentiment(); return; }
    document.getElementById("sentPreview").style.display = "block";
    document.getElementById("sentLoading").style.display = "inline";
    _sentTimer = setTimeout(() => analyzeSentiment(text), 1200);
  });

  // Screenshot: capture visible tab
  document.getElementById("captureBtn").addEventListener("click", captureChart);

  // Screenshot: file picker (mobile / desktop file)
  document.getElementById("screenshotFile").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => addScreenshot(ev.target.result);
    reader.readAsDataURL(file);
    e.target.value = "";   // reset so same file can be re-selected
  });
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
  const accountSize = parseFloat(document.getElementById("accountSize").value) || 10000;
  const entryPrice  = parseFloat(document.getElementById("entryPrice").value);
  const stopPrice   = parseFloat(document.getElementById("stopPrice").value);
  const tpPrice     = parseFloat(document.getElementById("tpPrice").value);

  // Dollar risk from whichever mode is active
  let dollarRisk;
  if (riskMode === "fixed") {
    dollarRisk = parseFloat(document.getElementById("riskFixed").value) || 100;
  } else {
    const pct  = parseFloat(document.getElementById("riskPct").value) || 1;
    dollarRisk = accountSize * (pct / 100);
    // sync fixed box so switching modes feels consistent
    document.getElementById("riskFixed").value = dollarRisk.toFixed(0);
  }

  // Save prefs
  chrome.storage.local.set({ accountSize, riskMode,
    riskPct:   parseFloat(document.getElementById("riskPct").value)   || 1,
    riskFixed: parseFloat(document.getElementById("riskFixed").value) || 100,
  });

  const result = document.getElementById("calcResult");
  if (!instrument || isNaN(entryPrice) || isNaN(stopPrice) || entryPrice === stopPrice) {
    result.style.display = "none";
    return;
  }

  const pipVal  = pipValues[instrument] || 10.0;
  const pipSize = getPipSize(instrument);
  const stopPips = Math.abs(entryPrice - stopPrice) / pipSize;
  const lots     = dollarRisk / (stopPips * pipVal);

  // TP & RR
  const hasTP   = !isNaN(tpPrice) && tpPrice > 0;
  const tpPips  = hasTP ? Math.abs(tpPrice - entryPrice) / pipSize : 0;
  const rr      = hasTP && stopPips > 0 ? (tpPips / stopPips) : 0;

  result.style.display = "block";
  document.getElementById("resultLots").textContent   = lots.toFixed(2);
  document.getElementById("resultRisk").textContent   = "$" + dollarRisk.toFixed(0);
  document.getElementById("resultPips").textContent   = stopPips.toFixed(1);
  document.getElementById("resultTPPips").textContent = hasTP ? tpPips.toFixed(1) : "—";
  document.getElementById("resultPipVal").textContent = "$" + pipVal.toFixed(2);
  document.getElementById("resultMini").textContent   = (lots * 10).toFixed(2);
  document.getElementById("resultMicro").textContent  = (lots * 100).toFixed(1);

  const rrEl = document.getElementById("resultRR");
  if (hasTP && rr > 0) {
    rrEl.style.display = "block";
    document.getElementById("rrVal").textContent = "1:" + rr.toFixed(2);
  } else {
    rrEl.style.display = "none";
  }

  // Auto-fill log trade form
  if (lots > 0 && lots < 100000) {
    document.getElementById("logLots").value       = lots.toFixed(2);
    document.getElementById("logInstrument").value = instrument;
    document.getElementById("logEntry").value      = entryPrice;
    document.getElementById("logStop").value       = stopPrice;
    if (hasTP) document.getElementById("logTarget").value = tpPrice;
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

// ── Sentiment preview ─────────────────────────────────────────────────────────
const POSITIVE_EMOTIONS = new Set([
  "discipline","patience","confidence","calm","conviction","satisfaction"
]);

async function analyzeSentiment(text) {
  const resp = await apiRequest("POST", "/api/ext/sentiment", { text });
  document.getElementById("sentLoading").style.display = "none";
  if (!resp || resp.status !== 200 || resp.data.error) return;
  renderSentiment(resp.data);
}

function renderSentiment(s) {
  const readiness = s.trade_readiness ?? 0;
  const score     = s.score          ?? 0;
  const emotions  = s.emotions       || [];
  const tip       = s.coaching_tip   || "";
  const violations = s.violations    || [];

  // Show box
  document.getElementById("sentPreview").style.display = "block";

  // Label + score colour
  const scoreColor = score >= 0.4 ? "#00e5a0" : score <= -0.3 ? "#f87171" : "#f59e0b";
  const labelEl = document.getElementById("sentLabel");
  labelEl.textContent = s.label || "";
  labelEl.style.color = scoreColor;
  labelEl.style.borderColor = scoreColor + "44";

  // Trade readiness bar
  const barColor = readiness >= 8 ? "#00e5a0" : readiness >= 5 ? "#f59e0b" : "#ef4444";
  document.getElementById("sentReadiness").textContent = readiness + "/10";
  document.getElementById("sentReadiness").style.color = barColor;
  const bar = document.getElementById("sentReadinessBar");
  bar.style.width      = (readiness * 10) + "%";
  bar.style.background = barColor;

  // Emotions chips
  const emotionsEl = document.getElementById("sentEmotions");
  emotionsEl.innerHTML = "";
  emotions.forEach(em => {
    const chip = document.createElement("span");
    chip.className = "sent-chip " + (POSITIVE_EMOTIONS.has(em) ? "pos" : "neg");
    chip.textContent = em;
    emotionsEl.appendChild(chip);
  });

  // Coaching tip
  const tipEl = document.getElementById("sentTip");
  if (tip) {
    tipEl.textContent = "💡 " + tip;
    tipEl.style.display = "block";
  } else {
    tipEl.style.display = "none";
  }

  // Violations
  const violEl = document.getElementById("sentViolations");
  if (violations.length > 0) {
    violEl.style.display = "block";
    violEl.innerHTML = violations.map(v =>
      `<div class="sent-viol-item">${v}</div>`
    ).join("");
  } else {
    violEl.style.display = "none";
  }
}

function hideSentiment() {
  document.getElementById("sentPreview").style.display = "none";
  document.getElementById("sentLoading").style.display = "none";
}

// ── Screenshot helpers ────────────────────────────────────────────────────────
async function captureChart() {
  const btn = document.getElementById("captureBtn");
  btn.innerHTML = '<span class="ss-icon">📷</span> Capturing...';
  btn.disabled  = true;

  try {
    // Find the actual browser tab (lastFocusedWindow avoids grabbing the popup's own window)
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab) throw new Error("No active browser tab found");

    const dataUrl = await new Promise((resolve, reject) => {
      chrome.tabs.captureVisibleTab(
        tab.windowId,
        { format: "jpeg", quality: 85 },
        (url) => {
          if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
          else resolve(url);
        }
      );
    });

    const resized = await resizeDataUrl(dataUrl, 1200, 0.82);
    addScreenshot(resized);
  } catch (err) {
    showToast("Capture failed — use Upload instead", true);
    console.error("[TJ capture]", err.message);
  } finally {
    btn.innerHTML = '<span class="ss-icon">📷</span> Capture Chart';
    btn.disabled  = false;
  }
}

function resizeDataUrl(dataUrl, maxWidth, quality) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const scale = img.width > maxWidth ? maxWidth / img.width : 1;
      const w = Math.round(img.width  * scale);
      const h = Math.round(img.height * scale);
      const canvas = document.createElement("canvas");
      canvas.width = w; canvas.height = h;
      canvas.getContext("2d").drawImage(img, 0, 0, w, h);
      resolve(canvas.toDataURL("image/jpeg", quality));
    };
    img.onerror = () => resolve(dataUrl);  // fallback: use original
    img.src = dataUrl;
  });
}

function addScreenshot(dataUrl) {
  if (capturedScreenshots.length >= 3) {
    showToast("Max 3 screenshots allowed", true);
    return;
  }
  capturedScreenshots.push(dataUrl);
  renderThumbs();
}

function removeScreenshot(idx) {
  capturedScreenshots.splice(idx, 1);
  renderThumbs();
}

function renderThumbs() {
  const container = document.getElementById("ssThumbs");
  const countEl   = document.getElementById("ssCount");
  container.innerHTML = "";
  capturedScreenshots.forEach((dataUrl, i) => {
    const wrap = document.createElement("div");
    wrap.style.cssText = "position:relative;display:inline-block";
    const img = document.createElement("img");
    img.src = dataUrl;
    img.style.cssText = "width:90px;height:56px;object-fit:cover;border-radius:6px;border:1px solid #1e2a38";
    const btn = document.createElement("button");
    btn.textContent = "✕";
    btn.style.cssText = "position:absolute;top:2px;right:2px;background:rgba(0,0,0,.7);color:#fff;border:none;border-radius:50%;width:16px;height:16px;font-size:10px;cursor:pointer;line-height:14px;padding:0";
    btn.addEventListener("click", () => removeScreenshot(i));
    wrap.appendChild(img);
    wrap.appendChild(btn);
    container.appendChild(wrap);
  });
  countEl.textContent = `(${capturedScreenshots.length}/3)`;
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
    screenshot_b64:  capturedScreenshots[0] || null,
    screenshot_b64_extra: capturedScreenshots.slice(1),
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
  capturedScreenshots = [];
  renderThumbs();
  hideSentiment();
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
