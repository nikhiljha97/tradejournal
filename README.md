# TradeJournal

A professional trading analytics journal with:
- **All Upcomers-style analytics widgets** — calendar heatmap, equity curve, RRR gauge, Edge Score radar, win/loss donut, order types, intraday/duration analysis, trading days table
- **LLM psychology layer** (Anthropic claude-sonnet-4-6) — emotion extraction, discipline scoring, phrase-level attribution, emotion→R correlation
- **File importer** — MT5 HTML, CSV, Excel, PDF with auto column detection
- **Prop-firm compliance strip** — daily loss, max drawdown, consistency %, profit target progress
- **Multi-user ready** — `user_id` on every table; add auth in one session

---

## Local development

```bash
cd tradejournal
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: add GROQ_API_KEY
# Leave DATABASE_URL blank to use local SQLite

python app.py
# → http://127.0.0.1:5000
```

---

## Free online hosting (Render + Neon)

### 1. Database — Neon (free Postgres, no credit card)
1. Go to https://neon.tech → Sign up → Create project
2. Copy the **Connection string** (starts with `postgresql://`)
3. Keep it — you'll need it in step 3

### 2. Push to GitHub
```bash
cd tradejournal
git init && git add . && git commit -m "initial"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/tradejournal.git
git push -u origin main
```

### 3. Deploy on Render (free tier)
1. Go to https://render.com → New → Web Service
2. Connect your GitHub repo
3. Settings:
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT`
4. Under **Environment Variables**, add:
   ```
   DATABASE_URL  = postgresql://... (from Neon)
   GROQ_API_KEY = sk-ant-...
   SECRET_KEY    = any-long-random-string
   ```
5. Click **Deploy** — live in ~2 minutes

Render free tier sleeps after 15 min inactivity (cold start ~30s). Upgrade to $7/mo for always-on.

---

## File import formats

| Format       | How to export |
|--------------|---------------|
| MT5 HTML     | MT5 → Account History → right-click → Report → Save as HTML |
| Upcomers CSV | Dashboard → Trading History → Export |
| Generic CSV  | Any CSV with columns: Symbol, Type, Entry Time, Amount, Entry Price, Exit Time, Profit |
| Excel        | Same columns as CSV, saved as .xlsx |
| PDF          | Broker statements with table data |

The importer auto-detects column names and pairs MT5 `PROTECTIVE_STOP` orders with their parent market orders.

---

## Adding auth (when you go multi-user)

Every table has `user_id`. To add login:
1. `pip install Flask-Login Flask-Bcrypt`
2. Add `User` model, login/register routes
3. Replace `USER_ID = 1` in `app.py` with `current_user.id`
4. That's it — all queries are already scoped by `user_id`

---

## Files

```
app.py          Flask app + API routes
models.py       SQLAlchemy models (Trade, Settings, ImportLog)
metrics.py      KPI engine matching Upcomers definitions
sentiment.py    LLM sentiment via claude-sonnet-4-6
importer.py     Universal file parser (MT5/CSV/Excel/PDF)
config.py       Instrument pip values, constants
templates/      index.html — full dashboard
static/css/     style.css
static/js/      app.js
Procfile        Render/Heroku deployment
requirements.txt
```
