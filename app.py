"""
TradeJournal — Flask app with auth, multi-tenancy, Cloudinary image storage.
"""
import os, json, uuid
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import Config, INSTRUMENT_PIP, SESSIONS, SETUP_TAGS
from models import db, Trade, Settings, ImportLog, User
import metrics as kpi
import sentiment as sent
import importer as imp

app = Flask(__name__)
app.config.from_object(Config)
CORS(app)
db.init_app(app)
bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = ""

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


# ── helpers ───────────────────────────────────────────────────────────────────
def uid():
    return current_user.id

def _get_settings():
    s = Settings.query.filter_by(user_id=uid()).first()
    if not s:
        s = Settings(user_id=uid())
        db.session.add(s)
        db.session.commit()
    return s.to_dict()

def _get_trades():
    return [t.to_dict() for t in Trade.query.filter_by(user_id=uid()).order_by(
        Trade.trade_date.asc(), Trade.entry_time.asc(), Trade.id.asc()).all()]

def _num(v):
    if v is None or v == "": return None
    try: return float(str(v).replace(",","").replace("$","").strip())
    except: return None

def _compute_derived(data):
    instrument  = data.get("instrument","")
    pv          = INSTRUMENT_PIP.get(instrument.upper(), 10.0)
    lots        = _num(data.get("lots"))
    entry       = _num(data.get("entry_price"))
    stop        = _num(data.get("stop_price"))
    target      = _num(data.get("target_price"))
    exit_price  = _num(data.get("exit_price"))
    direction   = data.get("direction","Long")
    dollar_risk = _num(data.get("dollar_risk"))
    stop_pips   = _num(data.get("stop_pips"))
    target_pips = _num(data.get("target_pips"))

    planned_risk = dollar_risk
    if planned_risk is None and stop_pips is not None and lots:
        planned_risk = abs(stop_pips) * pv * lots
    if planned_risk is None and entry is not None and stop is not None and lots:
        if instrument.upper() in ["XAUUSD","XAGUSD"]:
            planned_risk = abs(entry - stop) * pv * lots
        else:
            planned_risk = abs(entry - stop) * pv * lots / 0.0001

    planned_rr = None
    if target_pips is not None and stop_pips and stop_pips != 0:
        planned_rr = abs(target_pips / stop_pips)
    elif entry is not None and stop is not None and target is not None and entry != stop:
        planned_rr = abs(target - entry) / abs(entry - stop)

    realized_pnl = _num(data.get("realized_pnl"))
    if realized_pnl is None and exit_price is not None and entry is not None and lots:
        sign = 1 if direction == "Long" else -1
        if instrument.upper() in ["XAUUSD","XAGUSD"]:
            realized_pnl = (exit_price - entry) * sign * pv * lots
        else:
            realized_pnl = (exit_price - entry) * sign * pv * lots / 0.0001

    realized_r = None
    if realized_pnl is not None and planned_risk and planned_risk > 0:
        realized_r = realized_pnl / planned_risk

    dur = _num(data.get("duration_minutes"))
    if dur is None:
        from dateutil import parser as dp
        try:
            td = data.get("trade_date","")
            et = data.get("entry_time","")
            xt = data.get("exit_time","")
            if et and xt and td:
                e_dt = dp.parse(f"{td} {et}")
                x_dt = dp.parse(f"{td} {xt}")
                if x_dt > e_dt:
                    dur = (x_dt - e_dt).total_seconds() / 60
        except: pass

    data.update({
        "stop_pips":       stop_pips,
        "target_pips":     target_pips,
        "planned_risk_usd": round(planned_risk,2)  if planned_risk  is not None else None,
        "planned_rr":       round(planned_rr,2)    if planned_rr    is not None else None,
        "realized_pnl":     round(realized_pnl,2)  if realized_pnl  is not None else None,
        "realized_r":       round(realized_r,3)    if realized_r    is not None else None,
        "duration_minutes": round(dur,1)           if dur           is not None else None,
    })
    return data

def _save_trade(data):
    from datetime import datetime, timezone
    t = Trade(
        user_id=uid(),
        trade_date=data.get("trade_date"), entry_time=data.get("entry_time"),
        exit_time=data.get("exit_time"), duration_minutes=data.get("duration_minutes"),
        instrument=data.get("instrument"), session=data.get("session"),
        direction=data.get("direction"), lots=data.get("lots"),
        contracts=data.get("contracts"), entry_price=data.get("entry_price"),
        stop_price=data.get("stop_price"), target_price=data.get("target_price"),
        exit_price=data.get("exit_price"), stop_pips=data.get("stop_pips"),
        target_pips=data.get("target_pips"), dollar_risk=data.get("dollar_risk"),
        planned_risk_usd=data.get("planned_risk_usd"), planned_rr=data.get("planned_rr"),
        realized_pnl=data.get("realized_pnl"), realized_r=data.get("realized_r"),
        commission=data.get("commission") or 0.0, order_type=data.get("order_type","MARKET"),
        setups=json.dumps(data.get("setups") or []), notes=data.get("notes"),
        import_source=data.get("import_source","manual"),
        emotions=json.dumps(data.get("emotions") or []),
        sentiment_label=data.get("sentiment_label"),
        sentiment_score=data.get("sentiment_score"),
        sentiment_summary=data.get("sentiment_summary"),
        sentiment_phrases=json.dumps(data.get("sentiment_phrases") or []),
        sentiment_source=data.get("sentiment_source","none"),
        image_url=data.get("image_url"),
    )
    db.session.add(t)
    db.session.flush()
    return t

