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

# Sector keyword mapping
SECTOR_KEYWORDS = {
    "BANK": ["bank", "rbi", "interest rate", "loan", "credit"],
    "IT": ["it services", "software", "tech", "ai", "outsourcing"],
    "METAL": ["steel", "metal", "commodity", "iron", "aluminium"],
    "ENERGY": ["oil", "gas", "energy", "power"],
    "PHARMA": ["pharma", "drug", "healthcare"],
    "INFRA": ["infrastructure", "construction", "capital goods"],
}

# ----------------------------------------------------------
# NLTK BOOTSTRAP (RENDER SAFE, ONE-TIME)
# ----------------------------------------------------------
def ensure_nltk():
    try:
        nltk.data.find("sentiment/vader_lexicon")
    except LookupError:
        nltk.download("vader_lexicon", quiet=True)

ensure_nltk()
SIA = SentimentIntensityAnalyzer()

# ----------------------------------------------------------
# FETCH & ANALYZE NEWS
# ----------------------------------------------------------
def fetch_market_news(hours_back=12):
    """
    Returns:
    {
        overall: float,
        noise: float,
        sector_map: dict,
        headlines: list
    }
    """

    # Fail-safe if API key missing
    if not NEWS_API_KEY:
        return _neutral_news()

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
        data = resp.json()
        articles = data.get("articles", [])
    except Exception:
        return _neutral_news()

    if not articles:
        return _neutral_news()

    compound_scores = []
    noise_scores = []
    sector_scores = {k: [] for k in SECTOR_KEYWORDS}
    headlines = []

    for art in articles:
        title = art.get("title", "")
        if not title:
            continue

        scores = SIA.polarity_scores(title)
        compound = scores["compound"]

        compound_scores.append(compound)
        noise_scores.append(abs(compound))
        headlines.append(title[:90])

        lower = title.lower()
        for sector, keys in SECTOR_KEYWORDS.items():
            if any(k in lower for k in keys):
                sector_scores[sector].append(compound)

    overall = round(sum(compound_scores) / len(compound_scores), 3)
    noise = round(sum(noise_scores) / len(noise_scores), 3)

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
# FORMAT FOR TELEGRAM
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

# ----------------------------------------------------------
# NEUTRAL FALLBACK
# ----------------------------------------------------------
def _neutral_news():
    return {
        "overall": 0.0,
        "noise": 0.0,
        "sector_map": {},
        "headlines": [],
    }
