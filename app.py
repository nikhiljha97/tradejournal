"""
TradeJournal — Flask app with auth, multi-tenancy, Cloudinary image storage.
"""
import os, json, uuid, re, requests as http_requests
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, Response
from sqlalchemy import text
from blog_posts import POSTS, get_post
import resend
import secrets
from datetime import datetime, timedelta, timezone
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from flask_compress import Compress

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import Config, INSTRUMENT_PIP, SESSIONS, SETUP_TAGS
from models import db, Trade, Settings, ImportLog, User, BlogPost, TradeIdea, PostComment, PostLike, IdeaComment, IdeaLike, ChartCandle, ChartMeta, BacktestSession, BacktestTrade
import metrics as kpi
import sentiment as sent
import importer as imp

app = Flask(__name__)
app.config.from_object(Config)
app.config["COMPRESS_MIMETYPES"] = [
    "text/html", "text/css", "application/javascript",
    "application/json", "application/xml", "text/plain"
]
app.config["COMPRESS_LEVEL"] = 6
app.config["COMPRESS_MIN_SIZE"] = 500
CORS(app)
Compress(app)
db.init_app(app)
bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = ""

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── Canonical domain redirect ─────────────────────────────────────────────────
_CANONICAL = "https://backtesting-journalmytrades.com"
_OLD_HOST  = "tradejournal-n3hn.onrender.com"

@app.before_request
def redirect_to_canonical():
    if request.host == _OLD_HOST:
        return redirect(_CANONICAL + request.full_path.rstrip("?"), 301)


# ── Email validation helpers ──────────────────────────────────────────────────
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
_DISPOSABLE_DOMAINS = {
    "mailinator.com","guerrillamail.com","tempmail.com","throwam.com","yopmail.com",
    "sharklasers.com","guerrillamailblock.com","grr.la","guerrillamail.info",
    "spam4.me","trashmail.com","trashmail.me","trashmail.net","dispostable.com",
    "maildrop.cc","mailnull.com","spamgourmet.com","spamgourmet.net",
    "getairmail.com","filzmail.com","throwam.com","fakeinbox.com","mailnesia.com",
    "mailnull.com","spamevader.com","spamevader.net","discard.email",
    "crazymailing.com","discardmail.com","discardmail.de","spamfree24.org",
    "tempr.email","tempm.com","zzrgg.com","mt2015.com","mt2016.com","mt2017.com",
    "emkei.cz","spamwc.de","spamwc.ga","spamwc.gq","spamwc.ml","spamwc.cf",
}

def _validate_email(email: str):
    """Returns (ok: bool, error: str|None)."""
    if not _EMAIL_RE.match(email):
        return False, "Invalid email address format"
    domain = email.split("@")[-1].lower()
    if domain in _DISPOSABLE_DOMAINS:
        return False, "Disposable email addresses are not allowed"
    return True, None

# ── Mail config ───────────────────────────────────────────────────────────────
_MAIL_FROM = "TradeJournal <noreply@backtesting-journalmytrades.com>"
_SITE_URL  = "https://backtesting-journalmytrades.com"

def _send_email(to: str, subject: str, html: str, text: str = ""):
    """Send a multipart HTML+text email via Resend API."""
    resend.api_key = os.environ.get("RESEND_API_KEY", "")
    payload = {
        "from": _MAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    resend.Emails.send(payload)

def _email_wrap(body_html: str) -> str:
    """Wrap email body in a standard light-background shell (better deliverability)."""
    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>body{margin:0;padding:0;background:#f4f4f5;font-family:Arial,sans-serif}'
        '.wrap{max-width:520px;margin:32px auto;background:#ffffff;border-radius:10px;'
        'overflow:hidden;border:1px solid #e0e0e0}'
        '.hdr{background:#111827;padding:20px 28px}'
        '.logo{font-size:17px;font-weight:900;color:#ffffff;letter-spacing:-.01em}'
        '.logo span{color:#00c97a}'
        '.body{padding:32px 28px;color:#1a1a1a;font-size:15px;line-height:1.7}'
        '.btn{display:inline-block;background:#00a86b;color:#ffffff;font-weight:700;'
        'font-size:15px;padding:12px 28px;border-radius:8px;text-decoration:none;margin:8px 0}'
        '.ftr{background:#f9fafb;padding:16px 28px;text-align:center;border-top:1px solid #e0e0e0;'
        'font-size:11px;color:#6b7280}'
        '.ftr a{color:#6b7280}'
        'h2{margin:0 0 12px;font-size:20px;color:#111827}'
        'p{margin:0 0 16px;color:#374151}'
        '.muted{color:#6b7280;font-size:12px}'
        '</style></head><body>'
        '<div class="wrap">'
        '<div class="hdr"><div class="logo">Trade<span>·</span>Journal</div></div>'
        '<div class="body">' + body_html + '</div>'
        '<div class="ftr">TradeJournal &mdash; backtesting-journalmytrades.com<br>'
        '<a href="https://backtesting-journalmytrades.com">Visit site</a></div>'
        '</div></body></html>'
    )

def _send_verification_email(user_email: str, token: str):
    verify_url = f"{_SITE_URL}/verify-email/{token}"
    html = _email_wrap(
        f'<h2>Confirm your email</h2>'
        f'<p>Click the button below to verify your TradeJournal account. This link expires in 24 hours.</p>'
        f'<a href="{verify_url}" class="btn">Verify Email</a>'
        f'<p class="muted" style="margin-top:24px">If you did not create a TradeJournal account, you can safely ignore this email.</p>'
    )
    text = (
        f"TradeJournal — Confirm your email\n\n"
        f"Click the link below to verify your account (expires in 24 hours):\n{verify_url}\n\n"
        f"If you did not sign up, ignore this email.\n\n"
        f"— TradeJournal\nhttps://backtesting-journalmytrades.com"
    )
    _send_email(user_email, "Verify your TradeJournal email", html, text)

# ── Security headers ──────────────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # HSTS — only enforce over HTTPS (Render always serves HTTPS in production)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

with app.app_context():
    db.create_all()
    # Add notification columns if they don't exist
    with db.engine.connect() as conn:
        for col, typ in [("idea_notifications","BOOLEAN DEFAULT TRUE"),("notif_token","VARCHAR(100)")]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {typ}"))
                conn.commit()
            except: pass
        # Add reset token columns if they don't exist
    try:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token VARCHAR(100)"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expiry TIMESTAMP"))
            conn.commit()
    except Exception as e:
        print(f"Migration note: {e}")
    # Add email verification columns if they don't exist
    try:
        with db.engine.connect() as conn:
            # Add columns (IF NOT EXISTS is safe to run multiple times)
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token VARCHAR(100) UNIQUE"))
            # Mark all pre-existing users as verified so they aren't locked out
            conn.execute(text("UPDATE users SET email_verified = TRUE WHERE email_verified = FALSE AND created_at < NOW() - INTERVAL '1 minute'"))
            conn.commit()
    except Exception as e:
        print(f"Migration note (email_verified): {e}")

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
    inst_up     = instrument.upper()
    lots        = _num(data.get("lots"))
    entry       = _num(data.get("entry_price"))
    stop        = _num(data.get("stop_price"))
    target      = _num(data.get("target_price"))
    exit_price  = _num(data.get("exit_price"))
    direction   = data.get("direction","Long")
    dollar_risk = _num(data.get("dollar_risk"))
    stop_pips   = _num(data.get("stop_pips"))
    target_pips = _num(data.get("target_pips"))

    # Pip value per lot lookup ($ per 1 pip movement per standard lot)
    # pip_val_per_lot = pip_value / pip_size
    # XAUUSD: pip = 0.01, $10/pip/lot → price_unit_value = 10/0.01 = $1000/lot
    # Forex:  pip = 0.0001, $10/pip/lot → price_unit_value = 10/0.0001 = $100,000/lot (×lots = standard)
    # MT5 P&L formula: price_distance × contract_size × lots
    # contract_size = oz/lot for metals, units/lot for forex
    # Verified against broker: XAUUSD contract_size=100, P&L=price_diff×100×lots
    CONTRACT_SIZE = {
        "XAUUSD": 100,    # 100 oz/lot  ← verified from broker MT5 properties
        "XAGUSD": 100,    # 100 oz/lot
        "EURUSD": 100000, "GBPUSD": 100000, "AUDUSD": 100000,
        "NZDUSD": 100000, "USDCAD": 100000, "USDCHF": 100000,
        "USDJPY": 100000, "EURGBP": 100000, "EURJPY": 100000, "GBPJPY": 100000,
        "BTCUSD": 1, "ETHUSD": 1,
        "US30": 1, "US500": 1, "NAS100": 1, "UK100": 1, "GER40": 1,
    }
    # For forex: P&L in quote currency, convert to USD
    # For XAUUSD/metals: P&L directly in USD
    # pip_size used only for pip-mode risk entry
    PIP_SIZE = {
        "XAUUSD": 0.01, "XAGUSD": 0.01,
        "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01,
    }
    contract_size = CONTRACT_SIZE.get(inst_up, 100000)  # default forex
    pip_size_val  = PIP_SIZE.get(inst_up, 0.0001)

    def _dollar_move(price_diff, n_lots):
        return abs(price_diff) * contract_size * n_lots

    planned_risk = dollar_risk
    if planned_risk is None and stop_pips is not None and lots:
        # pip-mode: convert pips to price units then to dollars
        planned_risk = abs(stop_pips) * pip_size_val * contract_size * lots
    if planned_risk is None and entry is not None and stop is not None and lots:
        planned_risk = _dollar_move(entry - stop, lots)

    planned_rr = None
    if target_pips is not None and stop_pips and stop_pips != 0:
        planned_rr = abs(target_pips / stop_pips)
    elif entry is not None and stop is not None and target is not None and entry != stop:
        planned_rr = abs(target - entry) / abs(entry - stop)

    realized_pnl = _num(data.get("realized_pnl"))
    if realized_pnl is None and exit_price is not None and entry is not None and lots:
        sign = 1 if direction == "Long" else -1
        realized_pnl = sign * (exit_price - entry) * contract_size * lots

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
    # Convert empty strings to None for all fields
    data = {k: (None if v == "" else v) for k, v in data.items()}
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
RECAPTCHA_SECRET = "6LfTT0gtAAAAABIxnN5icIPPzZb1h2OuTfGYiD7O"

