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
    image_url           = db.Column(db.String(500))  # local path or Cloudinary URL

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
            "image_url": self.image_url,
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


class User(db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(150), unique=True, nullable=False, index=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active     = db.Column(db.Boolean, default=True)
    reset_token        = db.Column(db.String(100), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    idea_notifications = db.Column(db.Boolean, default=True)
    notif_token        = db.Column(db.String(100), nullable=True)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Flask-Login required
    def get_id(self):         return str(self.id)
    def is_authenticated(self): return True
    def is_anonymous(self):     return False

    def to_dict(self):
        return {"id": self.id, "email": self.email, "username": self.username}


class BlogPost(db.Model):
    __tablename__ = "blog_posts"
    id          = db.Column(db.Integer, primary_key=True)
    author_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title       = db.Column(db.String(200), nullable=False)
    slug        = db.Column(db.String(220), unique=True, nullable=False)
    excerpt     = db.Column(db.Text)
    content     = db.Column(db.Text, nullable=False)
    tag         = db.Column(db.String(50), default="Chart Update")
    published   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    comments    = db.relationship("PostComment", backref="post", lazy=True, cascade="all,delete")
    likes       = db.relationship("PostLike",    backref="post", lazy=True, cascade="all,delete")
    def to_dict(self, user_id=None):
        return {"id":self.id,"title":self.title,"slug":self.slug,"excerpt":self.excerpt,"content":self.content,"tag":self.tag,"created_at":self.created_at.strftime("%Y-%m-%d"),"likes":sum(1 for l in self.likes if l.is_like),"dislikes":sum(1 for l in self.likes if not l.is_like),"liked":any(l.user_id==user_id and l.is_like for l in self.likes) if user_id else False,"disliked":any(l.user_id==user_id and not l.is_like for l in self.likes) if user_id else False,"comment_count":len(self.comments),"author":"TradeJournal"}

class TradeIdea(db.Model):
    __tablename__ = "trade_ideas"
    id          = db.Column(db.Integer, primary_key=True)
    author_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title       = db.Column(db.String(200), nullable=False)
    instrument  = db.Column(db.String(20), nullable=False)
    direction   = db.Column(db.String(10), nullable=False)
    content     = db.Column(db.Text, nullable=False)
    image_url   = db.Column(db.String(500))
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    comments    = db.relationship("IdeaComment", backref="idea", lazy=True, cascade="all,delete")
    likes       = db.relationship("IdeaLike",    backref="idea", lazy=True, cascade="all,delete")
    def to_dict(self, user_id=None):
        from models import User
        author = User.query.get(self.author_id)
        return {"id":self.id,"title":self.title,"instrument":self.instrument,"direction":self.direction,"content":self.content,"image_url":self.image_url,"created_at":self.created_at.strftime("%Y-%m-%d %H:%M"),"author":author.username if author else "Unknown","likes":sum(1 for l in self.likes if l.is_like),"dislikes":sum(1 for l in self.likes if not l.is_like),"liked":any(l.user_id==user_id and l.is_like for l in self.likes) if user_id else False,"disliked":any(l.user_id==user_id and not l.is_like for l in self.likes) if user_id else False,"comment_count":len(self.comments)}

class PostComment(db.Model):
    __tablename__ = "post_comments"
    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey("blog_posts.id"), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    def to_dict(self):
        from models import User
        u = User.query.get(self.user_id)
        return {"id":self.id,"content":self.content,"author":u.username if u else "?","created_at":self.created_at.strftime("%Y-%m-%d %H:%M")}

class PostLike(db.Model):
    __tablename__ = "post_likes"
    id      = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("blog_posts.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    is_like = db.Column(db.Boolean, default=True)
    __table_args__ = (db.UniqueConstraint("post_id","user_id"),)

class IdeaComment(db.Model):
    __tablename__ = "idea_comments"
    id       = db.Column(db.Integer, primary_key=True)
    idea_id  = db.Column(db.Integer, db.ForeignKey("trade_ideas.id"), nullable=False)
    user_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content  = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    def to_dict(self):
        from models import User
        u = User.query.get(self.user_id)
        return {"id":self.id,"content":self.content,"author":u.username if u else "?","created_at":self.created_at.strftime("%Y-%m-%d %H:%M")}

class IdeaLike(db.Model):
    __tablename__ = "idea_likes"
    id      = db.Column(db.Integer, primary_key=True)
    idea_id = db.Column(db.Integer, db.ForeignKey("trade_ideas.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    is_like = db.Column(db.Boolean, default=True)
    __table_args__ = (db.UniqueConstraint("idea_id","user_id"),)


# ── Chart Replay / Backtest cache ─────────────────────────────────────────────

class ChartCandle(db.Model):
    """One OHLCV bar per symbol+timeframe+timestamp.  All timestamps are UTC ms."""
    __tablename__ = "chart_candles"
    id        = db.Column(db.Integer, primary_key=True)
    symbol    = db.Column(db.String(20), nullable=False, index=True)
    timeframe = db.Column(db.String(10), nullable=False)
    ts        = db.Column(db.BigInteger, nullable=False)   # epoch ms UTC
    open      = db.Column(db.Float)
    high      = db.Column(db.Float)
    low       = db.Column(db.Float)
    close     = db.Column(db.Float)
    volume    = db.Column(db.Float, default=0.0)
    __table_args__ = (db.UniqueConstraint("symbol", "timeframe", "ts", name="uq_candle"),)


class ChartMeta(db.Model):
    """Tracks when we last fetched data for a symbol+timeframe so we know when to refresh."""
    __tablename__ = "chart_meta"
    id           = db.Column(db.Integer, primary_key=True)
    symbol       = db.Column(db.String(20), nullable=False)
    timeframe    = db.Column(db.String(10), nullable=False)
    last_fetched = db.Column(db.DateTime)
    candle_count = db.Column(db.Integer, default=0)
    __table_args__ = (db.UniqueConstraint("symbol", "timeframe", name="uq_meta"),)
