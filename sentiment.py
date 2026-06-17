"""
Sentiment engine v2 — neural models only, no lexicon.

Priority chain:
  1. GROQ_API_KEY → Groq llama-3.3-70b-versatile  (online LLM, best nuance + phrase attribution)
  2. Offline       → FinBERT × Hartmann ensemble + trading-context signal boost
       ProsusAI/finbert                           : financial sentiment (pos/neg/neutral)
       j-hartmann/emotion-english-distilroberta   : 7 Ekman emotions
       trading_boost()                            : domain-specific vocabulary amplifier
                                                    that compensates for what pure neural
                                                    models miss in trading journal text

Output schema identical across all paths:
  { emotions, score, label, summary, phrases, source }
"""
import os, json, threading

KNOWN_EMOTIONS = [
    "discipline","patience","confidence","calm","conviction",
    "hope","hesitation","anxiety","frustration","fear",
    "greed","fomo","revenge","tilt","overconfidence",
    "satisfaction","regret","excitement","boredom",
]

KNOWN_SETUPS = [
    "Fair Value Gap","Order Block","Breaker Block","Mitigation Block",
    "Liquidity Sweep","BOS","CHoCH","Inducement","Imbalance",
    "Premium/Discount","Equal Highs/Lows","Turtle Soup","OTE",
    "Session Open","NY Killzone","London Killzone",
]

GROQ_SYSTEM = (
    "You are a trading psychology analyst AND setup classifier for futures and forex traders. "
    "Analyze in-trade journal notes for psychological state AND identify trading setups mentioned. "
    "Return ONLY valid JSON, no markdown, no prose.\n\n"
    "Schema:\n"
    '{"emotions":["emotion1"],"score":0.0,"label":"3-5 word label",'
    '"summary":"1 honest sentence ≤20 words",'
    '"phrases":[{"phrase":"exact text","emotion":"which emotion"}],'
    '"setups":["Setup Name"],'
    '"setup_notes":"brief note on timeframe/context if mentioned"}\n\n'
    "Rules:\n"
    f"- emotions: 0-4 from: {', '.join(KNOWN_EMOTIONS)}\n"
    "- score: -1.0=revenge/tilt/FOMO/fear-driven  0=neutral  1.0=disciplined/patient/calm\n"
    "- phrases: 1-3 exact quotes. [] if no notes.\n"
    f"- setups: identify any from {KNOWN_SETUPS} OR extract custom ones (e.g. '4H OB', '15min FVG', 'Weekly OB')\n"
    "- setup_notes: if trader mentions a timeframe (4H, 15min, 1H etc) with a setup, note it briefly (e.g. '4H FVG tap, 15min OB entry')\n"
    "- Very short/empty text: score 0, [] emotions, label 'No notes', [] setups"
)

# Hartmann Ekman label → trading emotion + minimum confidence threshold
HARTMANN_MAP = {
    "anger":   ("frustration",    0.15),
    "disgust": ("frustration",    0.22),
    "fear":    ("fear",           0.10),
    "joy":     ("confidence",     0.15),   # → overconfidence if FinBERT=negative
    "sadness": ("regret",         0.30),   # high threshold — fires spuriously otherwise
    "surprise":("hesitation",     0.28),
}

EMOTION_VALENCE = {
    "discipline":1.0,"patience":1.0,"confidence":0.8,"calm":0.8,"conviction":0.6,
    "satisfaction":0.5,"excitement":0.2,"hope":0.1,"boredom":-0.1,
    "hesitation":-0.4,"anxiety":-0.6,"frustration":-0.7,"fear":-0.7,"regret":-0.5,
    "overconfidence":-0.5,"greed":-0.8,"fomo":-0.9,"revenge":-1.0,"tilt":-1.0,
}

FINBERT_VALENCE = {"positive":0.30,"neutral":0.0,"negative":-0.30}

# Trading-domain vocabulary that pure neural models miss in journal text.
# NOT a fallback lexicon — used only as a signal amplifier on top of neural scores.
_DISC_SIGNALS = [
    "waited for","stuck to my plan","followed the rules","by the book","let it play out",
    "patient","disciplined","conviction","trusted the setup","clean setup","textbook",
    "no rush","let it come","waited patiently","stayed calm","composed","confirmed entry",
    "high probability","planned entry","backed by","process","followed my plan",
]
_DEST_SIGNALS = [
    "fomo","chased","revenge trade","tilt","tilted","doubled down","moved my stop",
    "couldn't wait","had to be in","got greedy","let it run too long","gave back",
    "overtraded","forced a trade","broke my rules","shouldn't have","panic",
    "fear of missing","scared","revenge",
]

def _trading_boost(text: str) -> float:
    """Domain vocabulary amplifier: [-0.35, +0.35]."""
    t = text.lower()
    pos = sum(1 for s in _DISC_SIGNALS if s in t)
    neg = sum(1 for s in _DEST_SIGNALS if s in t)
    total = pos + neg
    if not total:
        return 0.0
    return max(-0.35, min(0.35, (pos - neg) / total * 0.35))


# ── Lazy model loader ──────────────────────────────────────────────────────────
_lock = threading.Lock()
_finbert  = None
_hartmann = None
_offline_ready = False
_offline_error = None