def _verify_recaptcha(token):
    try:
        r = http_requests.post("https://www.google.com/recaptcha/api/siteverify",
            data={"secret": RECAPTCHA_SECRET, "response": token}, timeout=5)
        return r.json().get("success", False)
    except Exception:
        return False

@app.route("/register", methods=["GET","POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        data = request.get_json(force=True)
        if not _verify_recaptcha(data.get("recaptcha","")):
            return jsonify({"error": "reCAPTCHA verification failed. Please try again."}), 400
        email    = (data.get("email","")).strip().lower()
        username = (data.get("username","")).strip()
        password = data.get("password","")
        if not email or not username or not password:
            return jsonify({"error":"All fields required"}), 400
        ok, err = _validate_email(email)
        if not ok:
            return jsonify({"error": err}), 400
        if len(password) < 8:
            return jsonify({"error":"Password must be at least 8 characters"}), 400
        if User.query.filter_by(email=email).first():
            return jsonify({"error":"Email already registered"}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({"error":"Username taken"}), 400
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        token = secrets.token_urlsafe(32)
        user = User(email=email, username=username, password_hash=pw_hash,
                    email_verified=False, verification_token=token)
        db.session.add(user)
        db.session.flush()
        # Create default settings for new user (guard against duplicate)
        if not Settings.query.filter_by(user_id=user.id).first():
            db.session.add(Settings(user_id=user.id))
        db.session.commit()
        email_sent = True
        try:
            _send_verification_email(email, token)
        except Exception as e:
            print(f"Verification email error: {e}")
            email_sent = False
        if not email_sent:
            # Resend domain not yet verified — auto-verify user so they can log in
            user.email_verified = True
            user.verification_token = None
            db.session.commit()
            login_user(user)
            return jsonify({"ok": True, "verify": False})
        return jsonify({"ok": True, "verify": True})
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET","POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        data = request.get_json(force=True)
        if not _verify_recaptcha(data.get("recaptcha","")):
            return jsonify({"error": "reCAPTCHA verification failed. Please try again."}), 400
        email    = (data.get("email","")).strip().lower()
        password = data.get("password","")
        user = User.query.filter_by(email=email).first()
        if not user or not bcrypt.check_password_hash(user.password_hash, password):
            return jsonify({"error":"Invalid email or password"}), 401
        if not user.email_verified:
            return jsonify({"error":"Please verify your email before signing in.", "unverified": True}), 403
        login_user(user, remember=True)
        return jsonify({"ok":True, "username": user.username})
    return render_template("auth.html", mode="login")

@app.route("/verify-email/<token>")
def verify_email(token):
    user = User.query.filter_by(verification_token=token).first()
    if not user:
        return render_template("auth.html", mode="login",
                               flash_error="Verification link is invalid or has already been used.")
    user.email_verified = True
    user.verification_token = None
    db.session.commit()
    login_user(user, remember=True)
    return redirect(url_for("index"))

@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    data  = request.get_json(force=True)
    email = (data.get("email", "")).strip().lower()
    user  = User.query.filter_by(email=email).first()
    if user and not user.email_verified:
        token = secrets.token_urlsafe(32)
        user.verification_token = token
        db.session.commit()
        try:
            _send_verification_email(email, token)
        except Exception as e:
            print(f"Resend verification error: {e}")
    # Always return ok to avoid email enumeration
    return jsonify({"ok": True})

@app.route("/api/prices")
def public_prices():
    """Public price feed for landing page ticker — uses GoldAPI + exchange rate fallbacks."""
    import urllib.request, json as _json
    prices = {}
    try:
        # XAUUSD via GoldAPI
        req = urllib.request.Request(
            "https://www.goldapi.io/api/XAU/USD",
            headers={"x-access-token": "goldapi-61098d6b50e88976c2ce53472d03098e-io", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            d = _json.loads(resp.read())
            p = d.get("price", 0)
            prev = d.get("prev_close_price", p)
            chg = ((p - prev) / prev * 100) if prev else 0
            prices["XAUUSD"] = {"price": f"{p:,.2f}", "change_pct": f"{chg:+.2f}%"}
    except Exception:
        prices["XAUUSD"] = {"price": "—", "change_pct": ""}

    # Static fallbacks for other pairs (could expand with more APIs later)
    static = {
        "EURUSD": "1.0821", "GBPUSD": "1.2734", "USDJPY": "157.83",
        "BTCUSD": "67,420", "NAS100": "19,847", "US30": "39,215",
        "XAGUSD": "29.41", "ETHUSD": "3,521", "DXY": "104.21",
    }
    for sym, val in static.items():
        if sym not in prices:
            prices[sym] = {"price": val, "change_pct": ""}

    return jsonify(prices)

@app.route("/blog")
def blog_index():
    return render_template("blog.html", posts=POSTS, post=None)

@app.route("/blog/<slug>")
def blog_post(slug):
    post = get_post(slug)
    if not post:
        return redirect(url_for("blog_index"))
    related = [p for p in POSTS if p["slug"] != slug][:3]
    return render_template("blog.html", post=post, posts=None, related=related)

@app.route("/economic-calendar")
def economic_calendar():
    return render_template("forex_calendar.html")

@app.route("/api/calendar-ai", methods=["POST"])
def calendar_ai():
    """Public AI chat for the forex economic calendar page."""
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()[:500]
    history  = (data.get("history") or [])[-4:]   # keep last 4 turns for context

    if not user_msg:
        return jsonify({"reply": "Please enter a question.", "blog": None}), 200

    from blog_posts import POSTS
    import json as _json

    blog_index = "\n".join(
        f'- slug:{p["slug"]} | "{p["title"]}" | {p["excerpt"][:100]}'
        for p in POSTS
    )

    system = f"""You are an economic calendar assistant embedded on TradeJournal (backtesting-journalmytrades.com), a free AI trading journal for SMC and ICT forex/gold traders.

Answer questions about forex economic events concisely and accurately. Assume the user is an active retail trader focused on XAUUSD, forex majors, or futures.

TOPIC COVERAGE:
- What each event means (NFP, FOMC, CPI, PPI, PCE, GDP, PMI, JOLTS, ADP, retail sales, interest rate decisions, etc.)
- Which pairs/assets each event impacts and the causal mechanism (USD correlation, real rates, safe-haven flows, risk sentiment)
- Typical pip ranges and volatility windows for high-impact releases
- Gold (XAUUSD) reaction logic: real interest rates, DXY inverse correlation, safe-haven demand
- Prop firm risk management around news events (stop widening, position closure, daily loss limit risk)
- ICT methodology: news as a liquidity event, post-news displacement, kill zones
- Session timing: London open, New York open, overlap windows

RESPONSE FORMAT — return ONLY a JSON object, no text outside it:
{{
  "reply": "Your answer. Short paragraphs only, no markdown headers or bullet lists using dashes. Keep under 160 words.",
  "blog": null
}}

If the question is directly answered or meaningfully extended by one of the TradeJournal blog posts below, set "blog" to:
{{"title": "Exact post title", "url": "/blog/post-slug", "reason": "One sentence, under 12 words, why this helps."}}
Otherwise keep "blog": null.

BLOG POSTS AVAILABLE:
{blog_index}

RULES:
- No financial advice — explain mechanisms and facts only
- Never invent data or pip figures you are not confident about
- Return ONLY valid JSON — nothing else"""

    messages = [{"role": "system", "content": system}]
    for turn in history:
        role    = turn.get("role")
        content = (turn.get("content") or "")[:300]
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_msg})

    try:
        from groq import Groq
        client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=450,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=messages,
        )
        raw    = resp.choices[0].message.content.strip()
        parsed = _json.loads(raw)
        return jsonify({
            "reply": (parsed.get("reply") or "").strip(),
            "blog":  parsed.get("blog") or None,
        })
    except Exception as e:
        return jsonify({
            "reply": "The calendar assistant is temporarily unavailable. Please try again in a moment.",
            "blog": None,
        }), 200


@app.route("/sitemap.xml")
def sitemap():
    # Use real dates for static pages so Google trusts lastmod.
    # Only truly dynamic pages (home, blog index, community) get today's date.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from blog_posts import POSTS
    blog_urls = "\n".join(
        f'  <url><loc>https://backtesting-journalmytrades.com/blog/{p["slug"]}</loc>'
        f'<lastmod>{p.get("date", today)}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>'
        for p in POSTS
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://backtesting-journalmytrades.com/</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>
  <url><loc>https://backtesting-journalmytrades.com/blog</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>0.9</priority></url>
  <url><loc>https://backtesting-journalmytrades.com/economic-calendar</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.8</priority></url>
{blog_urls}
</urlset>""", 200, {"Content-Type": "application/xml"}
    return xml

@app.route("/ads.txt")
def ads_txt():
    return "google.com, pub-2562826047096065, DIRECT, f08c47fec0942fa0", 200, {"Content-Type": "text/plain"}

@app.route("/robots.txt")
def robots_txt():
    return """# AI crawlers — explicitly allowed to index public content
User-agent: GPTBot
Allow: /
Disallow: /api/
Disallow: /login
Disallow: /register
Disallow: /logout
Disallow: /settings
Disallow: /import
Disallow: /backtest
Disallow: /ideas
Disallow: /reset-password/
Disallow: /verify-email/
Disallow: /resend-verification
Disallow: /forgot-password

User-agent: OAI-SearchBot
Allow: /
Disallow: /api/

User-agent: ClaudeBot
Allow: /
Disallow: /api/

User-agent: anthropic-ai
Allow: /
Disallow: /api/

User-agent: PerplexityBot
Allow: /
Disallow: /api/

User-agent: Google-Extended
Allow: /
Disallow: /api/

User-agent: Googlebot
Allow: /
Disallow: /api/

User-agent: meta-externalagent
Allow: /
Disallow: /api/

User-agent: Applebot-Extended
Allow: /
Disallow: /api/

User-agent: Amazonbot
Allow: /
Disallow: /api/

User-agent: Bytespider
Allow: /
Disallow: /api/

User-agent: CCBot
Allow: /
Disallow: /api/

# All other bots — allow public pages, block private routes
User-agent: *
Disallow: /api/
Disallow: /login
Disallow: /register
Disallow: /logout
Disallow: /settings
Disallow: /import
Disallow: /backtest
Disallow: /blog-posts
Disallow: /ideas
Disallow: /reset-password/
Disallow: /verify-email/
Disallow: /resend-verification
Disallow: /forgot-password

Sitemap: https://backtesting-journalmytrades.com/sitemap.xml""", 200, {"Content-Type": "text/plain"}

@app.route("/favicon.ico")
def favicon_ico():
    return redirect("/static/favicon.svg", 301)

@app.route("/llms.txt")
def llms_txt():
    content = """# TradeJournal

> Free AI-powered trading journal for SMC and ICT traders. Tracks trades, reads trading psychology from notes, monitors prop firm compliance, and calculates correct P&L across gold (XAUUSD), forex pairs, futures (NQ/ES/YM), and crypto.

TradeJournal is a web application built for retail traders who use Smart Money Concepts (SMC) and Inner Circle Trader (ICT) methodology, as well as traders attempting prop firm challenges (FTMO, Funded Next, Topstep). It is free forever with no credit card required.

## Core Capabilities

- AI psychology analysis: reads trade notes, detects emotions (FOMO, revenge, patience, overconfidence), scores discipline from -1 to +1, surfaces cost-per-pattern data
- MT5 trade import via HTML export, CSV, or XLSX — pip math is instrument-aware
- Prop firm compliance dashboard: profit target %, daily loss limit, max drawdown, cooling period
- SMC/ICT setup tagging: Order Blocks, Fair Value Gaps, BOS, CHoCH, Liquidity Sweeps, Breaker Blocks, Mitigation Blocks, Inducement, OTE, Premium/Discount, Imbalance, Equal Highs/Lows, Turtle Soup, Killzones, Session Opens
- Correct contract math for all instruments: XAUUSD (100 oz/lot, $1/pip), EURUSD/GBPUSD ($10/pip), JPY pairs ($9.09/pip), NQ ($20/pt), ES ($50/pt), YM ($5/pt), BTC/ETH ($1/$ move)
- Backtest replay tool with drawing tools and session annotation
- Position size calculator: lot size from risk % and stop loss distance
- Community trade ideas board

## Blog Articles

- [How to Pass a Prop Firm Challenge in 45 Days](https://backtesting-journalmytrades.com/blog/pass-prop-firm-challenge-45-days)
- [Free Position Size Calculator: Lot Size by Risk %](https://backtesting-journalmytrades.com/blog/position-size-calculator-risk-percentage)
- [Why Trading Journals Show Wrong P&L for Gold, Forex and Futures](https://backtesting-journalmytrades.com/blog/trading-journal-pip-math-gold-forex-crypto-futures)
- [The Complete SMC Trading Journal Guide](https://backtesting-journalmytrades.com/blog/smc-trading-journal-guide)
- [XAUUSD Trading Journal for Gold Traders on MT5](https://backtesting-journalmytrades.com/blog/xauusd-trading-journal)
- [Trading Psychology Journal: Stop Revenge Trading](https://backtesting-journalmytrades.com/blog/trading-psychology-journal)
- [Best Free Trading Journal App in 2026](https://backtesting-journalmytrades.com/blog/free-trading-journal-app)
- [What to Write in a Trading Journal](https://backtesting-journalmytrades.com/blog/what-to-write-in-trading-journal)
- [How to Start a Trading Journal (Beginners)](https://backtesting-journalmytrades.com/blog/how-to-start-trading-journal)
- [TradeZella vs Edgewonk vs TraderSync Alternatives](https://backtesting-journalmytrades.com/blog/tradezella-vs-edgewonk-vs-tradersync-alternatives)
- [Prop Firm Trading Journal Guide](https://backtesting-journalmytrades.com/blog/prop-firm-trading-journal)

## Pricing

Free forever. No trial period. No credit card. No feature tiers. All functionality available from signup.

## Target Users

- Retail traders using SMC/ICT methodology (order blocks, FVGs, liquidity)
- Prop firm challenge traders on FTMO, Funded Next, Topstep, Upcomers
- XAUUSD, forex, NQ/ES futures, and crypto day traders
- Traders who want AI-driven pattern analysis from their trade notes
"""
    return Response(content, mimetype="text/plain")

@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")
    email = request.json.get("email","").strip().lower()
    user = User.query.filter_by(email=email).first()
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token = token
        user.reset_token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        db.session.commit()
        reset_url = f"{_SITE_URL}/reset-password/{token}"
        try:
            html = _email_wrap(
                f'<h2>Reset your password</h2>'
                f'<p>We received a request to reset the password for your TradeJournal account. '
                f'This link expires in 1 hour.</p>'
                f'<a href="{reset_url}" class="btn">Reset Password</a>'
                f'<p class="muted" style="margin-top:24px">If you did not request a password reset, you can safely ignore this email. '
                f'Your password will not change.</p>'
            )
            text = (
                f"TradeJournal — Reset your password\n\n"
                f"Click the link below to reset your password (expires in 1 hour):\n{reset_url}\n\n"
                f"If you did not request this, ignore this email.\n\n"
                f"— TradeJournal\nhttps://backtesting-journalmytrades.com"
            )
            _send_email(email, "Reset your TradeJournal password", html, text)
        except Exception as e:
            print(f"Email error: {e}")
    return jsonify({"ok": True})

@app.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password(token):
    if request.method == "GET":
        user = User.query.filter_by(reset_token=token).first()
        if not user or not user.reset_token_expiry:
            return redirect(url_for("login"))
        expiry = user.reset_token_expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(timezone.utc):
            return redirect(url_for("login"))
        return render_template("reset_password.html", token=token)
    data = request.json
    token_val = data.get("token","")
    password = data.get("password","")
    if len(password) < 6:
        return jsonify({"ok": False, "error": "Password must be at least 6 characters"})
    user = User.query.filter_by(reset_token=token_val).first()
    if not user or not user.reset_token_expiry:
        return jsonify({"ok": False, "error": "Invalid or expired link"})
    expiry = user.reset_token_expiry
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry < datetime.now(timezone.utc):
        return jsonify({"ok": False, "error": "Link has expired. Request a new one."})
    user.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
    user.reset_token = None
    user.reset_token_expiry = None
    db.session.commit()
    return jsonify({"ok": True})

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL","nikhiljha97@yahoo.com")
def is_admin():
    return current_user.is_authenticated and current_user.email == ADMIN_EMAIL

@app.route("/blog-posts")
def blog_posts_page():
    return render_template("community.html", section="blog")

@app.route("/ideas")
def ideas_page():
    return render_template("community.html", section="ideas")

@app.route("/api/blog-posts", methods=["GET"])
def get_blog_posts():
    posts = BlogPost.query.filter_by(published=True).order_by(BlogPost.created_at.desc()).all()
    uid = current_user.id if current_user.is_authenticated else None
    return jsonify([p.to_dict(uid) for p in posts])

@app.route("/api/blog-posts", methods=["POST"])
@login_required
def create_blog_post():
    if not is_admin(): return jsonify({"error":"Unauthorized"}),403
    d = request.json
    import re
    slug = re.sub(r"[^a-z0-9]+","-",d.get("title","").lower()).strip("-")
    slug = f"{slug}-{int(datetime.now(timezone.utc).timestamp())}"
    post = BlogPost(author_id=current_user.id,title=d.get("title",""),slug=slug,excerpt=d.get("excerpt",""),content=d.get("content",""),tag=d.get("tag","Chart Update"))
    db.session.add(post); db.session.commit()
    return jsonify(post.to_dict(current_user.id))

@app.route("/api/blog-posts/<int:pid>", methods=["DELETE"])
@login_required
def delete_blog_post(pid):
    if not is_admin(): return jsonify({"error":"Unauthorized"}),403
    post = BlogPost.query.get_or_404(pid)
    db.session.delete(post); db.session.commit()
    return jsonify({"ok":True})

@app.route("/api/blog-posts/<int:pid>/like", methods=["POST"])
@login_required
def like_blog_post(pid):
    is_like = request.json.get("is_like",True)
    existing = PostLike.query.filter_by(post_id=pid,user_id=current_user.id).first()
    if existing:
        if existing.is_like==is_like: db.session.delete(existing)
        else: existing.is_like=is_like
    else: db.session.add(PostLike(post_id=pid,user_id=current_user.id,is_like=is_like))
    db.session.commit()
    post = BlogPost.query.get(pid)
    return jsonify(post.to_dict(current_user.id))

@app.route("/api/blog-posts/<int:pid>/comments", methods=["GET"])
def get_blog_comments(pid):
    return jsonify([c.to_dict() for c in PostComment.query.filter_by(post_id=pid).order_by(PostComment.created_at.asc()).all()])

@app.route("/api/blog-posts/<int:pid>/comments", methods=["POST"])
@login_required
def add_blog_comment(pid):
    txt = request.json.get("content","").strip()
    if not txt: return jsonify({"error":"Empty"}),400
    c = PostComment(post_id=pid,user_id=current_user.id,content=txt)
    db.session.add(c); db.session.commit()
    return jsonify(c.to_dict())

@app.route("/api/blog-posts/<int:pid>/comments/<int:cid>", methods=["PUT"])
@login_required
def edit_blog_comment(pid,cid):
    c = PostComment.query.get_or_404(cid)
    if c.user_id!=current_user.id and not is_admin(): return jsonify({"error":"Unauthorized"}),403
    c.content = request.json.get("content","").strip() or c.content
    db.session.commit()
    return jsonify(c.to_dict())

@app.route("/api/blog-posts/<int:pid>/comments/<int:cid>", methods=["DELETE"])
@login_required
def delete_blog_comment(pid,cid):
    c = PostComment.query.get_or_404(cid)
    if c.user_id!=current_user.id and not is_admin(): return jsonify({"error":"Unauthorized"}),403
    db.session.delete(c); db.session.commit()
    return jsonify({"ok":True})

@app.route("/api/ideas", methods=["GET"])
def get_ideas():
    ideas = TradeIdea.query.order_by(TradeIdea.created_at.desc()).all()
    uid = current_user.id if current_user.is_authenticated else None
    return jsonify([i.to_dict(uid) for i in ideas])

@app.route("/api/ideas", methods=["POST"])
@login_required
def create_idea():
    d = request.json
    idea = TradeIdea(author_id=current_user.id,title=d.get("title",""),instrument=d.get("instrument","").upper(),direction=d.get("direction","Neutral"),content=d.get("content",""),image_url=d.get("image_url"))
    db.session.add(idea); db.session.commit()
    try:
        _send_idea_notifications(idea, current_user)
    except Exception as e:
        print(f"Notification error: {e}")
    return jsonify(idea.to_dict(current_user.id))

def _send_idea_notifications(idea, author):
    import threading
    def send():
        try:
            dir_emoji = "Bullish" if idea.direction=="Long" else "Bearish" if idea.direction=="Short" else "Neutral"
            dir_icon  = "📈" if idea.direction=="Long" else "📉" if idea.direction=="Short" else "↔"
            bg_color  = "rgba(0,229,160,.1)" if idea.direction=="Long" else "rgba(255,71,87,.1)" if idea.direction=="Short" else "rgba(90,112,128,.15)"
            txt_color = "#00e5a0" if idea.direction=="Long" else "#ff4757" if idea.direction=="Short" else "#5a7080"
            subscribers = User.query.filter_by(idea_notifications=True).all()
            for user in subscribers:
                if user.id == author.id:
                    continue
                if not user.notif_token:
                    user.notif_token = secrets.token_urlsafe(32)
                    db.session.commit()
                unsub_url = f"{_SITE_URL}/unsubscribe-ideas/{user.notif_token}"
                preview   = (idea.content or "")[:200] + ("..." if len(idea.content or "") > 200 else "")
                dir_label = "Bullish (Long)" if idea.direction=="Long" else "Bearish (Short)" if idea.direction=="Short" else "Neutral"
                badge_bg  = "#d1fae5" if idea.direction=="Long" else "#fee2e2" if idea.direction=="Short" else "#f3f4f6"
                badge_txt = "#065f46" if idea.direction=="Long" else "#991b1b" if idea.direction=="Short" else "#374151"
                html_body = _email_wrap(
                    f'<p style="font-size:11px;font-weight:700;color:#00a86b;letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px">New Trade Idea</p>'
                    f'<h2>{idea.title}</h2>'
                    f'<div style="margin-bottom:16px">'
                    f'<span style="background:#ecfdf5;color:#065f46;font-size:12px;font-weight:700;padding:3px 10px;border-radius:4px;margin-right:6px">{idea.instrument}</span>'
                    f'<span style="background:{badge_bg};color:{badge_txt};font-size:12px;font-weight:700;padding:3px 10px;border-radius:4px">{dir_icon} {dir_emoji}</span>'
                    f'</div>'
                    f'<p>{preview}</p>'
                    f'<a href="{_SITE_URL}/ideas" class="btn">View Trade Idea</a>'
                    f'<p class="muted" style="margin-top:20px">Posted by <strong>{author.username}</strong> on TradeJournal &mdash; '
                    f'<a href="{unsub_url}" style="color:#6b7280">Unsubscribe</a></p>'
                )
                text_body = (
                    f"New Trade Idea on TradeJournal\n\n"
                    f"{idea.title}\n{idea.instrument} — {dir_label}\n\n"
                    f"{preview}\n\n"
                    f"View it here: {_SITE_URL}/ideas\n\n"
                    f"Posted by {author.username}\n"
                    f"Unsubscribe: {unsub_url}"
                )
                try:
                    _send_email(user.email, f"New Trade Idea: {idea.instrument} — {dir_emoji}", html_body, text_body)
                except Exception as e:
                    print(f"Email to {user.email} failed: {e}")
        except Exception as e:
            print(f"Notification thread error: {e}")
    threading.Thread(target=send, daemon=True).start()

@app.route("/api/ideas/<int:iid>", methods=["DELETE"])
@login_required
def delete_idea(iid):
    idea = TradeIdea.query.get_or_404(iid)
    if idea.author_id!=current_user.id and not is_admin(): return jsonify({"error":"Unauthorized"}),403
    db.session.delete(idea); db.session.commit()
    return jsonify({"ok":True})

@app.route("/api/ideas/<int:iid>/like", methods=["POST"])
@login_required
def like_idea(iid):
    is_like = request.json.get("is_like",True)
    existing = IdeaLike.query.filter_by(idea_id=iid,user_id=current_user.id).first()
    if existing:
        if existing.is_like==is_like: db.session.delete(existing)
        else: existing.is_like=is_like
    else: db.session.add(IdeaLike(idea_id=iid,user_id=current_user.id,is_like=is_like))
    db.session.commit()
    idea = TradeIdea.query.get(iid)
    return jsonify(idea.to_dict(current_user.id))

@app.route("/api/ideas/<int:iid>/comments", methods=["GET"])
def get_idea_comments(iid):
    return jsonify([c.to_dict() for c in IdeaComment.query.filter_by(idea_id=iid).order_by(IdeaComment.created_at.asc()).all()])

@app.route("/api/ideas/<int:iid>/comments", methods=["POST"])
@login_required
def add_idea_comment(iid):
    txt = request.json.get("content","").strip()
    if not txt: return jsonify({"error":"Empty"}),400
    c = IdeaComment(idea_id=iid,user_id=current_user.id,content=txt)
    db.session.add(c); db.session.commit()
    return jsonify(c.to_dict())

@app.route("/api/ideas/<int:iid>/comments/<int:cid>", methods=["PUT"])
@login_required
def edit_idea_comment(iid,cid):
    c = IdeaComment.query.get_or_404(cid)
    if c.user_id!=current_user.id and not is_admin(): return jsonify({"error":"Unauthorized"}),403
    c.content = request.json.get("content","").strip() or c.content
    db.session.commit()
    return jsonify(c.to_dict())

@app.route("/api/ideas/<int:iid>/comments/<int:cid>", methods=["DELETE"])
@login_required
def delete_idea_comment(iid,cid):
    c = IdeaComment.query.get_or_404(cid)
    if c.user_id!=current_user.id and not is_admin(): return jsonify({"error":"Unauthorized"}),403
    db.session.delete(c); db.session.commit()
    return jsonify({"ok":True})

@app.route("/unsubscribe-ideas/<token>")
def unsubscribe_ideas(token):
    user = User.query.filter_by(notif_token=token).first()
    if user:
        user.idea_notifications = False
        db.session.commit()
    html = "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"/><style>body{background:#07090d;color:#d4dde8;font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}.box{background:#111820;border:1px solid #1c2b3a;border-radius:14px;padding:40px;max-width:400px;text-align:center}.icon{font-size:48px;margin-bottom:16px}.title{font-size:20px;font-weight:700;margin-bottom:8px}.sub{color:#5a7080;font-size:14px;line-height:1.6}.btn{display:inline-block;margin-top:20px;background:#00e5a0;color:#000;font-weight:700;padding:10px 24px;border-radius:8px;text-decoration:none;font-size:14px}</style></head><body><div class=\"box\"><div class=\"icon\">✅</div><div class=\"title\">Unsubscribed</div><div class=\"sub\">You won't receive trade idea notifications anymore.<br>You can re-subscribe anytime from the Trade Ideas page.</div><a href=\"/ideas\" class=\"btn\">Go to Trade Ideas</a></div></body></html>"
    return html

@app.route("/api/me/notifications", methods=["GET","POST"])
@login_required
def manage_notifications():
    if request.method == "GET":
        return jsonify({"idea_notifications": current_user.idea_notifications})
    data = request.json
    current_user.idea_notifications = data.get("idea_notifications", True)
    if not current_user.notif_token:
        current_user.notif_token = secrets.token_urlsafe(32)
    db.session.commit()
    return jsonify({"ok": True, "idea_notifications": current_user.idea_notifications})

@app.route("/api/me/is_admin")
def check_admin():
    return jsonify({"is_admin":is_admin()})

@app.route("/google18b855e2f453917d.html")
def google_verification():
    return "google-site-verification: google18b855e2f453917d.html"

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))

@app.route("/api/me")
@login_required
def me():
    return jsonify(current_user.to_dict())


# ── Main app ──────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    if current_user.is_authenticated:
        return render_template("index.html",
            sessions=SESSIONS, setup_tags=SETUP_TAGS,
            instruments=list(INSTRUMENT_PIP.keys()),
            ai_available=sent.groq_available(),
            username=current_user.username)
    return render_template("landing.html")

@app.route("/app")
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
    # Merge LLM-extracted setup tags with manually selected ones
    manual_setups = set(payload.get("setups") or [])
    llm_setups    = set(s.get("setups") or [])
    merged_setups = sorted(manual_setups | llm_setups)
    payload.update({
        "sentiment_label":  s["label"], "sentiment_score":   s["score"],
        "sentiment_summary":s["summary"], "sentiment_phrases": s["phrases"],
        "sentiment_source": s["source"],
        "emotions": sorted(set(payload.get("emotions") or []) | set(s.get("emotions") or [])),
        "setups": merged_setups,
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
    _numeric = {"duration_minutes","lots","contracts","entry_price","stop_price",
                "target_price","exit_price","stop_pips","target_pips","dollar_risk",
                "planned_risk_usd","planned_rr","realized_pnl","realized_r","commission"}
    for field in ["trade_date","entry_time","exit_time","duration_minutes","instrument",
                  "session","direction","lots","contracts","entry_price","stop_price",
                  "target_price","exit_price","stop_pips","target_pips","dollar_risk",
                  "planned_risk_usd","planned_rr","realized_pnl","realized_r","commission","order_type"]:
        if field in payload:
            val = payload[field]
            if field in _numeric and (val == "" or val is None):
                val = None
            elif field in _numeric and val is not None:
                try: val = float(val)
                except (TypeError, ValueError): val = None
            setattr(t, field, val)
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

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

if __name__ == "__main__":
    print(f"\n  TradeJournal  →  http://127.0.0.1:5000")
    print(f"  AI sentiment: {'ON (Groq)' if sent.groq_available() else 'OFF'}")
    print(f"  DB: {app.config['SQLALCHEMY_DATABASE_URI'][:60]}\n")
    app.run(debug=True, port=5000)

# ── AI Coach & Pattern Analysis ───────────────────────────────────────────────

def _trades_context(trades, limit=50):
    """Compact trade summary for LLM context."""
    recent = sorted(trades, key=lambda t: t.get('trade_date',''), reverse=True)[:limit]
    lines = []
    for t in recent:
        lines.append(
            f"{t.get('trade_date')} | {t.get('instrument')} {t.get('direction')} "
            f"| lots={t.get('lots')} | entry={t.get('entry_price')} sl={t.get('stop_price')} tp={t.get('target_price')} "
            f"| exit={t.get('exit_price')} pnl=${t.get('realized_pnl')} R={t.get('realized_r')} "
            f"| RR={t.get('planned_rr')} session={t.get('session')} "
            f"| setups={t.get('setups')} emotions={t.get('emotions')} "
            f"| sentiment={t.get('sentiment_label')} score={t.get('sentiment_score')} "
            f"| notes={str(t.get('notes',''))[:120]}"
        )
    return "\n".join(lines)


@app.route("/api/coach/insights", methods=["POST"])
@login_required
def coach_insights():
    """Generate proactive insights after a trade or on demand."""
    body = request.get_json(force=True)
    trigger = body.get("trigger", "on_demand")  # "trade_logged", "on_demand", "chat"
    user_message = body.get("message", "")
    trade_id = body.get("trade_id")

    trades = _get_trades()
    settings = _get_settings()
    if not trades:
        return jsonify({"insights": [], "reply": "Log some trades first and I'll start finding patterns."})

    ctx = _trades_context(trades)
    total_pnl = sum(t.get('realized_pnl') or 0 for t in trades if t.get('realized_pnl') is not None)
    wins  = sum(1 for t in trades if (t.get('realized_pnl') or 0) > 0)
    losses= sum(1 for t in trades if (t.get('realized_pnl') or 0) < 0)
    win_rate = round(wins/(wins+losses)*100, 1) if (wins+losses) else 0

    # Find the specific trade if triggered by logging
    trade_context = ""
    if trade_id:
        t = next((x for x in trades if x['id'] == trade_id), None)
        if t:
            trade_context = f"\nMOST RECENT TRADE JUST LOGGED: {t.get('trade_date')} {t.get('instrument')} {t.get('direction')} | P&L=${t.get('realized_pnl')} R={t.get('realized_r')} | sentiment: {t.get('sentiment_label')} | notes: {(t.get('notes') or '')[:200]}"

    system = f"""You are a brutally honest trading coach with deep knowledge of SMC/ICT methodology, prop firm rules, and trading psychology.
You have full access to this trader's journal. Your job is to find patterns they haven't noticed and tell them things they might not want to hear.

TRADER STATS:
- Total trades: {len(trades)}
- Win rate: {win_rate}%
- Total P&L: ${round(total_pnl,2)}
- Account: {settings.get('account_label','')}
- Profit target: ${settings.get('profit_target',500)}

ALL TRADES (most recent first):
{ctx}
{trade_context}

RULES:
- Be specific: cite actual trade dates, P&L amounts, patterns from the data
- Don't hallucinate trades that aren't in the data
- Prioritize insights by dollar damage
- Keep each insight concise and actionable
- For chat mode: answer the question directly using real trade data
- Trigger mode "trade_logged": give 2-3 immediate insights about this specific trade + any pattern it fits
- Trigger mode "on_demand": give top 3 most expensive patterns you see
- Always end proactive insights with one concrete rule the trader should follow"""

    if trigger == "chat" and user_message:
        prompt = f"Trader asks: {user_message}\n\nAnswer using their actual trade data. Be specific with dates, amounts, and patterns."
    elif trigger == "trade_logged":
        prompt = "A new trade was just logged. Give 2-3 immediate insights: what this trade reveals, any pattern it fits, and one thing they should do differently."
    else:
        prompt = "Scan all trades and surface the top 3 most expensive behavioral patterns. For each: name it, quantify the dollar cost, give 1-2 specific trade examples, and give one actionable rule."

    try:
        from groq import Groq
        client = Groq(api_key=os.environ.get("GROQ_API_KEY",""))
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=800,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
        )
        reply = resp.choices[0].message.content.strip()
        return jsonify({"reply": reply, "trigger": trigger})
    except Exception as e:
        return jsonify({"reply": f"Coach unavailable: {str(e)[:80]}", "trigger": trigger}), 500


@app.route("/api/coach/patterns", methods=["GET"])
@login_required
def coach_patterns():
    """Deep pattern analysis — runs on Psychology tab open."""
    trades = _get_trades()
    settings = _get_settings()
    if len(trades) < 3:
        return jsonify({"patterns": [], "message": "Log at least 3 trades to unlock pattern analysis."})

    ctx = _trades_context(trades, limit=100)
    total_pnl = sum(t.get('realized_pnl') or 0 for t in trades if t.get('realized_pnl') is not None)
    wins  = sum(1 for t in trades if (t.get('realized_pnl') or 0) > 0)
    losses= sum(1 for t in trades if (t.get('realized_pnl') or 0) < 0)

    system = f"""You are a quantitative trading coach analyzing a trader's complete journal.
Find SPECIFIC patterns backed by actual data — no generic advice.

TRADER DATA:
Trades: {len(trades)} | Wins: {wins} | Losses: {losses} | Total P&L: ${round(total_pnl,2)}
Account: {settings.get('account_label','')} | Target: ${settings.get('profit_target',500)}

ALL TRADES:
{ctx}"""

    prompt = """Analyze ALL trades and return a JSON array of max 5 patterns, sorted by dollar damage (most expensive first).

IMPORTANT ADAPTIVE RULES:
- Only include patterns with ACTUAL evidence in the current data — not generic advice
- If a pattern exists in early trades but NOT in recent trades, reduce its severity or drop it entirely (trader may have fixed it)
- Prioritize recency: a pattern that cost $100 last week beats one that cost $500 three months ago
- Never pad with low-confidence patterns just to reach 5. 2-3 strong insights beat 5 weak ones
- If sample size is too small to confirm a pattern, say so in description and set confidence below 50%

Return ONLY valid JSON, no prose, no markdown:
[
  {
    "title": "Short pattern name (≤5 words)",
    "severity": "high|medium|low",
    "cost_usd": 123.45,
    "confidence": 87,
    "description": "What the pattern is, why it costs money, whether it is improving or worsening (2 sentences)",
    "evidence": "Specific trades: dates, instruments, P&L amounts",
    "rule": "One concrete actionable rule (not generic advice)"
  }
]

Focus on: streak effects, session timing, emotion-to-loss correlation, RR discipline, setup performance, revenge trading, overtrading after losses, time-of-day patterns, partial profit taking, stop placement."""

    try:
        from groq import Groq
        client = Groq(api_key=os.environ.get("GROQ_API_KEY",""))
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=1200,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        patterns = json.loads(raw)
        return jsonify({"patterns": patterns})
    except json.JSONDecodeError:
        return jsonify({"patterns": [], "message": "Pattern analysis returned unexpected format."})
    except Exception as e:
        return jsonify({"patterns": [], "message": f"Analysis unavailable: {str(e)[:80]}"}), 500


# ── Chart Replay / Backtest ───────────────────────────────────────────────────

# Internal key → yfinance ticker
CHART_SYMBOLS = {
    "EURUSD":  "EURUSD=X",
    "XAUUSD":  "GC=F",
    "USOIL":   "CL=F",
    "NQ":      "NQ=F",
    "ES":      "ES=F",
    "YM":      "YM=F",
    "BTCUSD":  "BTC-USD",
    "ETHUSD":  "ETH-USD",
    "XRP":     "XRP-USD",
}

# Category structure for the UI dropdown selector
CHART_CATEGORIES = [
    {"cat": "Forex", "syms": [
        {"key": "EURUSD", "abbr": "EUR/USD", "name": "Euro / US Dollar"},
    ]},
    {"cat": "Futures", "syms": [
        {"key": "XAUUSD", "abbr": "GC", "name": "Gold Futures"},
        {"key": "USOIL",  "abbr": "CL", "name": "Crude Oil Futures"},
        {"key": "NQ",     "abbr": "NQ", "name": "Nasdaq Futures"},
        {"key": "ES",     "abbr": "ES", "name": "S&P 500 Futures"},
        {"key": "YM",     "abbr": "YM", "name": "Dow Jones Futures"},
    ]},
    {"cat": "Crypto", "syms": [
        {"key": "BTCUSD", "abbr": "BTC", "name": "Bitcoin"},
        {"key": "ETHUSD", "abbr": "ETH", "name": "Ethereum"},
        {"key": "XRP",    "abbr": "XRP", "name": "XRP"},
    ]},
]

# How far back to fetch per timeframe
_YF_PARAMS = {
    "15m": ("60d",  "15m"),
    "30m": ("60d",  "30m"),
    "1h":  ("730d", "1h"),
    "4h":  ("730d", "1h"),   # fetch 1h, resample → 4h
    "1d":  ("max",  "1d"),
}

# Staleness thresholds (seconds) before we refresh from yfinance
_STALE_AFTER = {
    "15m": 900,    # 15 min
    "30m": 1800,   # 30 min
    "1h":  3600,   # 1 h
    "4h":  3600,
    "1d":  86400,  # 24 h
}


def _fetch_and_cache(symbol_key: str, tf: str):
    """Pull OHLCV from yfinance and bulk-insert new candles into ChartCandle."""
    import yfinance as yf
    import pandas as pd
    import time as _time

    ticker_sym = CHART_SYMBOLS.get(symbol_key)
    if not ticker_sym:
        return

    period, interval = _YF_PARAMS[tf]

    # Retry up to 3 times with backoff — Yahoo rate-limits cloud IPs on first hit
    df = None
    for attempt in range(3):
        try:
            df = yf.Ticker(ticker_sym).history(
                period=period, interval=interval,
                auto_adjust=True, raise_errors=False)
            if df is not None and not df.empty:
                break
            df = None
        except Exception as e:
            print(f"[chart] attempt {attempt+1} failed for {symbol_key}/{tf}: {e}")
        if attempt < 2:
            _time.sleep(2 ** attempt)   # 1s, 2s

    if df is None or df.empty:
        print(f"[chart] no data returned for {symbol_key}/{tf} after retries")
        return

    # Flatten multi-level columns (newer yfinance versions)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    # Resample 1h → 4h
    if tf == "4h":
        df.index = pd.to_datetime(df.index, utc=True)
        df = (df.resample("4h")
                .agg({"Open": "first", "High": "max", "Low": "min",
                      "Close": "last", "Volume": "sum"})
                .dropna(subset=["Open", "Close"]))

    # Clamp OHLC: yfinance daily futures data occasionally has Close/Open
    # slightly outside the reported High/Low (data quality artifact).
    # Ensure High >= max(O,C) and Low <= min(O,C) for all bars.
    df["High"]  = df[["High",  "Open", "Close"]].max(axis=1)
    df["Low"]   = df[["Low",   "Open", "Close"]].min(axis=1)

    # ── Bulk upsert ───────────────────────────────────────────────────────────
    # Retry once on OperationalError (stale SSL connection dropped between
    # pool_pre_ping and actual query — common on Neon/Render cold starts).
    from sqlalchemy.exc import OperationalError as _OpErr
    for _db_attempt in range(2):
        try:
            # One query to get ALL existing timestamps — avoids 13k individual SELECTs
            existing_ts = {
                row[0] for row in db.session.execute(
                    text("SELECT ts FROM chart_candles WHERE symbol=:s AND timeframe=:t"),
                    {"s": symbol_key, "t": tf}
                ).fetchall()
            }

            new_objs = []
            for ts_idx, row in df.iterrows():
                try:
                    ts_ms = int(pd.Timestamp(ts_idx).timestamp() * 1000)
                except Exception:
                    continue
                if ts_ms in existing_ts:
                    continue
                try:
                    new_objs.append(ChartCandle(
                        symbol=symbol_key, timeframe=tf, ts=ts_ms,
                        open=float(row["Open"]),  high=float(row["High"]),
                        low=float(row["Low"]),    close=float(row["Close"]),
                        volume=float(row.get("Volume", 0) or 0),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue

            if new_objs:
                db.session.add_all(new_objs)   # single bulk INSERT

            # Update meta
            meta = ChartMeta.query.filter_by(symbol=symbol_key, timeframe=tf).first()
            if not meta:
                meta = ChartMeta(symbol=symbol_key, timeframe=tf)
                db.session.add(meta)
            meta.last_fetched = datetime.utcnow()
            db.session.commit()
            print(f"[chart] {symbol_key}/{tf}: +{len(new_objs)} new candles")
            break  # success

        except _OpErr as db_err:
            db.session.remove()   # discard stale connection back to pool
            if _db_attempt == 1:
                print(f"[chart] DB error after retry for {symbol_key}/{tf}: {db_err}")
                raise
            print(f"[chart] DB connection dropped, retrying ({symbol_key}/{tf})…")
            _time.sleep(1)


def _background_fetch(app_ctx, symbol_key: str, tf: str):
    """Run _fetch_and_cache in a thread with its own app context."""
    with app_ctx.app_context():
        try:
            _fetch_and_cache(symbol_key, tf)
        except Exception as e:
            print(f"[chart] background fetch error {symbol_key}/{tf}: {e}")


@app.route("/backtest")
@login_required
def backtest_page():
    return render_template("backtest.html", categories=CHART_CATEGORIES, username=current_user.username)


@app.route("/api/chart-data")
@login_required
def chart_data():
    import threading as _threading
    symbol = request.args.get("symbol", "XAUUSD").upper()
    tf     = request.args.get("tf", "1h").lower()

    if symbol not in CHART_SYMBOLS:
        return jsonify({"error": "Unknown symbol"}), 400
    if tf not in _YF_PARAMS:
        return jsonify({"error": "Unknown timeframe"}), 400

    # Check cache
    meta = ChartMeta.query.filter_by(symbol=symbol, timeframe=tf).first()
    cached_count = ChartCandle.query.filter_by(symbol=symbol, timeframe=tf).count()
    stale = True
    if meta and meta.last_fetched:
        age = (datetime.utcnow() - meta.last_fetched).total_seconds()
        stale = age > _STALE_AFTER[tf]

    if cached_count == 0:
        # No data at all: fetch synchronously so the user gets something
        # (bulk ops are fast enough to stay within the 120s timeout)
        _fetch_and_cache(symbol, tf)
    elif stale:
        # Data exists but is stale: return cache immediately, refresh in background
        _threading.Thread(
            target=_background_fetch,
            args=(app, symbol, tf),
            daemon=True
        ).start()

    candles = (ChartCandle.query
               .filter_by(symbol=symbol, timeframe=tf)
               .order_by(ChartCandle.ts.asc())
               .all())

    data = [{"t": c.ts, "o": round(c.open, 5), "h": round(c.high, 5),
             "l": round(c.low, 5),  "c": round(c.close, 5),
             "v": round(c.volume, 2)} for c in candles]

    return jsonify({"symbol": symbol, "tf": tf, "candles": data,
                    "count": len(data), "stale": stale})


# ── Backtest sessions ─────────────────────────────────────────────────────────

def _bt_db(fn):
    """Run fn() with up to 3 retries + exponential backoff on Neon cold-start SSL drops."""
    import time as _time
    from sqlalchemy.exc import OperationalError as _SqlOpErr
    for attempt in range(3):
        try:
            return fn()
        except _SqlOpErr as e:
            db.session.remove()
            if attempt == 2:
                raise
            wait = 2 ** attempt   # 1s then 2s
            print(f"[bt_db] SSL/connection error attempt {attempt+1}, retrying in {wait}s: {e}")
            _time.sleep(wait)


@app.route("/api/backtest/sessions", methods=["GET"])
@login_required
def bt_list_sessions():
    def _q():
        sessions = (BacktestSession.query
                    .filter_by(user_id=current_user.id)
                    .order_by(BacktestSession.created_at.desc())
                    .limit(20).all())
        return jsonify([s.to_dict() for s in sessions])
    return _bt_db(_q)


@app.route("/api/backtest/sessions", methods=["POST"])
@login_required
def bt_create_session():
    data = request.json or {}
    def _q():
        s = BacktestSession(
            user_id=current_user.id,
            name=data.get("name") or f"Session {datetime.now(timezone.utc).strftime('%b %d %H:%M')}",
            symbol=data.get("symbol", "XAUUSD"),
            timeframe=data.get("timeframe", "1h"),
            start_date=data.get("start_date"),
        )
        db.session.add(s)
        db.session.commit()
        return jsonify(s.to_dict()), 201
    return _bt_db(_q)


@app.route("/api/backtest/sessions/<int:sid>", methods=["GET"])
@login_required
def bt_get_session(sid):
    def _q():
        s = BacktestSession.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        return jsonify(s.to_dict(include_trades=True))
    return _bt_db(_q)


@app.route("/api/backtest/sessions/<int:sid>", methods=["DELETE"])
@login_required
def bt_delete_session(sid):
    def _q():
        s = BacktestSession.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        db.session.delete(s)
        db.session.commit()
        return jsonify({"ok": True})
    return _bt_db(_q)


@app.route("/api/backtest/sessions/<int:sid>/trades", methods=["POST"])
@login_required
def bt_add_trade(sid):
    data = request.json or {}
    def _q():
        BacktestSession.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        otype = data.get("order_type", "market")
        t = BacktestTrade(
            session_id=sid,
            direction=data["direction"],
            order_type=otype,
            lots=float(data.get("lots", 0.01)),
            trigger_price=data.get("trigger_price"),
            entry_price=data.get("entry_price"),
            entry_ts=data.get("entry_ts"),
            tp_price=data.get("tp_price"),
            sl_price=data.get("sl_price"),
            status="open" if otype == "market" else "pending",
            pnl_usd=0.0,
        )
        db.session.add(t)
        db.session.commit()
        return jsonify(t.to_dict()), 201
    return _bt_db(_q)


@app.route("/api/backtest/sessions/<int:sid>/trades/<int:tid>", methods=["PUT"])
@login_required
def bt_update_trade(sid, tid):
    data = request.json or {}
    def _q():
        BacktestSession.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        t = BacktestTrade.query.filter_by(id=tid, session_id=sid).first_or_404()
        for field in ["exit_price", "exit_ts", "exit_reason", "pnl_usd",
                      "status", "entry_price", "entry_ts", "tp_price", "sl_price"]:
            if field in data:
                setattr(t, field, data[field])
        db.session.commit()
        return jsonify(t.to_dict())
    return _bt_db(_q)


@app.route("/api/backtest/sessions/<int:sid>/trades/<int:tid>", methods=["DELETE"])
@login_required
def bt_delete_trade(sid, tid):
    def _q():
        BacktestSession.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        t = BacktestTrade.query.filter_by(id=tid, session_id=sid).first_or_404()
        db.session.delete(t)
        db.session.commit()
        return jsonify({"ok": True})
    return _bt_db(_q)


@app.route("/backtest/results/<int:sid>")
@login_required
def backtest_results_page(sid):
    s = BacktestSession.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
    return render_template("backtest_results.html", session_id=s.id, username=current_user.username)
