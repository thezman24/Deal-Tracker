"""
deal-tracker/scraper/scrape.py
──────────────────────────────
Reads  data/watchlist.json   (your tracked stores/items)
Writes data/results.json     (latest scan results)
Writes data/history.json     (append-only price/deal log)
Sends  an email if new deals appear since last scan.

All free. Runs on GitHub Actions daily.
"""

import json, os, time, smtplib, traceback
from datetime import datetime, timezone
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup
import anthropic

# Selenium is only imported if needed (installed in workflow)
def _get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent.parent
WATCHLIST   = ROOT / "data" / "watchlist.json"
RESULTS     = ROOT / "data" / "results.json"
HISTORY     = ROOT / "data" / "history.json"

# ── helpers ───────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MIN_TEXT_LEN = 200   # if static fetch returns less than this, try the browser

def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "svg", "img"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ", strip=True).split())[:8000]

def fetch_page_static(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return _extract_text(resp.text)

def fetch_page_browser(url: str) -> str:
    print("  ↳ static fetch too thin — launching headless browser…")
    driver = _get_driver()
    try:
        driver.get(url)
        time.sleep(4)          # wait for JS to render
        return _extract_text(driver.page_source)
    finally:
        driver.quit()

def fetch_page(url: str) -> str:
    """Try a fast static fetch first; fall back to headless browser if the page is JS-rendered."""
    try:
        text = fetch_page_static(url)
        if len(text) >= MIN_TEXT_LEN:
            return text
        print(f"  ⚠ static fetch only got {len(text)} chars for {url}")
    except Exception as e:
        print(f"  ⚠ static fetch failed ({e}) — trying browser…")

    try:
        text = fetch_page_browser(url)
        print(f"  ✓ browser fetch got {len(text)} chars")
        return text
    except Exception as e:
        print(f"  ⚠ browser fetch also failed: {e}")
        return ""


def ask_claude(page_text: str, item: dict) -> dict:
    """Use Claude to extract deal/price info from raw page text."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    context = (
        f"Store/item name: {item['name']}\n"
        f"URL: {item['url']}\n"
        f"Type: {item['type']}\n"
    )
    if item.get("notes"):
        context += f"User notes: {item['notes']}\n"

    prompt = f"""You are a deal-detection assistant. Analyse the page text below and extract pricing and promotion information.

{context}

PAGE TEXT:
{page_text}

Return ONLY valid JSON with no markdown fences:
{{
  "current_price": "e.g. $49.99 or null if not found",
  "original_price": "e.g. $79.99 or null",
  "discount_pct": "e.g. 37 (integer) or null",
  "store_wide_deals": [
    {{"title": "...", "description": "...", "code": "promo code or null"}}
  ],
  "item_deals": [
    {{"title": "...", "description": "...", "discount": "e.g. $30 OFF"}}
  ],
  "is_on_sale": true,
  "summary": "one sentence"
}}
If the page text is empty or unreadable, return the schema with all nulls/empty arrays and is_on_sale: false."""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",   # haiku → cheapest, fast enough
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "current_price": None, "original_price": None,
            "discount_pct": None, "store_wide_deals": [], "item_deals": [],
            "is_on_sale": False, "summary": "Parse error"
        }


# ── email ─────────────────────────────────────────────────────────────────────

def send_email(new_deals: list[dict]):
    """Send a plain-text + HTML notification email via SMTP."""
    to_addr   = os.environ.get("NOTIFY_EMAIL", "")
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not all([to_addr, smtp_host, smtp_user, smtp_pass]):
        print("  ⚠ Email env vars not set — skipping notification.")
        return

    lines_plain = []
    lines_html  = ["<h2 style='color:#c47a1e'>🏷️ New deals found!</h2><ul>"]

    for d in new_deals:
        name = d["name"]
        summary = d.get("summary", "")
        sw = d.get("store_wide_deals", [])
        it = d.get("item_deals", [])
        lines_plain.append(f"\n{name}")
        lines_plain.append(f"  {summary}")
        for deal in sw:
            lines_plain.append(f"  [STORE-WIDE] {deal['title']}: {deal['description']}")
        for deal in it:
            lines_plain.append(f"  [ITEM]       {deal['title']}: {deal.get('discount','')}")
        lines_html.append(
            f"<li><strong>{name}</strong> — {summary}"
            + ("".join(f"<br>&nbsp;&nbsp;🏪 {x['title']}: {x['description']}" for x in sw))
            + ("".join(f"<br>&nbsp;&nbsp;🎯 {x['title']}: {x.get('discount','')}" for x in it))
            + "</li>"
        )

    lines_html.append("</ul>")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Deal Tracker — {len(new_deals)} new deal(s) found"
    msg["From"]    = smtp_user
    msg["To"]      = to_addr
    msg.attach(MIMEText("\n".join(lines_plain), "plain"))
    msg.attach(MIMEText("\n".join(lines_html),  "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_addr, msg.as_string())
        print(f"  ✉ Email sent to {to_addr}")
    except Exception as e:
        print(f"  ⚠ Email failed: {e}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ROOT.joinpath("data").mkdir(exist_ok=True)

    # load watchlist
    if not WATCHLIST.exists():
        print("No watchlist.json found — nothing to do.")
        return
    watchlist: list[dict] = json.loads(WATCHLIST.read_text())
    if not watchlist:
        print("Watchlist is empty.")
        return

    # load previous results (to diff for new deals)
    prev_results: dict = {}
    if RESULTS.exists():
        try:
            prev_results = {r["id"]: r for r in json.loads(RESULTS.read_text())}
        except Exception:
            pass

    # load history
    history: list = []
    if HISTORY.exists():
        try:
            history = json.loads(HISTORY.read_text())
        except Exception:
            pass

    now_iso = datetime.now(timezone.utc).isoformat()
    results = []
    newly_on_sale = []

    for item in watchlist:
        iid = item["id"]
        print(f"\n→ Scanning: {item['name']} ({item['url']})")
        page = fetch_page(item["url"])
        time.sleep(1)   # be polite

        ai = ask_claude(page, item)
        print(f"  on_sale={ai['is_on_sale']} price={ai['current_price']} summary={ai['summary']}")

        result = {
            **item,
            "last_scan": now_iso,
            "current_price":    ai["current_price"],
            "original_price":   ai["original_price"],
            "discount_pct":     ai["discount_pct"],
            "store_wide_deals": ai["store_wide_deals"],
            "item_deals":       ai["item_deals"],
            "is_on_sale":       ai["is_on_sale"],
            "summary":          ai["summary"],
        }
        results.append(result)

        # append to history
        history.append({
            "id":            iid,
            "name":          item["name"],
            "ts":            now_iso,
            "price":         ai["current_price"],
            "discount_pct":  ai["discount_pct"],
            "is_on_sale":    ai["is_on_sale"],
        })

        # detect *new* deals (wasn't on sale before, is now)
        prev = prev_results.get(iid, {})
        was_on_sale = prev.get("is_on_sale", False)
        if ai["is_on_sale"] and not was_on_sale:
            newly_on_sale.append(result)

    # write outputs
    RESULTS.write_text(json.dumps(results, indent=2))
    HISTORY.write_text(json.dumps(history, indent=2))
    print(f"\n✅ Wrote results for {len(results)} items.")

    # notify
    if newly_on_sale:
        print(f"🏷️  {len(newly_on_sale)} new deal(s) — sending email…")
        send_email(newly_on_sale)
    else:
        print("No new deals since last scan.")


if __name__ == "__main__":
    main()
