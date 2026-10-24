"""
TradeJournal Health Monitor
============================
Validates all critical paths of the live site and alerts via Telegram on failure.

Usage:
  python monitor.py                    # run once
  python monitor.py --live             # run once, verbose
  python monitor.py --groq             # also validate Groq API with a real call
  python monitor.py --loop 300         # run every 5 minutes

Schedule on Render:
  Add as a Cron Job service → Command: python monitor.py
  Schedule: every 10 minutes

Environment variables (same as app):
  SITE_URL         = https://backtesting-journalmytrades.com  (default)
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID  (your personal chat ID or a monitoring group)
  GROQ_API_KEY
  DATABASE_URL
"""

import os, sys, time, json, argparse, traceback
import urllib.request, urllib.error

# ── Config ─────────────────────────────────────────────────────────────────────
SITE_URL        = os.environ.get("SITE_URL", "https://backtesting-journalmytrades.com").rstrip("/")
BOT_TOKEN       = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID         = os.environ.get("TELEGRAM_CHAT_ID", "")
GROQ_KEY        = os.environ.get("GROQ_API_KEY", "")
TIMEOUT         = 15   # seconds per request
ALERT_COOLDOWN  = 600  # seconds between repeat alerts for same check

_last_alert: dict[str, float] = {}   # check_name → last alert unix timestamp


# ── HTTP helpers ───────────────────────────────────────────────────────────────
def _get(url: str, headers: dict | None = None, timeout: int = TIMEOUT) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(8192).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = TIMEOUT) -> tuple[int, str]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(8192).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(8192).decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


# ── Telegram alert ─────────────────────────────────────────────────────────────
def send_telegram(message: str, force: bool = False, check_key: str = ""):
    if not BOT_TOKEN or not CHAT_ID:
        print("[monitor] Telegram not configured — skipping alert")
        return
    now = time.time()
    if check_key and not force:
        last = _last_alert.get(check_key, 0)
        if now - last < ALERT_COOLDOWN:
            print(f"[monitor] Alert suppressed (cooldown) for: {check_key}")
            return
    _last_alert[check_key] = now
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    status, _ = _post_json(url, {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if status == 200:
        print(f"[monitor] Telegram alert sent")
    else:
        print(f"[monitor] Telegram send failed: HTTP {status}")


# ── Individual checks ──────────────────────────────────────────────────────────
def check_homepage() -> dict:
    t0 = time.time()
    code, body = _get(f"{SITE_URL}/")
    ms = round((time.time() - t0) * 1000)
    ok = code == 200 and "TradeJournal" in body
    return {"name": "homepage", "ok": ok, "code": code, "ms": ms,
            "detail": "" if ok else f"HTTP {code}, body snippet: {body[:80]}"}


def check_blog_public() -> dict:
    """Blog must be accessible without login (for SEO)."""
    t0 = time.time()
    code, body = _get(f"{SITE_URL}/blog")
    ms = round((time.time() - t0) * 1000)
    ok = code == 200 and "journal" in body.lower()
    return {"name": "blog_public", "ok": ok, "code": code, "ms": ms,
            "detail": "" if ok else f"HTTP {code}"}


def check_blog_post() -> dict:
    t0 = time.time()
    code, body = _get(f"{SITE_URL}/blog/free-trading-journal-app")
    ms = round((time.time() - t0) * 1000)
    ok = code == 200 and len(body) > 1000
    return {"name": "blog_post", "ok": ok, "code": code, "ms": ms,
            "detail": "" if ok else f"HTTP {code}, body_len={len(body)}"}


def check_health_api() -> dict:
    t0 = time.time()
    code, body = _get(f"{SITE_URL}/api/health")
    ms = round((time.time() - t0) * 1000)
    try:
        data = json.loads(body)
        db_ok = data.get("db") == "ok"
        groq_ok = data.get("groq_key") == "ok"
        ok = code == 200 and db_ok
        detail = "" if ok else f"db={data.get('db')}, groq={data.get('groq_key')}"
    except Exception:
        ok, detail, db_ok, groq_ok = False, f"Bad JSON: {body[:60]}", False, False
    return {"name": "health_api", "ok": ok, "code": code, "ms": ms,
            "detail": detail, "db": db_ok, "groq_key": groq_ok}


def check_ads_txt() -> dict:
    code, body = _get(f"{SITE_URL}/ads.txt")
    ok = code == 200 and "google.com" in body and "pub-2562826047096065" in body
    return {"name": "ads_txt", "ok": ok, "code": code, "ms": 0,
            "detail": "" if ok else f"HTTP {code}"}


def check_sitemap() -> dict:
    code, body = _get(f"{SITE_URL}/sitemap.xml")
    ok = code == 200 and "<urlset" in body
    return {"name": "sitemap", "ok": ok, "code": code, "ms": 0,
            "detail": "" if ok else f"HTTP {code}"}


def check_llms_txt() -> dict:
    code, body = _get(f"{SITE_URL}/llms.txt")
    ok = code == 200 and "TradeJournal" in body
    return {"name": "llms_txt", "ok": ok, "code": code, "ms": 0,
            "detail": "" if ok else f"HTTP {code}"}


def check_groq_api() -> dict:
    """Live Groq test — costs ~50 tokens. Only run with --groq flag."""
    if not GROQ_KEY:
        return {"name": "groq_live", "ok": False, "detail": "GROQ_API_KEY not set", "ms": 0}
    t0 = time.time()
    code, body = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        {
            "model": "qwen/qwen3.8-27b",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "say ok"}],
        },
        headers={"Authorization": f"Bearer {GROQ_KEY}"},
    )
    ms = round((time.time() - t0) * 1000)
    ok = code == 200
    detail = "" if ok else f"HTTP {code}: {body[:120]}"
    return {"name": "groq_live", "ok": ok, "code": code, "ms": ms, "detail": detail}


