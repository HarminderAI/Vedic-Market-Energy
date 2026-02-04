# ==========================================================
# 📰 NEWS LOGIC — INSTITUTIONAL MACRO & SECTOR SENTIMENT (2026)
# ==========================================================

import os
import requests
import nltk
from datetime import datetime, timedelta
from nltk.sentiment.vader import SentimentIntensityAnalyzer

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")  # GNews API key
NEWS_ENDPOINT = "https://gnews.io/api/v4/search"

LANG = "en"
COUNTRY = "in"
MAX_ARTICLES = 10
TIMEOUT = 10

# Sector keyword mapping (used for sector-specific weighting)
SECTOR_KEYWORDS = {
    "BANK": ["bank", "rbi", "interest rate", "loan", "credit"],
    "IT": ["it services", "software", "tech", "ai", "outsourcing"],
    "METAL": ["steel", "metal", "commodity", "iron", "aluminium"],
    "ENERGY": ["oil", "gas", "energy", "power"],
    "PHARMA": ["pharma", "drug", "healthcare"],
    "INFRA": ["infrastructure", "construction", "capital goods"],
}

# ----------------------------------------------------------
# NLTK BOOTSTRAP (RENDER SAFE)
# ----------------------------------------------------------
def ensure_nltk():
    try:
        nltk.data.find("sentiment/vader_lexicon")
    except LookupError:
        nltk.download("vader_lexicon")

ensure_nltk()
SIA = SentimentIntensityAnalyzer()

# ----------------------------------------------------------
# CORE FETCH FUNCTION
# ----------------------------------------------------------
def fetch_market_news(hours_back=12):
    """
    Fetches Indian market news and computes:
    - overall sentiment
    - subjectivity (noise)
    - sector-wise sentiment map
    """

    if not NEWS_API_KEY:
        return {
            "overall": 0.0,
            "noise": 0.0,
            "sector_map": {},
            "headlines": []
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
    except Exception:
        return {
            "overall": 0.0,
            "noise": 0.0,
            "sector_map": {},
            "headlines": []
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

    return {
        "overall": overall,
        "noise": noise,
        "sector_map": sector_map,
        "headlines": headlines[:5],
    }

# ----------------------------------------------------------
# TELEGRAM FORMATTER
# ----------------------------------------------------------
def format_news_block(news):
    overall = news["overall"]
    noise = news["noise"]
    headlines = news["headlines"]

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
