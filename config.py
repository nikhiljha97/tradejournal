import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    # Database — swap DATABASE_URL to your Neon/Postgres URL for production
    # Neon free tier: https://neon.tech → create project → copy connection string
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///journal_dev.db"          # local fallback for development
    ).replace("postgres://", "postgresql://")  # fix Render/Neon legacy prefix
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Neon closes idle connections after ~5 min.
    # pool_pre_ping tests the connection before use and reconnects if closed.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 180,      # recycle every 3 min (Neon closes idle at ~5 min)
        "pool_size": 5,
        "max_overflow": 2,
        "pool_timeout": 30,
        "connect_args": {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,    # send keepalive after 30s idle
            "keepalives_interval": 5, # retry every 5s
            "keepalives_count": 3,    # drop connection after 3 failed keepalives
        },
    }

    # Anthropic — required for LLM sentiment
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

    # App
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024   # 16MB upload limit
    CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "")

    # Prop-firm defaults (overridable per-user in settings)
    DEFAULT_ACCOUNT_SIZE     = 10000
    DEFAULT_DAILY_LOSS_LIMIT = 500
    DEFAULT_MAX_DRAWDOWN     = 1000
    DEFAULT_MAX_CONTRACTS    = 3
    DEFAULT_CONSISTENCY_PCT  = 30
    DEFAULT_PROFIT_TARGET    = 500

# Instrument pip values — USD per pip per 1 standard lot
# Dollar risk = lots × pip_distance × pip_value
INSTRUMENT_PIP = {
    # Forex majors / minors
    "EURUSD": 10.0, "GBPUSD": 10.0, "AUDUSD": 10.0, "NZDUSD": 10.0,
    "USDCAD": 10.0, "USDCHF": 10.0, "USDJPY": 9.09,
    "EURGBP": 10.0, "EURJPY": 9.09, "GBPJPY": 9.09,
    # Metals
    "XAUUSD": 10.0,   # Gold: 1 pip = $0.10/0.01lot → $10/lot
    "XAGUSD": 50.0,   # Silver
    # Indices (point values)
    "US30":   1.0,    "US500": 10.0,  "NAS100": 1.0,
    "UK100":  1.0,    "GER40": 1.0,
    # Crypto CFDs
    "BTCUSD": 1.0,    "ETHUSD": 1.0,
    # Micro futures (for fallback manual)
    "MGC": 10.0, "MES": 5.0, "MNQ": 2.0,
}

SESSIONS = ["Asia", "London", "New York", "Overlap", "Other"]

SETUP_TAGS = [
    "Liquidity Sweep", "Order Block", "Fair Value Gap", "BOS", "CHoCH",
    "Breaker", "Mitigation Block", "Inducement", "Premium/Discount",
    "Imbalance", "Equal Highs/Lows", "Turtle Soup", "OTE",
    "Session Open", "NY Killzone", "London Killzone",
]
