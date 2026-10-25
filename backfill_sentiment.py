"""
backfill_sentiment.py
---------------------
Retroactively runs AI psychology analysis on trades that were saved
during the period when the transformers/AI model was broken.

Targets trades where sentiment_source is NULL, "pending", or "none"
AND the trade has notes worth analysing.

Usage:
  python3 backfill_sentiment.py --dry-run      # shows how many would be processed
  python3 backfill_sentiment.py --run           # actually updates the DB
  python3 backfill_sentiment.py --run --limit 50  # process max 50 at a time (safe batches)
  python3 backfill_sentiment.py --run --user-id 42  # only one user's trades

Run from the tradejournal 3 folder with the .env loaded.
"""

import os, sys, json, time, argparse
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask
from config import Config
from models import db, Trade
import sentiment as sent

# ── Bootstrap minimal Flask app so SQLAlchemy works ──────────────────────────
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

def get_candidates(user_id=None):
    """Return Trade objects that need sentiment backfill."""
    q = Trade.query.filter(
        db.or_(
            Trade.sentiment_source == None,
            Trade.sentiment_source == "pending",
            Trade.sentiment_source == "none",
            Trade.sentiment_source == "",
        )
    )
    if user_id:
        q = q.filter(Trade.user_id == user_id)
    return q.order_by(Trade.id.asc()).all()

def backfill(dry_run=True, limit=None, user_id=None):
    with app.app_context():
        candidates = get_candidates(user_id=user_id)

        total = len(candidates)
        print(f"\n{'[DRY RUN] ' if dry_run else ''}Found {total} trade(s) without AI sentiment analysis.")

        if total == 0:
            print("Nothing to do. All trades already have sentiment data.")
            return

        if limit:
            candidates = candidates[:limit]
            print(f"Processing first {len(candidates)} (limit={limit})\n")
        else:
            print(f"Processing all {total}\n")

        ok = 0
        skipped = 0
        failed = 0

        for i, trade in enumerate(candidates, 1):
            notes = (trade.notes or "").strip()
            uid = trade.user_id
            tid = trade.id
            instrument = trade.instrument or "?"

            prefix = f"[{i}/{len(candidates)}] Trade #{tid} (user {uid}, {instrument})"

            if len(notes) < 5:
                print(f"  {prefix} — SKIP (notes too short: {repr(notes[:40])})")
                if not dry_run:
                    # Mark as "none" so we don't keep re-checking it
                    trade.sentiment_source = "none"
                    trade.sentiment_label  = "No notes"
                    trade.sentiment_score  = 0.0
                    trade.sentiment_summary = ""
                    trade.emotions          = "[]"
                    trade.sentiment_phrases = "[]"
                    db.session.commit()
                skipped += 1
                continue

            print(f"  {prefix} — notes: {repr(notes[:60])}{'…' if len(notes)>60 else ''}")

            if dry_run:
                ok += 1
                continue

            try:
                result = sent.analyze(notes)
                trade.sentiment_label   = result.get("label")   or "Neutral"
                trade.sentiment_score   = result.get("score")   or 0.0
                trade.sentiment_summary = result.get("summary") or ""
                trade.sentiment_source  = result.get("source")  or "llm"
                trade.emotions          = json.dumps(result.get("emotions")  or [])
                trade.sentiment_phrases = json.dumps(result.get("phrases")   or [])

                # Also backfill setups if detected and not already set
                existing_setups = json.loads(trade.setups or "[]")
                new_setups      = result.get("setups") or []
                if new_setups and not existing_setups:
                    trade.setups = json.dumps(new_setups)

                db.session.commit()
                print(f"    OK — label={trade.sentiment_label}, score={trade.sentiment_score:.2f}, "
                      f"emotions={json.loads(trade.emotions)}")
                ok += 1

                # Be nice to the API — small delay between calls
                time.sleep(0.4)

            except Exception as e:
                db.session.rollback()
                print(f"    ERROR — {e}")
                failed += 1

        print(f"\n{'[DRY RUN] ' if dry_run else ''}Done.")
        print(f"  Would process: {ok + skipped}"   if dry_run else f"  Updated:  {ok}")
        if not dry_run:
            print(f"  Skipped (no notes): {skipped}")
            print(f"  Failed:  {failed}")
        print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill AI sentiment for existing trades")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed, no DB writes")
    parser.add_argument("--run",     action="store_true", help="Actually update the database")
    parser.add_argument("--limit",   type=int, default=None, help="Max trades to process in one run")
    parser.add_argument("--user-id", type=int, default=None, help="Only process trades for a specific user ID")
    args = parser.parse_args()

    if not args.dry_run and not args.run:
        print("Specify --dry-run to preview, or --run to execute.")
        sys.exit(1)

    backfill(
        dry_run=not args.run,
        limit=args.limit,
        user_id=args.user_id,
    )