def _allowed(filename):
    return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

def _upload_image(file):
    cloudinary_url = os.environ.get("CLOUDINARY_URL","")
    if cloudinary_url:
        import cloudinary, cloudinary.uploader
        cloudinary.config(cloudinary_url=cloudinary_url)
        result = cloudinary.uploader.upload(
            file, folder=f"tradejournal/user_{uid()}",
            resource_type="image",
            transformation=[{"width":1200,"crop":"limit","quality":"auto"}],
        )
        return result["secure_url"]
    # Local fallback
    ext = file.filename.rsplit(".",1)[1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, fname))
    return f"/static/uploads/{fname}"


# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/register", methods=["GET","POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        data = request.get_json(force=True)
        email    = (data.get("email","")).strip().lower()
        username = (data.get("username","")).strip()
        password = data.get("password","")
        if not email or not username or not password:
            return jsonify({"error":"All fields required"}), 400
        if len(password) < 8:
            return jsonify({"error":"Password must be at least 8 characters"}), 400
        if User.query.filter_by(email=email).first():
            return jsonify({"error":"Email already registered"}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({"error":"Username taken"}), 400
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        user = User(email=email, username=username, password_hash=pw_hash)
        db.session.add(user)
        db.session.flush()
        # Create default settings for new user
        existing = Settings.query.filter_by(user_id=user.id).first()
        if not existing:
            if not Settings.query.filter_by(user_id=user.id).first():
            db.session.add(Settings(user_id=user.id))
        db.session.commit()
        login_user(user, remember=True)
        return jsonify({"ok":True, "username": user.username})
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET","POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        data = request.get_json(force=True)
        email    = (data.get("email","")).strip().lower()
        password = data.get("password","")
        user = User.query.filter_by(email=email).first()
        if not user or not bcrypt.check_password_hash(user.password_hash, password):
            return jsonify({"error":"Invalid email or password"}), 401
        login_user(user, remember=True)
        return jsonify({"ok":True, "username": user.username})
    return render_template("auth.html", mode="login")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/api/me")
@login_required
def me():
    return jsonify(current_user.to_dict())


# ── Main app ──────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html",
        sessions=SESSIONS, setup_tags=SETUP_TAGS,
        instruments=list(INSTRUMENT_PIP.keys()),
        ai_available=sent.groq_available(),
        username=current_user.username)

@app.route("/api/trades", methods=["GET"])
@login_required
def list_trades():
    return jsonify(_get_trades())

@app.route("/api/trades", methods=["POST"])
@login_required
def create_trade():
    payload = request.get_json(force=True)
    payload = _compute_derived(payload)
    s = sent.analyze(payload.get("notes",""))
    payload.update({
        "sentiment_label": s["label"], "sentiment_score": s["score"],
        "sentiment_summary": s["summary"], "sentiment_phrases": s["phrases"],
        "sentiment_source": s["source"],
        "emotions": sorted(set(payload.get("emotions") or []) | set(s.get("emotions") or [])),
    })
    t = _save_trade(payload)
    db.session.commit()
    return jsonify({"id": t.id, "sentiment": s, "trade": t.to_dict()}), 201

@app.route("/api/trades/<int:trade_id>", methods=["DELETE"])
@login_required
def delete_trade(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=uid()).first_or_404()
    db.session.delete(t); db.session.commit()
    return jsonify({"deleted": trade_id})

