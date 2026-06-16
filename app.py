"""
TradeJournal — Flask app
Run locally:  python app.py
Deploy:       gunicorn app:app
"""
import os, json
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import Config, INSTRUMENT_PIP, SESSIONS, SETUP_TAGS
from models import db, Trade, Settings, ImportLog
import metrics as kpi
import sentiment as sent
from sentiment import preload_offline_models
import importer as imp

app = Flask(__name__)
app.config.from_object(Config)
CORS(app)
db.init_app(app)

with app.app_context():
    preload_offline_models()  # warm HuggingFace models in background
    db.create_all()
    # Seed default settings for user 1 if absent
    if not Settings.query.filter_by(user_id=1).first():
        db.session.add(Settings(user_id=1))
        db.session.commit()


# ─── helpers ────────────────────────────────────────────────────────────────
USER_ID = 1   # single-user now; swap to session["user_id"] when auth added

def _get_settings():
    s = Settings.query.filter_by(user_id=USER_ID).first()
    return s.to_dict() if s else {}

def _get_trades():
    trades = Trade.query.filter_by(user_id=USER_ID).order_by(
        Trade.trade_date.asc(), Trade.entry_time.asc(), Trade.id.asc()
    ).all()
    return [t.to_dict() for t in trades]

def _compute_derived(data: dict) -> dict:
    """Compute planned_risk_usd, planned_rr, realized_pnl, realized_r from prices/lots."""
    instrument = data.get("instrument", "")
    pip_val    = INSTRUMENT_PIP.get(instrument.upper(), 10.0)
    lots       = _num(data.get("lots"))
    entry      = _num(data.get("entry_price"))
    stop       = _num(data.get("stop_price"))
    target     = _num(data.get("target_price"))
    exit_price = _num(data.get("exit_price"))
    direction  = data.get("direction", "Long")

    # Risk: pip-mode OR dollar-mode
    dollar_risk = _num(data.get("dollar_risk"))
    stop_pips   = _num(data.get("stop_pips"))
    target_pips = _num(data.get("target_pips"))

    planned_risk = dollar_risk
    if planned_risk is None and stop_pips is not None and lots is not None:
        planned_risk = abs(stop_pips) * pip_val * lots
    if planned_risk is None and entry is not None and stop is not None and lots is not None:
        planned_risk = abs(entry - stop) * pip_val * lots / 0.0001  # price diff → pips for forex
        # For metals / non-forex where 1 price unit = pip_val directly
        if instrument.upper() in ["XAUUSD", "XAGUSD"]:
            planned_risk = abs(entry - stop) * pip_val * lots

    planned_rr = None
    if target_pips is not None and stop_pips is not None and stop_pips != 0:
        planned_rr = abs(target_pips / stop_pips)
    elif entry is not None and stop is not None and target is not None and entry != stop:
        planned_rr = abs(target - entry) / abs(entry - stop)

    # Realized P&L
    realized_pnl = _num(data.get("realized_pnl"))
    if realized_pnl is None and exit_price is not None and entry is not None and lots is not None:
        sign = 1 if direction == "Long" else -1
        if instrument.upper() in ["XAUUSD", "XAGUSD"]:
            realized_pnl = (exit_price - entry) * sign * pip_val * lots
        else:
            realized_pnl = (exit_price - entry) * sign * pip_val * lots / 0.0001

    realized_r = None
    if realized_pnl is not None and planned_risk and planned_risk > 0:
        realized_r = realized_pnl / planned_risk

    # Duration
    dur = _num(data.get("duration_minutes"))
    if dur is None:
        from dateutil import parser as dp
        try:
            et = data.get("entry_time", "")
            xt = data.get("exit_time", "")
            td = data.get("trade_date", "")
            if et and xt and td:
                e_dt = dp.parse(f"{td} {et}")
                x_dt = dp.parse(f"{td} {xt}")
                if x_dt > e_dt:
                    dur = (x_dt - e_dt).total_seconds() / 60
        except Exception:
            pass

    data.update({
        "planned_risk_usd": round(planned_risk, 2) if planned_risk is not None else None,
        "planned_rr":       round(planned_rr, 2)   if planned_rr   is not None else None,
        "realized_pnl":     round(realized_pnl, 2) if realized_pnl is not None else None,
        "realized_r":       round(realized_r, 3)   if realized_r   is not None else None,
        "duration_minutes": round(dur, 1)           if dur          is not None else None,
    })
    return data


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _save_trade(data: dict) -> Trade:
    """Persist a single trade dict to DB."""
    t = Trade(
        user_id=USER_ID,
        trade_date=data.get("trade_date"),
        entry_time=data.get("entry_time"),
        exit_time=data.get("exit_time"),
        duration_minutes=data.get("duration_minutes"),
        instrument=data.get("instrument"),
        session=data.get("session"),
        direction=data.get("direction"),
        lots=data.get("lots"),
        contracts=data.get("contracts"),
        entry_price=data.get("entry_price"),
        stop_price=data.get("stop_price"),
        target_price=data.get("target_price"),
        exit_price=data.get("exit_price"),
        stop_pips=data.get("stop_pips"),
        target_pips=data.get("target_pips"),
        dollar_risk=data.get("dollar_risk"),
        planned_risk_usd=data.get("planned_risk_usd"),
        planned_rr=data.get("planned_rr"),
        realized_pnl=data.get("realized_pnl"),
        realized_r=data.get("realized_r"),
        commission=data.get("commission") or 0.0,
        order_type=data.get("order_type", "MARKET"),
        setups=json.dumps(data.get("setups") or []),
        notes=data.get("notes"),
        import_source=data.get("import_source", "manual"),
        emotions=json.dumps(data.get("emotions") or []),
        sentiment_label=data.get("sentiment_label"),
        sentiment_score=data.get("sentiment_score"),
        sentiment_summary=data.get("sentiment_summary"),
        sentiment_phrases=json.dumps(data.get("sentiment_phrases") or []),
        sentiment_source=data.get("sentiment_source", "none"),
    )
    db.session.add(t)
    db.session.flush()
    return t


