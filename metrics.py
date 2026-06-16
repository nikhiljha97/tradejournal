"""
KPI engine — matches Upcomers metric definitions exactly (verified against screenshots).
All metrics expressed in both $ and R where applicable.
"""
import math
from collections import defaultdict
from datetime import datetime


def _safe_div(a, b):
    return a / b if b else 0.0


def _stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))


def compute_all(trades: list, settings: dict) -> dict:
    return {
        "kpi":      compute_kpi(trades),
        "prop":     compute_prop(trades, settings),
        "calendar": compute_calendar(trades),
        "intraday": compute_intraday(trades),
        "duration": compute_duration(trades),
        "emotion":  compute_emotion(trades),
    }


def compute_kpi(trades: list) -> dict:
    closed = [t for t in trades if t.get("realized_pnl") is not None]
    n = len(closed)
    if n == 0:
        return _empty_kpi()

    pnls  = [t["realized_pnl"] for t in closed]
    rs    = [t["realized_r"] for t in closed if t.get("realized_r") is not None]
    wins  = [t for t in closed if t["realized_pnl"] > 0]
    losses= [t for t in closed if t["realized_pnl"] < 0]
    be    = [t for t in closed if t["realized_pnl"] == 0]

    gross_profit = sum(t["realized_pnl"] for t in wins)
    gross_loss   = abs(sum(t["realized_pnl"] for t in losses))

    win_rate   = _safe_div(len(wins), n)
    avg_win    = _safe_div(gross_profit, len(wins))    # $255.07 in screenshots
    avg_loss   = _safe_div(-gross_loss, len(losses))   # -$30.14
    profit_factor = _safe_div(gross_profit, gross_loss) if gross_loss else (
        float("inf") if gross_profit > 0 else 0.0)
    # RRR = avg_win / |avg_loss|  → 8.46 in screenshots
    rrr = _safe_div(avg_win, abs(avg_loss)) if avg_loss else 0.0

    best_trade  = max(pnls)
    worst_trade = min(pnls)
    avg_per_trade = _safe_div(sum(pnls), n)  # $47.65

    # R metrics
    win_r   = [t["realized_r"] for t in wins  if t.get("realized_r") is not None]
    loss_r  = [t["realized_r"] for t in losses if t.get("realized_r") is not None]
    avg_r   = _safe_div(sum(rs), len(rs)) if rs else 0.0
    expectancy_r = avg_r

    # Equity & drawdown
    equity, eq_r = [], []
    run, run_r = 0.0, 0.0
    for t in closed:
        run   += t["realized_pnl"]
        run_r += t.get("realized_r") or 0.0
        equity.append(round(run, 2))
        eq_r.append(round(run_r, 3))

    max_dd   = _max_drawdown(equity)
    max_dd_r = _max_drawdown(eq_r)
    peak     = max(equity) if equity else 0.0

    # Streaks
    cur_streak, longest_win_streak, longest_loss_streak = _streaks(closed)

    # Sharpe / Sortino per-trade R
    r_std = _stdev(rs)
    sharpe = _safe_div(expectancy_r, r_std) if r_std else 0.0
    down_r = [r for r in rs if r < 0]
    d_std  = _stdev(down_r) if len(down_r) > 1 else 0.0
    sortino = _safe_div(expectancy_r, d_std) if d_std else 0.0

    # Starting balance for ROI
    starting = 10000  # overridden at call site if settings passed

    return {
        "total_trades": n, "wins": len(wins), "losses": len(losses), "breakeven": len(be),
        "win_rate": round(win_rate * 100, 2),
        "total_pnl": round(sum(pnls), 2),
        "total_r": round(sum(rs), 3) if rs else 0.0,
        "avg_per_trade": round(avg_per_trade, 2),    # Upcomers "average return per trade"
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_win_r": round(_safe_div(sum(win_r), len(win_r)), 3) if win_r else 0.0,
        "avg_loss_r": round(_safe_div(sum(loss_r), len(loss_r)), 3) if loss_r else 0.0,
        "expectancy_r": round(expectancy_r, 3),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 9999,
        "rrr": round(rrr, 2),                        # Upcomers "Risk-to-Reward Ratio"
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_r": round(max_dd_r, 3),
        "peak_equity": round(peak, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "current_streak": cur_streak,
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
        "equity_curve": equity,
        "equity_curve_r": eq_r,
        "r_distribution": _r_histogram(rs),
        "by_setup": _breakdown(closed, "setups", is_list=True),
        "by_session": _breakdown(closed, "session"),
        "by_instrument": _breakdown(closed, "instrument"),
        "by_weekday": _weekday_breakdown(closed),
        "by_direction": _breakdown(closed, "direction"),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }


def _empty_kpi():
    return {k: 0 for k in [
        "total_trades","wins","losses","breakeven","win_rate","total_pnl","total_r",
        "avg_per_trade","avg_win","avg_loss","avg_win_r","avg_loss_r","expectancy_r",
        "profit_factor","rrr","best_trade","worst_trade","max_drawdown","max_drawdown_r",
        "peak_equity","sharpe","sortino","current_streak","longest_win_streak",
        "longest_loss_streak","gross_profit","gross_loss",
    ]} | {"equity_curve":[],"equity_curve_r":[],"r_distribution":{},
          "by_setup":{},"by_session":{},"by_instrument":{},
          "by_weekday":{},"by_direction":{}}


def compute_prop(trades, settings):
    closed  = [t for t in trades if t.get("realized_pnl") is not None]
    start   = float(settings.get("starting_balance", 10000))
    dl_lim  = float(settings.get("daily_loss_limit", 500))
    max_dd  = float(settings.get("max_drawdown", 1000))
    max_c   = float(settings.get("max_contracts", 3))
    cons_pct= float(settings.get("consistency_pct", 30))
    target  = float(settings.get("profit_target", 500))

    day_pnl = defaultdict(float)
    for t in closed:
        day_pnl[t["trade_date"]] += t["realized_pnl"]

    total_pnl  = sum(day_pnl.values())
    profit_days = {d: p for d, p in day_pnl.items() if p > 0}
    best_day   = max(profit_days.values()) if profit_days else 0.0
    best_day_share = _safe_div(best_day, total_pnl) * 100 if total_pnl > 0 else 0.0

    loss_breaches = [{"date": d, "pnl": round(p, 2)} for d, p in day_pnl.items()
                     if dl_lim and p <= -dl_lim]
    size_breaches = [{"id": t.get("id"), "date": t["trade_date"], "lots": t.get("lots")}
                     for t in closed if max_c and (t.get("lots") or 0) > max_c]

    run = peak = worst_dd = 0.0
    for t in closed:
        run  += t["realized_pnl"]
        peak  = max(peak, run)
        worst_dd = max(worst_dd, peak - run)

    roi = _safe_div(total_pnl, start) * 100
    target_progress = _safe_div(total_pnl, target) * 100 if target else 0.0

    return {
        "account_label": settings.get("account_label", "Eval"),
        "starting_balance": start,
        "current_balance": round(start + total_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": round(roi, 2),
        "profit_target": target,
        "target_progress": round(target_progress, 1),
        "consistency_pct_limit": cons_pct,
        "best_day_share": round(best_day_share, 1),
        "best_day": round(best_day, 2),
        "consistency_ok": best_day_share <= cons_pct if total_pnl > 0 else True,
        "daily_loss_limit": dl_lim,
        "loss_breaches": loss_breaches,
        "max_contracts": max_c,
        "size_breaches": size_breaches,
        "max_drawdown_limit": max_dd,
        "worst_drawdown": round(worst_dd, 2),
        "drawdown_limit_usd": round(start - max_dd, 2),
        "drawdown_ok": worst_dd < max_dd,
        "daily_drawdown_pct": round(_safe_div(dl_lim, start) * 100, 2),
        "max_loss_pct": round(_safe_div(max_dd, start) * 100, 2),
        "trading_days": len(day_pnl),
        "day_pnl": {k: round(v, 2) for k, v in sorted(day_pnl.items())},
    }


def compute_calendar(trades):
    """Daily P&L for calendar heatmap widget."""
    closed = [t for t in trades if t.get("realized_pnl") is not None]
    day_pnl   = defaultdict(float)
    day_count  = defaultdict(int)
    for t in closed:
        d = t["trade_date"]
        day_pnl[d]   += t["realized_pnl"]
        day_count[d] += 1
    return {d: {"pnl": round(day_pnl[d], 2), "trades": day_count[d]}
            for d in sorted(day_pnl)}


def compute_intraday(trades):
    """Best/worst/busiest hour — mirrors Upcomers Intraday Activity widget."""
    closed = [t for t in trades if t.get("realized_pnl") is not None]
    hour_pnl   = defaultdict(float)
    hour_count = defaultdict(int)
    for t in closed:
        et = t.get("entry_time", "")
        if et and len(et) >= 2:
            try:
                h = int(et[:2])
                hour_pnl[h]   += t["realized_pnl"]
                hour_count[h] += 1
            except ValueError:
                pass

    if not hour_pnl:
        return {"best_hour": None, "worst_hour": None, "busiest_hour": None,
                "total_trades": 0, "hour_data": {}}

    best_h  = max(hour_pnl, key=hour_pnl.get)
    worst_h = min(hour_pnl, key=hour_pnl.get)
    busy_h  = max(hour_count, key=hour_count.get)

    return {
        "best_hour":    {"hour": best_h,  "pnl": round(hour_pnl[best_h], 2),  "trades": hour_count[best_h]},
        "worst_hour":   {"hour": worst_h, "pnl": round(hour_pnl[worst_h], 2), "trades": hour_count[worst_h]},
        "busiest_hour": {"hour": busy_h,  "pnl": round(hour_pnl[busy_h], 2),  "trades": hour_count[busy_h]},
        "total_trades": len(closed),
        "hour_data": {h: {"pnl": round(hour_pnl[h], 2), "trades": hour_count[h]}
                      for h in sorted(hour_pnl)},
    }


def compute_duration(trades):
    """Duration analysis — most profitable hold time, worst hour, best avg PnL."""
    closed = [t for t in trades if t.get("realized_pnl") is not None and t.get("duration_minutes")]

    def bucket(m):
        if m < 1:    return "< 1m"
        if m < 15:   return "1-15m"
        if m < 60:   return "15m-1h"
        if m < 240:  return "1h-4h"
        if m < 1440: return "4h-1d"
        return "1d+"

    dur_pnl   = defaultdict(float)
    dur_count = defaultdict(int)
    dur_wins  = defaultdict(int)
    for t in closed:
        b = bucket(t["duration_minutes"])
        dur_pnl[b]   += t["realized_pnl"]
        dur_count[b] += 1
        if t["realized_pnl"] > 0:
            dur_wins[b] += 1

    if not dur_pnl:
        return {}

    most_profitable = max(dur_pnl, key=dur_pnl.get)
    best_avg = max(dur_pnl, key=lambda k: _safe_div(dur_pnl[k], dur_count[k]))
    best_wr  = max(dur_wins, key=lambda k: _safe_div(dur_wins[k], dur_count[k]))
    most_common = max(dur_count, key=dur_count.get)

    hour_data = {}
    for t in closed:
        et = t.get("entry_time", "")
        if et and len(et) >= 2:
            try:
                h = int(et[:2])
                if h not in hour_data:
                    hour_data[h] = {"pnl": 0.0, "count": 0}
                hour_data[h]["pnl"]   += t["realized_pnl"]
                hour_data[h]["count"] += 1
            except ValueError:
                pass

    worst_hour_h = min(hour_data, key=lambda h: hour_data[h]["pnl"]) if hour_data else None

    return {
        "most_profitable_bucket": most_profitable,
        "most_profitable_pnl": round(dur_pnl[most_profitable], 2),
        "best_avg_pnl_bucket": best_avg,
        "best_avg_pnl": round(_safe_div(dur_pnl[best_avg], dur_count[best_avg]), 2),
        "highest_win_rate_bucket": best_wr,
        "highest_win_rate": round(_safe_div(dur_wins[best_wr], dur_count[best_wr]) * 100, 1),
        "most_common_bucket": most_common,
        "most_common_count": dur_count[most_common],
        "worst_hour": worst_hour_h,
        "worst_hour_pnl": round(hour_data[worst_hour_h]["pnl"], 2) if worst_hour_h else 0,
        "bucket_data": {b: {"pnl": round(dur_pnl[b], 2), "trades": dur_count[b],
                            "win_rate": round(_safe_div(dur_wins[b], dur_count[b]) * 100, 1)}
                        for b in dur_pnl},
    }


def compute_emotion(trades):
    """Emotion → avg R + avg PnL, the psychology layer Upcomers doesn't have."""
    closed = [t for t in trades if t.get("realized_pnl") is not None]
    buckets = defaultdict(list)
    for t in closed:
        emotions = t.get("emotions") or []
        if isinstance(emotions, str):
            import json
            try: emotions = json.loads(emotions)
            except: emotions = []
        if not emotions:
            buckets["(untagged)"].append(t)
        for em in emotions:
            buckets[em].append(t)

    def _agg(ts):
        pnls = [t["realized_pnl"] for t in ts]
        rs   = [t["realized_r"] for t in ts if t.get("realized_r") is not None]
        wins = sum(1 for p in pnls if p > 0)
        return {
            "trades": len(ts), "avg_pnl": round(_safe_div(sum(pnls), len(pnls)), 2),
            "avg_r":  round(_safe_div(sum(rs), len(rs)), 3) if rs else 0.0,
            "win_rate": round(_safe_div(wins, len(ts)) * 100, 1),
            "total_pnl": round(sum(pnls), 2),
        }
    return {k: _agg(v) for k, v in sorted(buckets.items())}


# ─── helpers ──────────────────────────────────────────────────────────────────
def _max_drawdown(equity):
    if not equity: return 0.0
    peak = equity[0]; max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return max_dd


def _streaks(closed):
    longest_win = longest_loss = win_run = loss_run = 0
    cur = 0
    for t in closed:
        p = t["realized_pnl"]
        if p > 0:
            win_run += 1; loss_run = 0; cur = win_run
        elif p < 0:
            loss_run += 1; win_run = 0; cur = -loss_run
        else:
            continue
        longest_win  = max(longest_win,  win_run)
        longest_loss = max(longest_loss, loss_run)
    return cur, longest_win, longest_loss


def _r_histogram(rs):
    bins = [(-99,-3),(-3,-2),(-2,-1),(-1,0),(0,1),(1,2),(2,3),(3,99)]
    labels = ["< -3R","-3 to -2R","-2 to -1R","-1 to 0R","0 to 1R","1 to 2R","2 to 3R","> 3R"]
    counts = [sum(1 for r in rs if lo <= r < hi) for lo, hi in bins]
    return dict(zip(labels, counts))


def _agg(rows):
    pnls = [t["realized_pnl"] for t in rows]
    rs   = [t["realized_r"] for t in rows if t.get("realized_r") is not None]
    wins = sum(1 for p in pnls if p > 0)
    return {"trades": len(rows), "pnl": round(sum(pnls), 2),
            "avg_r": round(_safe_div(sum(rs), len(rs)), 3) if rs else 0.0,
            "win_rate": round(_safe_div(wins, len(rows)) * 100, 1),
            "avg_pnl": round(_safe_div(sum(pnls), len(pnls)), 2)}


def _breakdown(closed, key, is_list=False):
    buckets = defaultdict(list)
    import json as _json
    for t in closed:
        val = t.get(key)
        if is_list:
            if isinstance(val, str):
                try: val = _json.loads(val)
                except: val = []
            items = val if isinstance(val, list) else []
            if not items: buckets["(none)"].append(t)
            for item in items: buckets[item].append(t)
        else:
            buckets[val or "(none)"].append(t)
    return {k: _agg(v) for k, v in sorted(buckets.items())}


def _weekday_breakdown(closed):
    order = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    buckets = defaultdict(list)
    for t in closed:
        try:
            d = datetime.strptime(t["trade_date"], "%Y-%m-%d")
            buckets[order[d.weekday()]].append(t)
        except: pass
    return {day: _agg(buckets[day]) for day in order if day in buckets}