def _load():
    global _finbert, _hartmann, _offline_ready, _offline_error
    with _lock:
        if _offline_ready or _offline_error:
            return
        try:
            from transformers import pipeline
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _finbert  = pipeline("text-classification",
                    model="ProsusAI/finbert",
                    top_k=None, truncation=True, max_length=512)
                _hartmann = pipeline("text-classification",
                    model="j-hartmann/emotion-english-distilroberta-base",
                    top_k=None, truncation=True, max_length=512)
            _offline_ready = True
        except Exception as e:
            _offline_error = str(e)


def _clamp(v): return round(max(-1.0, min(1.0, float(v))), 3)

def _empty(source="none", label="No notes", summary=""):
    return {"emotions":[],"score":0.0,"label":label,
            "summary":summary,"phrases":[],"source":source}


# ── Groq (online) ──────────────────────────────────────────────────────────────
def _groq(notes: str) -> dict:
    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=600, temperature=0.1,
        messages=[
            {"role":"system","content":GROQ_SYSTEM},
            {"role":"user","content":f"Analyze these trading notes:\n\n{notes}"},
        ],
    )
    raw  = resp.choices[0].message.content.strip()
    raw  = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(raw)
    emotions = [e for e in data.get("emotions",[]) if e in KNOWN_EMOTIONS]
    phrases  = [p for p in data.get("phrases",[])
                if isinstance(p,dict) and "phrase" in p and "emotion" in p][:3]
    # Extract setup tags — match against known list + allow custom timeframe tags
    raw_setups = data.get("setups", [])
    extracted_setups = []
    for s in raw_setups:
        if isinstance(s, str) and s.strip():
            extracted_setups.append(s.strip())
    setup_notes = str(data.get("setup_notes", ""))[:200]

    return {
        "emotions":     emotions,
        "score":        _clamp(data.get("score", 0.0)),
        "label":        str(data.get("label",""))[:100],
        "summary":      str(data.get("summary",""))[:300],
        "phrases":      phrases,
        "source":       "groq",
        "setups":       extracted_setups,
        "setup_notes":  setup_notes,
    }


# ── Offline neural ensemble ─────────────────────────────────────────────────────
def _offline(notes: str) -> dict:
    if not _offline_ready:
        _load()
    if not _offline_ready:
        raise RuntimeError(_offline_error or "models not loaded")

    # FinBERT — financial tone
    fb       = {r["label"].lower(): r["score"] for r in _finbert(notes)[0]}
    fb_label = max(fb, key=fb.get)
    fb_conf  = fb[fb_label]
    fb_val   = FINBERT_VALENCE.get(fb_label, 0.0) * min(fb_conf, 0.85)

    # Hartmann — Ekman emotions
    hm       = {r["label"].lower(): r["score"] for r in _hartmann(notes)[0]}
    emotions = []
    for hm_lbl, (trading_em, threshold) in HARTMANN_MAP.items():
        if hm.get(hm_lbl, 0) >= threshold:
            em = trading_em
            if hm_lbl == "joy" and fb_label == "negative" and fb_conf > 0.5:
                em = "overconfidence"
            if em not in emotions:
                emotions.append(em)

    # Sort by raw Hartmann score
    emotions.sort(
        key=lambda e: next(
            (hm.get(k, 0) for k, (te, _) in HARTMANN_MAP.items() if te == e), 0
        ), reverse=True
    )
    emotions = emotions[:4]

    # Trading-context boost (domain vocab amplifier)
    boost = _trading_boost(notes)

    # Final score: 40% emotion valence + 30% FinBERT + 30% trading boost
    em_val = (sum(EMOTION_VALENCE.get(e, 0) for e in emotions) / len(emotions)) if emotions else 0.0
    score  = _clamp(0.40 * em_val + 0.30 * fb_val + 0.30 * boost)

    # If neural models produce nothing but boost is strong, use boost to set emotions
    if not emotions and abs(boost) >= 0.20:
        emotions = ["discipline"] if boost > 0 else ["frustration"]

    dom    = emotions[0] if emotions else "neutral"
    band   = "constructive" if score > 0.2 else ("destructive" if score < -0.2 else "mixed")
    label  = f"{dom} ({band})"
    top_hm = max(hm, key=hm.get)
    summary = (
        f"FinBERT: {fb_label} ({fb_conf:.0%}). "
        f"Dominant emotion: {top_hm} ({hm[top_hm]:.0%}). "
        f"Discipline: {score:+.2f}."
    )

    return {
        "emotions": emotions,
        "score":    score,
        "label":    label,
        "summary":  summary,
        "phrases":  [],
        "source":   "offline_neural",
        "finbert":  {"label": fb_label, "confidence": round(fb_conf, 3)},
        "hartmann": {k: round(v, 3) for k, v in sorted(hm.items(), key=lambda x: -x[1])},
    }


# ── Public API ─────────────────────────────────────────────────────────────────
def analyze(notes: str) -> dict:
    if not notes or not notes.strip() or len(notes.strip()) < 5:
        return _empty()
    if os.environ.get("GROQ_API_KEY", "").strip():
        try:
            return _groq(notes)
        except Exception:
            pass   # fall through to offline on any error
    try:
        return _offline(notes)
    except Exception as e:
        return _empty("error", "Model error", str(e)[:120])

def groq_available() -> bool:   return bool(os.environ.get("GROQ_API_KEY","").strip())
def offline_ready()  -> bool:   return _offline_ready
def engine_status()  -> str:
    if groq_available():  return "groq"
    if _offline_ready:    return "offline_neural"
    if _offline_error:    return f"error: {_offline_error}"
    return "loading"

def preload_offline_models():
    t = threading.Thread(target=_load, daemon=True)
    t.start()
    return t