# ─── routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html",
        sessions=SESSIONS, setup_tags=SETUP_TAGS,
        instruments=list(INSTRUMENT_PIP.keys()),
        ai_available=sent.groq_available())


# Trades
@app.route("/api/trades", methods=["GET"])
def list_trades():
    return jsonify(_get_trades())


@app.route("/api/trades", methods=["POST"])
def create_trade():
    payload = request.get_json(force=True)
    payload = _compute_derived(payload)

    s = sent.analyze(payload.get("notes", ""))
    payload.update({
        "sentiment_label":   s["label"],
        "sentiment_score":   s["score"],
        "sentiment_summary": s["summary"],
        "sentiment_phrases": s["phrases"],
        "sentiment_source":  s["source"],
        "emotions":          sorted(set(payload.get("emotions") or []) | set(s.get("emotions") or [])),
    })
    t = _save_trade(payload)
    db.session.commit()
    return jsonify({"id": t.id, "sentiment": s, "trade": t.to_dict()}), 201


@app.route("/api/trades/<int:trade_id>", methods=["DELETE"])
def delete_trade(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=USER_ID).first_or_404()
    db.session.delete(t)
    db.session.commit()
    return jsonify({"deleted": trade_id})


@app.route("/api/trades/<int:trade_id>/sentiment", methods=["POST"])
def retry_sentiment(trade_id):
    """Re-run LLM sentiment on a trade (e.g. after adding notes or API key)."""
    t = Trade.query.filter_by(id=trade_id, user_id=USER_ID).first_or_404()
    s = sent.analyze(t.notes or "")
    t.sentiment_label   = s["label"]
    t.sentiment_score   = s["score"]
    t.sentiment_summary = s["summary"]
    t.sentiment_phrases = json.dumps(s["phrases"])
    t.sentiment_source  = s["source"]
    emotions = json.loads(t.emotions or "[]")
    t.emotions = json.dumps(sorted(set(emotions) | set(s.get("emotions") or [])))
    db.session.commit()
    return jsonify(s)


# Metrics
@app.route("/api/metrics")
def metrics_endpoint():
    trades   = _get_trades()
    settings = _get_settings()
    return jsonify(kpi.compute_all(trades, settings))


# Settings
@app.route("/api/settings", methods=["GET", "POST"])
def settings_endpoint():
    s = Settings.query.filter_by(user_id=USER_ID).first()
    if request.method == "POST":
        body = request.get_json(force=True)
        for k, v in body.items():
            if hasattr(s, k):
                setattr(s, k, v)
        db.session.commit()
    return jsonify(s.to_dict())


# File import
@app.route("/api/import", methods=["POST"])
def import_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    result = imp.import_file(f.read(), f.filename)
    imported = 0
    skipped  = result["skipped"]
    errors   = result["errors"]

    for trade_data in result["trades"]:
        try:
            trade_data = _compute_derived(trade_data)
            if trade_data.get("notes"):
                s = sent.analyze(trade_data["notes"])
                trade_data.update({
                    "sentiment_label": s["label"], "sentiment_score": s["score"],
                    "sentiment_summary": s["summary"], "sentiment_phrases": s["phrases"],
                    "sentiment_source": s["source"],
                    "emotions": sorted(set(s.get("emotions") or [])),
                })
            else:
                trade_data["sentiment_source"] = "none"
            _save_trade(trade_data)
            imported += 1
        except Exception as e:
            errors.append(str(e))
            skipped += 1

    db.session.commit()

    log = ImportLog(user_id=USER_ID, filename=f.filename,
                    format_detected=result["format"],
                    trades_imported=imported, trades_skipped=skipped,
                    errors=json.dumps(errors[:20]))
    db.session.add(log)
    db.session.commit()

    return jsonify({
        "imported": imported, "skipped": skipped,
        "format": result["format"], "errors": errors[:5]
    })


