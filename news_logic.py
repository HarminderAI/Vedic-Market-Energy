# ==========================================================
# 📰 NEWS LOGIC — VADER SENTIMENT (2026 INSTITUTIONAL)
# ==========================================================

import os
import requests
from datetime import datetime, timedelta

# ----------------------------------------------------------
# NLTK BOOTSTRAP (Render-safe)
# ----------------------------------------------------------

import nltk
try:
    nltk.data.find("sentiment/vader_lexicon")
except LookupError:
    nltk.download("vader_lexicon", quiet=True)

from nltk.sentiment.vader import SentimentIntensityAnalyzer

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------

GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

DEFAULT_SENTIMENT = 0.0
DEFAULT_SUBJECTIVITY = 0.0

MAX_ARTICLES = 3
TIMEOUT = 10
HOURS_BACK = 12  # UTC-safe rolling window

# ⚠️ Institutional thresholds
CRASH_SENTIMENT_THRESHOLD = -0.50  # dominates aggregation

KEYWORDS = [
    "nifty", "sensex", "market", "stocks", "equity",
    "rbi", "inflation", "gdp", "rates", "economy",
    "bank", "it", "metal", "pharma", "auto"
]

# ----------------------------------------------------------
# INITIALIZE ANALYZER
# ----------------------------------------------------------

vader = SentimentIntensityAnalyzer()

# ----------------------------------------------------------
# CORE FUNCTION
# ----------------------------------------------------------

def fetch_market_news(
    query="Nifty OR Indian Stock Market",
    country="in",
    lang="en",
    max_articles=MAX_ARTICLES,
    hours_back=HOURS_BACK
):
    """
    Returns:
        sentiment (float)     -> VADER compound (institutional aggregation)
        subjectivity (float)  -> Emotional intensity proxy
        headlines (list[str])
    """

    if not GNEWS_API_KEY:
        return DEFAULT_SENTIMENT, DEFAULT_SUBJECTIVITY, []

    try:
        # ✅ UTC-based rolling window (timezone safe)
        published_after = (
            datetime.utcnow() - timedelta(hours=hours_back)
        ).isoformat("T") + "Z"

        url = (
            "https://gnews.io/api/v4/search"
            f"?q={query}"
            f"&lang={lang}"
            f"&country={country}"
            f"&max={max_articles}"
            f"&from={published_after}"
            f"&apikey={GNEWS_API_KEY}"
        )

        response = requests.get(url, timeout=TIMEOUT)
        articles = response.json().get("articles", [])

        compounds = []
        subjectivities = []
        headlines = []

        for art in articles:
            title = art.get("title", "").strip()
            if not title:
                continue

            # Noise filter
            if not any(k in title.lower() for k in KEYWORDS):
                continue

            scores = vader.polarity_scores(title)

            compound = scores["compound"]

            # Subjectivity proxy = emotional imbalance
            subjectivity = abs(scores["pos"] - scores["neg"])

            compounds.append(compound)
            subjectivities.append(subjectivity)
            headlines.append(title)

        if not compounds:
            return DEFAULT_SENTIMENT, DEFAULT_SUBJECTIVITY, []

        # --------------------------------------------------
        # 🏛️ INSTITUTIONAL AGGREGATION LOGIC
        # --------------------------------------------------

        worst_news = min(compounds)

        if worst_news <= CRASH_SENTIMENT_THRESHOLD:
            # Worst-case dominates (panic logic)
            final_sentiment = worst_news
        else:
            # Otherwise average is acceptable
            final_sentiment = sum(compounds) / len(compounds)

        avg_subjectivity = sum(subjectivities) / len(subjectivities)

        return (
            round(final_sentiment, 2),
            round(avg_subjectivity, 2),
            headlines
        )

    except Exception:
        return DEFAULT_SENTIMENT, DEFAULT_SUBJECTIVITY, []

# ----------------------------------------------------------
# TELEGRAM FORMATTER
# ----------------------------------------------------------

def format_news_block(sentiment, subjectivity, headlines):
    if not headlines:
        return "📰 News: Neutral (No relevant macro headlines)"

    mood = (
        "🟢 Positive" if sentiment > 0.20 else
        "🔴 Negative" if sentiment < -0.20 else
        "🟡 Neutral"
    )

    tone = (
        "🔥 Opinion-Heavy" if subjectivity > 0.6 else
        "📘 Factual"
    )

    lines = [
        f"📰 News Sentiment (VADER): {sentiment:+.2f} ({mood})",
        f"🧠 News Tone: {tone} (Emotion: {subjectivity:.2f})"
    ]

    for h in headlines:
        lines.append(f"• {h[:90]}")

    return "\n".join(lines)
