from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
import json

db = SQLAlchemy()

class Trade(db.Model):
    __tablename__ = "trades"

    id                  = db.Column(db.Integer, primary_key=True)
    user_id             = db.Column(db.Integer, default=1, index=True)  # multi-user ready

    # Timing
    trade_date          = db.Column(db.String(10), nullable=False, index=True)
    entry_time          = db.Column(db.String(8))
    exit_time           = db.Column(db.String(8))
    duration_minutes    = db.Column(db.Float)

    # Instrument & direction
    instrument          = db.Column(db.String(20), nullable=False, index=True)
    session             = db.Column(db.String(20))
    direction           = db.Column(db.String(5))   # Long / Short

    # Sizing
    lots                = db.Column(db.Float)
    contracts           = db.Column(db.Float)       # futures fallback

    # Prices
    entry_price         = db.Column(db.Float)
    stop_price          = db.Column(db.Float)
    target_price        = db.Column(db.Float)
    exit_price          = db.Column(db.Float)

    # Risk inputs (both modes)
    stop_pips           = db.Column(db.Float)       # pip-mode
    target_pips         = db.Column(db.Float)
    dollar_risk         = db.Column(db.Float)       # direct-mode

    # Computed on save
    planned_risk_usd    = db.Column(db.Float)
    planned_rr          = db.Column(db.Float)
    realized_pnl        = db.Column(db.Float)
    realized_r          = db.Column(db.Float)
    commission          = db.Column(db.Float, default=0.0)

    # Metadata
    order_type          = db.Column(db.String(30))  # MARKET / PROTECTIVE_STOP etc
    setups              = db.Column(db.Text, default="[]")   # JSON list
    notes               = db.Column(db.Text)
    import_source       = db.Column(db.String(50))   # "manual" / "csv" / "mt5" / "pdf"

    # Sentiment — all LLM derived
    emotions            = db.Column(db.Text, default="[]")   # JSON list
    sentiment_label     = db.Column(db.String(100))
    sentiment_score     = db.Column(db.Float)        # -1 to 1
    sentiment_summary   = db.Column(db.Text)
    sentiment_phrases   = db.Column(db.Text, default="[]")  # JSON: [{phrase, emotion}]
    sentiment_source    = db.Column(db.String(20))   # "llm" / "pending" / "none"

    created_at          = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "trade_date": self.trade_date,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "duration_minutes": self.duration_minutes,
            "instrument": self.instrument,
            "session": self.session,
            "direction": self.direction,
            "lots": self.lots,
            "contracts": self.contracts,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "exit_price": self.exit_price,
            "stop_pips": self.stop_pips,
            "target_pips": self.target_pips,
            "dollar_risk": self.dollar_risk,
            "planned_risk_usd": self.planned_risk_usd,
            "planned_rr": self.planned_rr,
            "realized_pnl": self.realized_pnl,
            "realized_r": self.realized_r,
            "commission": self.commission,
            "order_type": self.order_type,
            "setups": json.loads(self.setups or "[]"),
            "notes": self.notes,
            "import_source": self.import_source,
            "emotions": json.loads(self.emotions or "[]"),
            "sentiment_label": self.sentiment_label,
            "sentiment_score": self.sentiment_score,
            "sentiment_summary": self.sentiment_summary,
            "sentiment_phrases": json.loads(self.sentiment_phrases or "[]"),
            "sentiment_source": self.sentiment_source,
        }


class Settings(db.Model):
    __tablename__ = "settings"
    id                  = db.Column(db.Integer, primary_key=True)
    user_id             = db.Column(db.Integer, default=1, unique=True, index=True)
    account_label       = db.Column(db.String(100), default="My Eval Account")
    starting_balance    = db.Column(db.Float, default=10000)
    profit_target       = db.Column(db.Float, default=500)
    daily_loss_limit    = db.Column(db.Float, default=500)
    max_drawdown        = db.Column(db.Float, default=1000)
    max_contracts       = db.Column(db.Float, default=3)
    consistency_pct     = db.Column(db.Float, default=30)
    updated_at          = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "account_label": self.account_label,
            "starting_balance": self.starting_balance,
            "profit_target": self.profit_target,
            "daily_loss_limit": self.daily_loss_limit,
            "max_drawdown": self.max_drawdown,
            "max_contracts": self.max_contracts,
            "consistency_pct": self.consistency_pct,
        }


class ImportLog(db.Model):
    __tablename__ = "import_logs"
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, default=1)
    filename        = db.Column(db.String(200))
    format_detected = db.Column(db.String(50))
    trades_imported = db.Column(db.Integer, default=0)
    trades_skipped  = db.Column(db.Integer, default=0)
    errors          = db.Column(db.Text)
    imported_at     = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
