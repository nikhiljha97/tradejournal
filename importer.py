"""
Universal trade importer.
Handles: MT5 HTML statement, Upcomers CSV, generic broker CSV/XLSX, PDF tables.
Auto-detects format. Returns list of normalized trade dicts ready for DB insert.
"""
import io, re, json
from datetime import datetime, timedelta
from dateutil import parser as dateparser
import pandas as pd

try:
    import pdfplumber
    PDF_OK = True
except ImportError:
    PDF_OK = False

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────
def import_file(file_bytes: bytes, filename: str) -> dict:
    """
    Returns {trades: [...], format: str, skipped: int, errors: [str]}
    """
    name = filename.lower()
    if name.endswith(".pdf"):
        return _import_pdf(file_bytes)
    if name.endswith(".html") or name.endswith(".htm"):
        return _import_mt5_html(file_bytes)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return _import_excel(file_bytes)
    if name.endswith(".csv"):
        return _import_csv(file_bytes)
    return {"trades": [], "format": "unknown", "skipped": 0,
            "errors": [f"Unsupported file type: {filename}"]}


# ─────────────────────────────────────────────
# MT5 HTML statement
# ─────────────────────────────────────────────
def _import_mt5_html(file_bytes):
    if not BS4_OK:
        return {"trades": [], "format": "mt5_html", "skipped": 0,
                "errors": ["beautifulsoup4 not installed"]}
    soup = BeautifulSoup(file_bytes, "lxml")
    tables = soup.find_all("table")
    rows = []
    for tbl in tables:
        headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if any(k in " ".join(headers) for k in ["deal", "order", "symbol", "profit"]):
            for tr in tbl.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if cells:
                    rows.append(dict(zip(headers, cells)))
    trades = [_normalize_mt5_row(r) for r in rows if r.get("symbol")]
    trades = [t for t in trades if t]
    return {"trades": trades, "format": "mt5_html", "skipped": 0, "errors": []}


def _normalize_mt5_row(r):
    try:
        symbol = r.get("symbol", "").upper().replace(".C", "").replace("_", "")
        pnl_raw = r.get("profit", r.get("p&l", "0")).replace(",", "")
        pnl = float(pnl_raw) if pnl_raw and pnl_raw not in ["-", ""] else None
        if pnl is None:
            return None

        lots = _safe_float(r.get("volume", r.get("lots", r.get("size", ""))))
        entry_price = _safe_float(r.get("price", r.get("open price", "")))
        exit_price = _safe_float(r.get("s/l", r.get("close price", r.get("exit", ""))))
        direction = "Long" if r.get("type", "").lower() in ["buy", "in"] else "Short"

        entry_dt = _parse_dt(r.get("time", r.get("open time", r.get("entry time", ""))))
        exit_dt = _parse_dt(r.get("time", r.get("close time", r.get("exit time", ""))))

        trade_date = entry_dt.strftime("%Y-%m-%d") if entry_dt else None
        entry_time = entry_dt.strftime("%H:%M:%S") if entry_dt else None
        exit_time_str = exit_dt.strftime("%H:%M:%S") if exit_dt else None

        dur = None
        if entry_dt and exit_dt and exit_dt > entry_dt:
            dur = (exit_dt - entry_dt).total_seconds() / 60

        order_type = r.get("type", r.get("order type", "MARKET")).upper()

        return {
            "instrument": symbol, "direction": direction,
            "lots": lots, "entry_price": entry_price, "exit_price": exit_price,
            "realized_pnl": pnl, "order_type": order_type,
            "trade_date": trade_date, "entry_time": entry_time,
            "exit_time": exit_time_str, "duration_minutes": dur,
            "import_source": "mt5",
        }
    except Exception:
        return None


# ─────────────────────────────────────────────
# CSV / XLSX (generic + Upcomers)
# ─────────────────────────────────────────────
COLUMN_ALIASES = {
    "instrument": ["symbol", "pair", "instrument", "market", "ticker"],
    "direction":  ["type", "direction", "side", "action", "trade type"],
    "lots":       ["volume", "lots", "lot size", "size", "amount"],
    "entry_price":["entry price", "open price", "entry", "open", "price"],
    "exit_price": ["exit price", "close price", "exit", "close"],
    "stop_price": ["stop", "stop loss", "sl", "s/l"],
    "target_price":["take profit", "target", "tp", "t/p"],
    "realized_pnl":["profit", "p&l", "pnl", "net profit", "realized p&l", "net p/l", "realized pnl"],
    "trade_date": ["date", "trade date", "open date", "entry date", "day"],
    "entry_time": ["entry time", "open time", "time", "entry_time"],
    "exit_time":  ["exit time", "close time", "exit_time"],
    "commission": ["commission", "fee", "swap", "fees"],
    "notes":      ["notes", "comment", "comments", "remarks", "note"],
    "order_type": ["order type", "type", "order"],
    "session":    ["session"],
}

def _map_columns(df):
    """Remap df columns using aliases. Returns new df with canonical names."""
    col_lower = {c.lower().strip(): c for c in df.columns}
    rename = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in col_lower and canonical not in rename.values():
                rename[col_lower[alias]] = canonical
                break
    return df.rename(columns=rename)


def _import_csv(file_bytes):
    errors = []
    for enc in ["utf-8", "latin-1", "cp1252"]:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc)
            break
        except Exception as e:
            errors.append(str(e))
    else:
        return {"trades": [], "format": "csv", "skipped": 0, "errors": errors}
    return _process_df(df, "csv")