@app.route("/api/trades/<int:trade_id>", methods=["PUT"])
@login_required
def update_trade(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=uid()).first_or_404()
    payload = request.get_json(force=True)
    payload = _compute_derived(payload)
    if payload.get("notes") != t.notes:
        s = sent.analyze(payload.get("notes",""))
        payload.update({
            "sentiment_label": s["label"], "sentiment_score": s["score"],
            "sentiment_summary": s["summary"], "sentiment_phrases": s["phrases"],
            "sentiment_source": s["source"],
            "emotions": sorted(set(s.get("emotions") or [])),
        })
    else:
        payload["sentiment_label"]   = t.sentiment_label
        payload["sentiment_score"]   = t.sentiment_score
        payload["sentiment_summary"] = t.sentiment_summary
        payload["sentiment_phrases"] = json.loads(t.sentiment_phrases or "[]")
        payload["sentiment_source"]  = t.sentiment_source
        payload["emotions"]          = json.loads(t.emotions or "[]")
    for field in ["trade_date","entry_time","exit_time","duration_minutes","instrument",
                  "session","direction","lots","contracts","entry_price","stop_price",
                  "target_price","exit_price","stop_pips","target_pips","dollar_risk",
                  "planned_risk_usd","planned_rr","realized_pnl","realized_r","commission","order_type"]:
        if field in payload: setattr(t, field, payload[field])
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

@app.route("/api/trades/<int:trade_id>/sentiment", methods=["POST"])
@login_required
def retry_sentiment(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=uid()).first_or_404()
    s = sent.analyze(t.notes or "")
    t.sentiment_label   = s["label"]; t.sentiment_score   = s["score"]
    t.sentiment_summary = s["summary"]; t.sentiment_phrases = json.dumps(s["phrases"])
    t.sentiment_source  = s["source"]
    t.emotions = json.dumps(sorted(set(json.loads(t.emotions or "[]")) | set(s.get("emotions") or [])))
    db.session.commit()
    return jsonify(s)

@app.route("/api/trades/<int:trade_id>/image", methods=["POST"])
@login_required
def upload_trade_image(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=uid()).first_or_404()
    if "image" not in request.files:
        return jsonify({"error":"No image provided"}), 400
    file = request.files["image"]
    if not file.filename or not _allowed(file.filename):
        return jsonify({"error":"Invalid file type"}), 400
    try:
        url = _upload_image(file)
        t.image_url = url; db.session.commit()
        return jsonify({"image_url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/trades/<int:trade_id>/image", methods=["DELETE"])
@login_required
def delete_trade_image(trade_id):
    t = Trade.query.filter_by(id=trade_id, user_id=uid()).first_or_404()
    if t.image_url and t.image_url.startswith("/static/uploads/"):
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), t.image_url.lstrip("/"))
        if os.path.exists(local): os.remove(local)
    t.image_url = None; db.session.commit()
    return jsonify({"deleted": True})

@app.route("/api/metrics")
@login_required
def metrics_endpoint():
    return jsonify(kpi.compute_all(_get_trades(), _get_settings()))

@app.route("/api/settings", methods=["GET","POST"])
@login_required
def settings_endpoint():
    s = Settings.query.filter_by(user_id=uid()).first()
    if not s:
        s = Settings(user_id=uid()); db.session.add(s); db.session.commit()
    if request.method == "POST":
        body = request.get_json(force=True)
        for k,v in body.items():
            if hasattr(s,k): setattr(s,k,v)
        db.session.commit()
    return jsonify(s.to_dict())

@app.route("/api/import", methods=["POST"])
@login_required
def import_file():
    if "file" not in request.files:
        return jsonify({"error":"No file provided"}), 400
    f = request.files["file"]
    result = imp.import_file(f.read(), f.filename)
    imported = skipped = 0
    errors = result["errors"]
    for td in result["trades"]:
        try:
            td = _compute_derived(td)
            if td.get("notes"):
                s = sent.analyze(td["notes"])
                td.update({"sentiment_label":s["label"],"sentiment_score":s["score"],
                           "sentiment_summary":s["summary"],"sentiment_phrases":s["phrases"],
                           "sentiment_source":s["source"],"emotions":sorted(set(s.get("emotions") or []))})
            else:
                td["sentiment_source"] = "none"
            _save_trade(td); imported += 1
        except Exception as e:
            errors.append(str(e)); skipped += 1
    db.session.commit()
    log = ImportLog(user_id=uid(), filename=f.filename, format_detected=result["format"],
                    trades_imported=imported, trades_skipped=skipped, errors=json.dumps(errors[:20]))
    db.session.add(log); db.session.commit()
    return jsonify({"imported":imported,"skipped":skipped,"format":result["format"],"errors":errors[:5]})

@app.route("/api/sentiment", methods=["POST"])
@login_required
def sentiment_endpoint():
    return jsonify(sent.analyze(request.get_json(force=True).get("text","")))

@app.route("/api/engine_status")
@login_required
def engine_status():
    return jsonify({"groq":sent.groq_available(),"offline_ready":sent.offline_ready(),"status":sent.engine_status()})

if __name__ == "__main__":
    print(f"\n  TradeJournal  →  http://127.0.0.1:5000")
    print(f"  AI sentiment: {'ON (Groq)' if sent.groq_available() else 'OFF'}")
    print(f"  DB: {app.config['SQLALCHEMY_DATABASE_URI'][:60]}\n")
    app.run(debug=True, port=5000)