# Sentiment standalone (preview before save)
@app.route("/api/sentiment", methods=["POST"])
def sentiment_endpoint():
    text = request.get_json(force=True).get("text", "")
    return jsonify(sent.analyze(text))


if __name__ == "__main__":
    print(f"\n  TradeJournal  →  http://127.0.0.1:5000")
    print(f"  AI sentiment: {'ON (Groq llama-3.3-70b)' if sent.groq_available() else 'OFF — set GROQ_API_KEY}")
    print(f"  DB: {app.config['SQLALCHEMY_DATABASE_URI'][:60]}\n")
    app.run(debug=True, port=5000)

@app.route("/api/engine_status")
def engine_status():
    return jsonify({
        "groq": sent.groq_available(),
        "offline_ready": sent.offline_ready(),
        "status": sent.engine_status(),
    })

@app.route("/api/trades/<int:trade_id>", methods=["PUT"])
def update_trade(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=USER_ID).first_or_404()
    payload = request.get_json(force=True)
    payload = _compute_derived(payload)

    # Re-run sentiment if notes changed
    if payload.get("notes") != t.notes:
        s = sent.analyze(payload.get("notes", ""))
        payload.update({
            "sentiment_label":   s["label"],
            "sentiment_score":   s["score"],
            "sentiment_summary": s["summary"],
            "sentiment_phrases": s["phrases"],
            "sentiment_source":  s["source"],
            "emotions": sorted(set(s.get("emotions") or [])),
        })
    else:
        payload["sentiment_label"]   = t.sentiment_label
        payload["sentiment_score"]   = t.sentiment_score
        payload["sentiment_summary"] = t.sentiment_summary
        payload["sentiment_phrases"] = json.loads(t.sentiment_phrases or "[]")
        payload["sentiment_source"]  = t.sentiment_source
        payload["emotions"]          = json.loads(t.emotions or "[]")

    # Update all fields
    for field in ["trade_date","entry_time","exit_time","duration_minutes",
                  "instrument","session","direction","lots","contracts",
                  "entry_price","stop_price","target_price","exit_price",
                  "stop_pips","target_pips","dollar_risk","planned_risk_usd",
                  "planned_rr","realized_pnl","realized_r","commission","order_type"]:
        if field in payload:
            setattr(t, field, payload[field])

    t.setups            = json.dumps(payload.get("setups") or [])
    t.notes             = payload.get("notes")
    t.emotions          = json.dumps(payload.get("emotions") or [])
    t.sentiment_label   = payload.get("sentiment_label")
    t.sentiment_score   = payload.get("sentiment_score")
    t.sentiment_summary = payload.get("sentiment_summary")
    t.sentiment_phrases = json.dumps(payload.get("sentiment_phrases") or [])
    t.sentiment_source  = payload.get("sentiment_source")

    db.session.commit()
    return jsonify({"id": t.id, "sentiment": {
        "label": t.sentiment_label, "score": t.sentiment_score,
        "source": t.sentiment_source, "summary": t.sentiment_summary,
    }})

# ── Image upload ──────────────────────────────────────────────────────────────
import uuid
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def _upload_image(file) -> str:
    """
    Returns a URL string for the stored image.
    - If CLOUDINARY_URL env var is set → upload to Cloudinary (works on Render)
    - Otherwise → save to static/uploads/ (local dev)
    """
    cloudinary_url = os.environ.get("CLOUDINARY_URL", "")
    if cloudinary_url:
        try:
            import cloudinary
            import cloudinary.uploader
            cloudinary.config(cloudinary_url=cloudinary_url)
            result = cloudinary.uploader.upload(
                file,
                folder="tradejournal",
                resource_type="image",
                transformation=[{"width": 1200, "crop": "limit", "quality": "auto"}],
            )
            return result["secure_url"]
        except Exception as e:
            app.logger.error(f"Cloudinary upload failed: {e}")
            raise

    # Local storage
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)
    return f"/static/uploads/{filename}"


@app.route("/api/trades/<int:trade_id>/image", methods=["POST"])
def upload_trade_image(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=USER_ID).first_or_404()
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400
    file = request.files["image"]
    if not file.filename or not _allowed(file.filename):
        return jsonify({"error": "Invalid file type. Use PNG, JPG, GIF or WEBP."}), 400
    try:
        url = _upload_image(file)
        t.image_url = url
        db.session.commit()
        return jsonify({"image_url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades/<int:trade_id>/image", methods=["DELETE"])
def delete_trade_image(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=USER_ID).first_or_404()
    # Delete local file if it's a local path
    if t.image_url and t.image_url.startswith("/static/uploads/"):
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), t.image_url.lstrip("/"))
        if os.path.exists(local):
            os.remove(local)
    t.image_url = None
    db.session.commit()
    return jsonify({"deleted": True})
