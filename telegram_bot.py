"""
TradeJournal Telegram Bot
=========================
Handles all incoming Telegram webhook updates.

Commands
--------
/start <token>   Link your TradeJournal account to Telegram
/help            Show available commands
/log <trade>     Quickly log a trade  e.g. /log XAUUSD Long 2650 SL:2640 TP:2680
/today           P&L summary for today
/balance         Current account balance vs. targets
/ideas           Latest 3 trade ideas from the community
/disconnect      Unlink your Telegram account

Channel push
------------
When TELEGRAM_CHANNEL_ID is set, new trade ideas are also broadcast to that channel.
"""

import os, re, json
from datetime import datetime, timezone, date

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SITE_URL   = os.environ.get("SITE_URL", "https://backtesting-journalmytrades.com")

# ── Telegram HTTP helpers ──────────────────────────────────────────────────────

def _tg(method: str, payload: dict):
    """Low-level call to the Telegram Bot API."""
    if not BOT_TOKEN:
        return
    import urllib.request, urllib.error
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"Telegram API error ({method}): {e}")

def send_message(chat_id: int, text: str, parse_mode: str = "HTML", reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _tg("sendMessage", payload)

def push_to_channel(text: str, parse_mode: str = "HTML"):
    """Broadcast a message to the configured Telegram channel (if any)."""
    ch = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    if not ch:
        return
    _tg("sendMessage", {"chat_id": ch, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": False})

# ── Trade parser ───────────────────────────────────────────────────────────────

_DIRECTION_MAP = {
    "long": "Long", "buy": "Long", "bull": "Long",
    "short": "Short", "sell": "Short", "bear": "Short",
}

def _parse_trade(text: str):
    """
    Parse a trade from free-form text.

    Accepted formats (case-insensitive):
      /log XAUUSD Long 2650 SL:2640 TP:2680
      /log EURUSD short 1.0850 1.0870 1.0800
      /log NQ Long 19500 sl 19300 tp 20000 notes about the trade

    Returns a dict ready to pass to the Trade model, or raises ValueError.
    """
    # Strip command prefix
    text = re.sub(r"^/log\s*", "", text.strip(), flags=re.IGNORECASE).strip()

    tokens = text.split()
    if len(tokens) < 3:
        raise ValueError("Need at least: INSTRUMENT DIRECTION ENTRY_PRICE")

    instrument = tokens[0].upper()
    direction_raw = tokens[1].lower()
    direction = _DIRECTION_MAP.get(direction_raw)
    if not direction:
        raise ValueError(f"Unknown direction '{tokens[1]}'. Use Long or Short.")

    # Find prices: look for SL: TP: labels or positional
    entry = stop = target = None
    notes_tokens = []
    i = 2
    while i < len(tokens):
        t = tokens[i].lower()
        val_str = re.sub(r"[^0-9.]", "", tokens[i])
        if t.startswith("sl:") or t.startswith("sl=") or t == "sl":
            raw = tokens[i].split(":")[-1].split("=")[-1]
            if not raw and i+1 < len(tokens):
                i += 1; raw = tokens[i]
            stop = float(raw)
        elif t.startswith("tp:") or t.startswith("tp=") or t == "tp":
            raw = tokens[i].split(":")[-1].split("=")[-1]
            if not raw and i+1 < len(tokens):
                i += 1; raw = tokens[i]
            target = float(raw)
        elif val_str and entry is None:
            entry = float(val_str)
        elif val_str and stop is None:
            stop = float(val_str)
        elif val_str and target is None:
            target = float(val_str)
        else:
            notes_tokens.append(tokens[i])
        i += 1

    if entry is None:
        raise ValueError("Could not parse entry price.")

    return {
        "instrument": instrument,
        "direction": direction,
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "notes": " ".join(notes_tokens) or None,
    }

# ── Command handlers ───────────────────────────────────────────────────────────

def _fmt_pnl(v):
    if v is None: return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:,.2f}"

def handle_start(chat_id: int, token: str | None, app):
    """Link a TradeJournal account via a one-time token."""
    from models import User
    with app.app_context():
        if not token:
            send_message(chat_id,
                "Welcome to <b>TradeJournal Bot</b>!\n\n"
                "To link your account, go to your TradeJournal profile settings and tap <b>Connect Telegram</b>.\n\n"
                "You'll get a link that completes the setup automatically.")
            return
        user = User.query.filter_by(telegram_link_token=token).first()
        if not user:
            send_message(chat_id, "That link has expired or was already used. Generate a new one from your TradeJournal settings.")
            return
        # Link the account
        user.telegram_chat_id = chat_id
        user.telegram_link_token = None
        from models import db
        db.session.commit()
        send_message(chat_id,
            f"Linked! Your TradeJournal account (<b>{user.username}</b>) is now connected.\n\n"
            "Send /help to see what you can do.")

def handle_help(chat_id: int):
    send_message(chat_id,
        "<b>TradeJournal Bot Commands</b>\n\n"
        "/log INSTRUMENT DIRECTION ENTRY [SL:xxx] [TP:xxx] [notes]\n"
        "  Quickly log a trade entry\n"
        "  Example: <code>/log XAUUSD Long 2650 SL:2640 TP:2680</code>\n\n"
        "/today  — Today's P&amp;L summary\n"
        "/balance  — Account balance vs. targets\n"
        "/ideas  — Latest 3 community trade ideas\n"
        "/disconnect  — Unlink your Telegram account\n"
        "/help  — Show this message\n\n"
        f"<a href='{SITE_URL}'>Open TradeJournal</a>")

def handle_log(chat_id: int, text: str, app):
    """Parse and save a trade from the /log command."""
    from models import User, Trade, db
    with app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            send_message(chat_id, "Your Telegram account is not linked yet. Go to TradeJournal settings and tap <b>Connect Telegram</b>.")
            return
        try:
            fields = _parse_trade(text)
        except ValueError as e:
            send_message(chat_id,
                f"Could not parse that trade: {e}\n\n"
                "Format: <code>/log XAUUSD Long 2650 SL:2640 TP:2680</code>")
            return

        trade = Trade(
            user_id       = user.id,
            trade_date    = date.today().strftime("%Y-%m-%d"),
            instrument    = fields["instrument"],
            direction     = fields["direction"],
            entry_price   = fields["entry_price"],
            stop_price    = fields.get("stop_price"),
            target_price  = fields.get("target_price"),
            notes         = fields.get("notes"),
            import_source = "telegram",
        )
        # Compute planned R:R if both SL and TP provided
        if trade.stop_price and trade.target_price and trade.entry_price:
            risk   = abs(trade.entry_price - trade.stop_price)
            reward = abs(trade.target_price - trade.entry_price)
            if risk:
                trade.planned_rr = round(reward / risk, 2)

        db.session.add(trade)
        db.session.commit()

        rr_str = f" | R:R {trade.planned_rr}" if trade.planned_rr else ""
        sl_str = f" | SL: {trade.stop_price}" if trade.stop_price else ""
        tp_str = f" | TP: {trade.target_price}" if trade.target_price else ""
        send_message(chat_id,
            f"Trade logged!\n\n"
            f"<b>{trade.instrument}</b> {trade.direction} @ {trade.entry_price}"
            f"{sl_str}{tp_str}{rr_str}\n\n"
            f"<a href='{SITE_URL}'>View in journal</a>")

def handle_today(chat_id: int, app):
    from models import User, Trade, db
    from sqlalchemy import func
    with app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            send_message(chat_id, "Link your account first. Go to TradeJournal settings and tap <b>Connect Telegram</b>.")
            return
        today = date.today().strftime("%Y-%m-%d")
        trades = Trade.query.filter_by(user_id=user.id, trade_date=today).all()
        if not trades:
            send_message(chat_id, f"No trades logged today ({today}) yet.\n\nLog one: <code>/log XAUUSD Long 2650 SL:2640 TP:2680</code>")
            return
        closed = [t for t in trades if t.realized_pnl is not None]
        daily_pnl = sum(t.realized_pnl for t in closed)
        wins  = sum(1 for t in closed if t.realized_pnl > 0)
        losses= sum(1 for t in closed if t.realized_pnl <= 0)
        lines = [f"<b>Today ({today})</b>"]
        for t in trades:
            status = _fmt_pnl(t.realized_pnl) if t.realized_pnl is not None else "Open"
            lines.append(f"  {t.instrument} {t.direction} @ {t.entry_price} — {status}")
        if closed:
            lines.append(f"\nTotal P&amp;L: <b>{_fmt_pnl(daily_pnl)}</b>  |  {wins}W / {losses}L")
        send_message(chat_id, "\n".join(lines))

def handle_balance(chat_id: int, app):
    from models import User, Trade, Settings, db
    with app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            send_message(chat_id, "Link your account first. Go to TradeJournal settings and tap <b>Connect Telegram</b>.")
            return
        settings = Settings.query.filter_by(user_id=user.id).first()
        if not settings:
            send_message(chat_id, "No account settings found. Set your starting balance on TradeJournal first.")
            return
        trades = Trade.query.filter_by(user_id=user.id).all()
        total_pnl = sum(t.realized_pnl for t in trades if t.realized_pnl is not None)
        balance   = settings.starting_balance + total_pnl
        progress  = (total_pnl / settings.profit_target * 100) if settings.profit_target else 0
        drawdown  = min(0, total_pnl)
        send_message(chat_id,
            f"<b>Account Balance</b>\n\n"
            f"Balance: <b>${balance:,.2f}</b>\n"
            f"Total P&amp;L: {_fmt_pnl(total_pnl)}\n"
            f"Profit target: ${settings.profit_target:,.0f} ({max(0,progress):.1f}% reached)\n"
            f"Max drawdown: ${settings.max_drawdown:,.0f} | Used: {_fmt_pnl(drawdown)}\n\n"
            f"<a href='{SITE_URL}'>Open dashboard</a>")

def handle_ideas(chat_id: int, app):
    from models import TradeIdea, User
    with app.app_context():
        ideas = TradeIdea.query.order_by(TradeIdea.created_at.desc()).limit(3).all()
        if not ideas:
            send_message(chat_id, f"No trade ideas posted yet.\n\n<a href='{SITE_URL}/ideas'>Browse ideas</a>")
            return
        lines = ["<b>Latest Trade Ideas</b>\n"]
        for idea in ideas:
            slug     = re.sub(r"[^a-z0-9]+", "-", idea.title.lower()).strip("-")
            url      = f"{SITE_URL}/ideas/{idea.id}/{slug}"
            dir_icon = "Long" if idea.direction == "Long" else "Short" if idea.direction == "Short" else "Neutral"
            lines.append(f"<b>{idea.instrument}</b> {dir_icon} — <a href='{url}'>{idea.title[:60]}</a>")
        send_message(chat_id, "\n".join(lines))

def handle_disconnect(chat_id: int, app):
    from models import User, db
    with app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            send_message(chat_id, "No account is linked to this Telegram chat.")
            return
        user.telegram_chat_id = None
        db.session.commit()
        send_message(chat_id, "Your Telegram account has been unlinked from TradeJournal.")

# ── Main dispatcher ────────────────────────────────────────────────────────────

def dispatch(update: dict, app):
    """Route an incoming Telegram update to the right handler."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text    = (msg.get("text") or "").strip()
    if not text:
        return

    if text.lower().startswith("/start"):
        parts = text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else None
        handle_start(chat_id, token, app)
    elif text.lower().startswith("/log"):
        handle_log(chat_id, text, app)
    elif text.lower().startswith("/today"):
        handle_today(chat_id, app)
    elif text.lower().startswith("/balance"):
        handle_balance(chat_id, app)
    elif text.lower().startswith("/ideas"):
        handle_ideas(chat_id, app)
    elif text.lower().startswith("/disconnect"):
        handle_disconnect(chat_id, app)
    elif text.lower().startswith("/help") or text.startswith("/"):
        handle_help(chat_id)
    else:
        # Free-text: try parsing as a trade entry
        if any(d in text.lower().split() for d in _DIRECTION_MAP):
            handle_log(chat_id, "/log " + text, app)
        else:
            handle_help(chat_id)

# ── Channel broadcast (called from app.py on new idea) ────────────────────────

def broadcast_idea(idea, author_username: str, ai_summary: dict | None = None):
    """Push a new trade idea to the Telegram channel (if TELEGRAM_CHANNEL_ID is set)."""
    if not os.environ.get("TELEGRAM_CHANNEL_ID", "").strip():
        return
    slug     = re.sub(r"[^a-z0-9]+", "-", idea.title.lower()).strip("-")
    post_url = f"{SITE_URL}/ideas/{idea.id}/{slug}"
    dir_word = "Long" if idea.direction == "Long" else "Short" if idea.direction == "Short" else "Neutral"

    lines = [
        f"New Trade Idea",
        f"",
        f"<b>{idea.instrument}</b> | {dir_word}",
        f"<b>{idea.title}</b>",
        f"",
    ]
    if ai_summary:
        lines += [
            f"Bias: <b>{ai_summary.get('bias','N/A')}</b>",
            f"Entry: <b>{ai_summary.get('entry','N/A')}</b>  |  TP: <b>{ai_summary.get('tp','N/A')}</b>  |  SL: <b>{ai_summary.get('sl','N/A')}</b>",
            f"Invalidation: {ai_summary.get('invalidation','N/A')}",
            f"Confidence: <b>{ai_summary.get('confidence','N/A')}</b>",
            f"",
        ]
    lines += [
        f"Posted by @{author_username}",
        f"<a href='{post_url}'>Read full analysis</a>",
    ]
    push_to_channel("\n".join(lines))