def _import_excel(file_bytes):
    try:
        df = pd.read_excel(io.BytesIO(file_bytes))
        return _process_df(df, "xlsx")
    except Exception as e:
        return {"trades": [], "format": "xlsx", "skipped": 0, "errors": [str(e)]}


def _process_df(df, fmt):
    df = _map_columns(df)
    df.columns = [c.lower().strip() for c in df.columns]

    trades, skipped, errors = [], 0, []
    for _, row in df.iterrows():
        try:
            t = _normalize_df_row(row, fmt)
            if t:
                trades.append(t)
            else:
                skipped += 1
        except Exception as e:
            errors.append(str(e))
            skipped += 1

    # MT5-style: pair market entries with their protective stops
    trades = _pair_mt5_stops(trades)
    return {"trades": trades, "format": fmt, "skipped": skipped, "errors": errors[:20]}


def _normalize_df_row(row, fmt):
    pnl = _safe_float(row.get("realized_pnl", ""))
    instrument = str(row.get("instrument", "")).upper().strip().replace(".C", "")
    if not instrument or instrument in ["NAN", ""]:
        return None

    direction_raw = str(row.get("direction", "")).strip().lower()
    direction = "Long" if direction_raw in ["buy", "long", "b", "1"] else (
                "Short" if direction_raw in ["sell", "short", "s", "-1"] else "Long")

    lots = _safe_float(row.get("lots", ""))
    entry_price = _safe_float(row.get("entry_price", ""))
    exit_price  = _safe_float(row.get("exit_price", ""))
    stop_price  = _safe_float(row.get("stop_price", ""))
    target_price= _safe_float(row.get("target_price", ""))
    commission  = _safe_float(row.get("commission", "")) or 0.0

    # Parse date/time
    date_raw = str(row.get("trade_date", "")).strip()
    entry_time_raw = str(row.get("entry_time", "")).strip()
    exit_time_raw  = str(row.get("exit_time", "")).strip()

    entry_dt = _parse_dt(f"{date_raw} {entry_time_raw}".strip() or date_raw)
    exit_dt  = _parse_dt(exit_time_raw) if exit_time_raw and exit_time_raw != "nan" else None

    trade_date = entry_dt.strftime("%Y-%m-%d") if entry_dt else (date_raw[:10] if date_raw else None)
    entry_time = entry_dt.strftime("%H:%M:%S") if entry_dt else None
    exit_time  = exit_dt.strftime("%H:%M:%S")  if exit_dt  else None

    dur = None
    if entry_dt and exit_dt and exit_dt > entry_dt:
        dur = (exit_dt - entry_dt).total_seconds() / 60

    order_type = str(row.get("order_type", "MARKET")).upper().strip()
    notes      = str(row.get("notes", "")).strip()
    session    = str(row.get("session", "")).strip()

    return {
        "instrument": instrument, "direction": direction,
        "lots": lots, "entry_price": entry_price, "exit_price": exit_price,
        "stop_price": stop_price, "target_price": target_price,
        "realized_pnl": pnl, "commission": commission,
        "trade_date": trade_date, "entry_time": entry_time,
        "exit_time": exit_time, "duration_minutes": dur,
        "order_type": order_type, "notes": notes if notes != "nan" else None,
        "session": session if session != "nan" else None,
        "import_source": fmt,
    }


def _pair_mt5_stops(trades):
    """
    MT5 exports MARKET orders and PROTECTIVE_STOP orders separately.
    Merge protective stops back into their parent market order as stop_price.
    """
    market = [t for t in trades if t.get("order_type") not in ["PROTECTIVE_STOP", "STOP_LOSS"]]
    stops  = [t for t in trades if t.get("order_type") in ["PROTECTIVE_STOP", "STOP_LOSS"]]
    if not stops:
        return market
    for stop in stops:
        # Match by instrument + closest trade_date
        for t in market:
            if (t["instrument"] == stop["instrument"] and
                    t["trade_date"] == stop["trade_date"] and
                    t.get("stop_price") is None):
                t["stop_price"] = stop["entry_price"]
                t["realized_pnl"] = (t.get("realized_pnl") or 0) + (stop.get("realized_pnl") or 0)
                break
    return market


# ─────────────────────────────────────────────
# PDF importer
# ─────────────────────────────────────────────
def _import_pdf(file_bytes):
    if not PDF_OK:
        return {"trades": [], "format": "pdf", "skipped": 0,
                "errors": ["pdfplumber not installed"]}
    try:
        all_rows = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue
                    headers = [str(c).lower().strip() if c else "" for c in table[0]]
                    for row in table[1:]:
                        if row:
                            all_rows.append(dict(zip(headers, row)))
        if not all_rows:
            return {"trades": [], "format": "pdf", "skipped": 0,
                    "errors": ["No tables found in PDF"]}
        df = pd.DataFrame(all_rows)
        return _process_df(df, "pdf")
    except Exception as e:
        return {"trades": [], "format": "pdf", "skipped": 0, "errors": [str(e)]}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _safe_float(v):
    if v is None:
        return None
    s = str(v).replace(",", "").replace("$", "").replace(" ", "").strip()
    if s in ["", "nan", "None", "-", "N/A"]:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_dt(s):
    if not s or str(s).strip() in ["", "nan", "None"]:
        return None
    try:
        return dateparser.parse(str(s), dayfirst=False)
    except Exception:
        return None
