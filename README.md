# Deal Tracker 🏷️

An automated, free-forever price & deal tracker.  
Scrapes your watchlist daily, uses AI to detect deals, emails you when something goes on sale.

**Stack:** GitHub Actions (scheduler) · Python + BeautifulSoup (scraper) · Claude Haiku (deal extraction) · GitHub Pages (dashboard)  
**Cost:** $0 — uses GitHub's free tier throughout.

---

## How it works

```
GitHub Actions (cron: daily 9am UTC)
  └─▶ scraper/scrape.py
        ├─ fetches each URL in data/watchlist.json
        ├─ sends page text to Claude Haiku for deal extraction
        ├─ writes data/results.json  (latest state)
        ├─ appends data/history.json (price log)
        ├─ emails you if new deals appeared
        └─ commits the updated data/ files back to the repo

GitHub Pages (auto-served from repo root)
  └─▶ index.html reads data/*.json and renders the dashboard
```

---

## Setup (10 minutes)

### 1. Fork / create the repo

Create a new **public** GitHub repo (or fork this one).  
Copy all files into it.

### 2. Enable GitHub Pages

`Settings → Pages → Source: Deploy from branch → branch: main, folder: / (root)`

Your dashboard will be live at `https://<you>.github.io/<repo>/`

### 3. Add repository secrets

`Settings → Secrets and variables → Actions → New repository secret`

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your key from console.anthropic.com |
| `NOTIFY_EMAIL` | Where to send deal alerts |
| `SMTP_HOST` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASS` | Gmail **App Password** (not your account password) |

> **Gmail app password:** Google Account → Security → 2-Step Verification → App passwords → create one for "Deal Tracker"

### 4. Edit your watchlist

Open `data/watchlist.json` and add what you want to track:

```json
[
  {
    "id": "amazon-headphones",
    "name": "Sony WH-1000XM5",
    "url": "https://www.amazon.com/dp/B09XS7JWHH",
    "type": "item",
    "notes": "Looking for any discount, ideally under $280"
  },
  {
    "id": "nike-store",
    "name": "Nike Sale",
    "url": "https://www.nike.com/w/sale",
    "type": "store",
    "notes": "Running shoes and training gear over 30% off"
  }
]
```

Commit and push — the scraper will pick it up on the next run.

### 5. Trigger a manual run (optional)

`Actions → Daily Deal Scraper → Run workflow`

---

## Watchlist fields

| Field | Required | Description |
|---|---|---|
| `id` | ✓ | Unique identifier (no spaces) |
| `name` | ✓ | Human-readable name |
| `url` | ✓ | Page to scrape |
| `type` | ✓ | `"store"` or `"item"` |
| `notes` | | Hints for the AI (target price, categories, etc.) |

---

## Data files

| File | Description |
|---|---|
| `data/watchlist.json` | **You edit this** — list of tracked items |
| `data/results.json` | Latest scan output — written by the scraper |
| `data/history.json` | Append-only price log — written by the scraper |

---

## Costs

| Service | Usage | Cost |
|---|---|---|
| GitHub Actions | ~2 min/day well within 2,000 free min/month | **$0** |
| GitHub Pages | Static hosting | **$0** |
| Claude Haiku API | ~30 items × 1000 tokens = ~30K tokens/day | ~$0.01/day |
| Email | Gmail SMTP | **$0** |

> The Anthropic API is the only cost. At $0.80/MTok input, 30 items/day costs roughly **$0.30/month** for a typical watchlist. The first $5 of API credit (free on signup) lasts months.

---

## Customising the schedule

Edit `.github/workflows/scrape.yml`:

```yaml
on:
  schedule:
    - cron: '0 9 * * *'   # daily at 9am UTC
    # - cron: '0 */12 * * *'  # twice a day
    # - cron: '0 9 * * 1'     # weekly on Monday
```

---

## Limitations

- Sites that require login (Costco member prices, etc.) won't work
- Heavy anti-bot sites (e.g. some Ticketmaster pages) may block scraping
- JavaScript-rendered prices (some SPAs) may not be visible to the plain HTML scraper — for those, add the URL of a static product page instead of the SPA