def check_db_direct() -> dict:
    """Direct DB check — only available in same environment as app."""
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return {"name": "db_direct", "ok": False, "detail": "DATABASE_URL not set", "ms": 0}
    try:
        import psycopg2
        t0 = time.time()
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades")
        count = cur.fetchone()[0]
        conn.close()
        ms = round((time.time() - t0) * 1000)
        return {"name": "db_direct", "ok": True, "ms": ms, "detail": f"{count} trades in DB"}
    except Exception as e:
        return {"name": "db_direct", "ok": False, "ms": 0, "detail": str(e)[:100]}


# ── Main runner ────────────────────────────────────────────────────────────────
def run_checks(include_groq: bool = False, verbose: bool = False) -> list[dict]:
    checks = [
        check_homepage,
        check_blog_public,
        check_blog_post,
        check_health_api,
        check_ads_txt,
        check_sitemap,
        check_llms_txt,
    ]
    if include_groq:
        checks += [check_groq_api, check_db_direct]

    results = []
    for fn in checks:
        try:
            r = fn()
        except Exception as e:
            r = {"name": fn.__name__, "ok": False, "detail": traceback.format_exc()[-200:], "ms": 0}
        results.append(r)
        icon = "✅" if r["ok"] else "❌"
        ms = f" {r.get('ms',0)}ms" if r.get("ms") else ""
        detail = f" — {r['detail']}" if r.get("detail") else ""
        if verbose or not r["ok"]:
            print(f"  {icon} {r['name']}{ms}{detail}")
        elif verbose:
            print(f"  {icon} {r['name']}{ms}")

    return results


def build_alert(results: list[dict], run_time: float) -> str:
    failed = [r for r in results if not r["ok"]]
    if not failed:
        return ""

    lines = ["🚨 <b>TradeJournal health alert</b>", ""]
    for r in failed:
        lines.append(f"❌ <b>{r['name']}</b>")
        if r.get("detail"):
            lines.append(f"   {r['detail'][:120]}")
    lines += ["", f"🔗 {SITE_URL}", f"⏱ Checked at {time.strftime('%H:%M UTC')} ({round(run_time,1)}s)"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="TradeJournal health monitor")
    parser.add_argument("--groq",   action="store_true", help="Also run live Groq API test")
    parser.add_argument("--live",   action="store_true", help="Verbose output")
    parser.add_argument("--loop",   type=int, default=0,  help="Run every N seconds (0=once)")
    parser.add_argument("--alert",  action="store_true", help="Send Telegram even if all OK")
    args = parser.parse_args()

    while True:
        t0 = time.time()
        print(f"\n[monitor] {time.strftime('%Y-%m-%d %H:%M:%S UTC')} — checking {SITE_URL}")
        results = run_checks(include_groq=args.groq, verbose=args.live or True)
        elapsed = time.time() - t0

        failed = [r for r in results if not r["ok"]]
        passed = len(results) - len(failed)
        print(f"\n  Summary: {passed}/{len(results)} checks passed in {elapsed:.1f}s")

        if failed:
            alert = build_alert(results, elapsed)
            # Send one alert per failing check, with cooldown
            for r in failed:
                send_telegram(alert, check_key=r["name"])
        elif args.alert:
            send_telegram(f"✅ All {len(results)} checks passed — {SITE_URL}", force=True)

        if args.loop <= 0:
            sys.exit(1 if failed else 0)

        print(f"\n  Next check in {args.loop}s…")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
