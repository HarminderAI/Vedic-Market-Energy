# ==========================================================
# 📰 NEWS LOGIC — INSTITUTIONAL MACRO & SECTOR SENTIMENT (2026)
# ==========================================================

import os
import json
import time
import requests
import nltk
import gspread
from datetime import datetime, timedelta
from nltk.sentiment.vader import SentimentIntensityAnalyzer
from google.oauth2.service_account import Credentials

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")          # GNews API key
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

NEWS_ENDPOINT = "https://gnews.io/api/v4/search"

LANG = "en"
COUNTRY = "in"
MAX_ARTICLES = 10
TIMEOUT = 10

# Cache TTL (seconds) → 1 hour (protects API credits)
CACHE_TTL_SECONDS = 3600

# Sector keyword mapping (sector-weighted sentiment)
SECTOR_KEYWORDS = {
    "BANK":   ["bank", "rbi", "interest rate", "loan", "credit"],
    "IT":     ["it services", "software", "tech", "ai", "outsourcing"],
    "METAL":  ["steel", "metal", "commodity", "iron", "aluminium"],
    "ENERGY": ["oil", "gas", "energy", "power"],
    "PHARMA": ["pharma", "drug", "healthcare"],
    "INFRA":  ["infrastructure", "construction", "capital goods"],
}

# ----------------------------------------------------------
# NLTK BOOTSTRAP (RENDER SAFE + PERSISTENT)
# ----------------------------------------------------------
def ensure_nltk():
    """
    Ensures VADER lexicon is available in cloud environments.
    Downloads to a local project directory if missing.
    """
    data_path = os.path.join(os.getcwd(), "nltk_data")
    nltk.data.path.append(data_path)

    try:
        nltk.data.find("sentiment/vader_lexicon")
    except LookupError:
        nltk.download("vader_lexicon", download_dir=data_path, quiet=True)

ensure_nltk()
SIA = SentimentIntensityAnalyzer()

# ----------------------------------------------------------
# GOOGLE SHEETS — CACHE HELPERS (WITH VISIBILITY)
# ----------------------------------------------------------
def _sheet():
    """
    Returns the 'news_cache' worksheet.
    Raises loudly if Google API is unavailable.
    """
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(
            json.loads(SERVICE_JSON), scopes=scopes
        )
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(GOOGLE_SHEET_ID)

        try:
            return sheet.worksheet("news_cache")
        except:
            ws = sheet.add_worksheet("news_cache", rows=10, cols=2)
            ws.append_row(["timestamp", "payload"])
            return ws

    except Exception as e:
        print("❌ Google Sheets unavailable for NEWS cache:", e)
        raise


def _read_cache():
    """
    Reads cached news payload if TTL not expired.
    Falls back safely but logs failures.
    """
    try:
        ws = _sheet()
        rows = ws.get_all_records()
        if not rows:
            return None

        ts = float(rows[0]["timestamp"])
        if time.time() - ts < CACHE_TTL_SECONDS:
            return json.loads(rows[0]["payload"])

    except Exception as e:
        print("⚠️ News cache READ failed — falling back to API:", e)

    return None


def _write_cache(data):
    """
    Writes latest news payload to cache.
    Failure does NOT break execution but is logged.
    """
    try:
        ws = _sheet()
        ws.clear()
        ws.append_row([time.time(), json.dumps(data)])
    except Exception as e:
        print("⚠️ News cache WRITE failed — cache not persisted:", e)

# ----------------------------------------------------------
# CORE FETCH FUNCTION
# ----------------------------------------------------------
def fetch_market_news(hours_back=12):
    """
    Fetches Indian market news and computes:
    - overall sentiment bias
    - noise (subjectivity)
    - sector-wise sentiment map
    - headline sample
    """

    # 1️⃣ Try cache first (protect API quota)
    cached = _read_cache()
    if cached:
        return cached

    # 2️⃣ No API key → safe neutral fallback
    if not NEWS_API_KEY:
        return {
            "overall": 0.0,
            "noise": 0.0,
            "sector_map": {},
            "headlines": [],
        }

    from_time = (datetime.utcnow() - timedelta(hours=hours_back)).isoformat() + "Z"

    params = {
        "q": "Indian stock market OR Nifty OR Sensex",
        "lang": LANG,
        "country": COUNTRY,
        "from": from_time,
        "max": MAX_ARTICLES,
        "apikey": NEWS_API_KEY,
    }

    try:
        resp = requests.get(NEWS_ENDPOINT, params=params, timeout=TIMEOUT)
        articles = resp.json().get("articles", [])
    except Exception as e:
        print("❌ GNews API failure:", e)
        return {
            "overall": 0.0,
            "noise": 0.0,
            "sector_map": {},
            "headlines": [],
        }

    compound_scores = []
    noise_scores = []
    sector_scores = {k: [] for k in SECTOR_KEYWORDS}
    headlines = []

    for art in articles:
        title = art.get("title", "")
        if not title:
            continue

        sentiment = SIA.polarity_scores(title)
        compound = sentiment["compound"]

        compound_scores.append(compound)
        noise_scores.append(abs(compound))
        headlines.append(title[:90])

        lower = title.lower()
        for sector, keys in SECTOR_KEYWORDS.items():
            if any(k in lower for k in keys):
                sector_scores[sector].append(compound)

    overall = round(sum(compound_scores) / len(compound_scores), 3) if compound_scores else 0.0
    noise = round(sum(noise_scores) / len(noise_scores), 3) if noise_scores else 0.0

    sector_map = {
        sector: round(sum(vals) / len(vals), 3)
        for sector, vals in sector_scores.items()
        if vals
    }

    result = {
        "overall": overall,
        "noise": noise,
        "sector_map": sector_map,
        "headlines": headlines[:5],
    }

    # 3️⃣ Cache result (best-effort)
    _write_cache(result)

    return result

# ----------------------------------------------------------
# TELEGRAM FORMATTER (INSTITUTIONAL)
# ----------------------------------------------------------
def format_news_block(news):
    overall = news["overall"]
    noise = news["noise"]
    headlines = news["headlines"]

    # Institutional sentiment thresholds
    if overall > 0.2:
        mood = "🟢 POSITIVE"
    elif overall < -0.2:
        mood = "🔴 NEGATIVE"
    else:
        mood = "🟡 NEUTRAL"

    noise_flag = "⚠️ HIGH NOISE" if noise > 0.6 else "✅ LOW NOISE"

    lines = [
        "📰 *Market Sentiment*",
        f"Bias: {mood}",
        f"Noise: {noise_flag} ({noise})",
    ]

    if headlines:
        lines.append("")
        for h in headlines:
            lines.append(f"• {h}")

    return "\n".join(lines)
