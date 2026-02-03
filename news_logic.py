# ==========================================================
# 📰 NEWS LOGIC — CONTEXTUAL SENTIMENT MODULE (PRODUCTION)
# ==========================================================

import os
import requests
from textblob import TextBlob
from datetime import datetime, timedelta

# ----------------------------------------------------------
# NLTK BOOTSTRAP (RENDER SAFE)
# ----------------------------------------------------------
# Render containers do NOT persist NLTK data.
# This prevents random LookupError crashes on cold starts.

import nltk
try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab")

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------

GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

DEFAULT_SENTIMENT = 0.0
DEFAULT_SUBJECTIVITY = 0.0

MAX_ARTICLES = 3
TIMEOUT = 10
HOURS_BACK = 12

KEYWORDS = [
    "nifty", "sensex", "market", "stocks", "equity",
    "rbi", "inflation", "gdp", "rates", "economy"
]

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
    Fetches recent macro market news and computes:

    Returns:
        sentiment (float)     -> avg polarity
        subjectivity (float)  -> avg subjectivity
        headlines (list[str]) -> filtered headlines
    """

    # News must NEVER crash the bot
    if not GNEWS_API_KEY:
        return DEFAULT_SENTIMENT, DEFAULT_SUBJECTIVITY, []

    try:
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

        sentiments = []
        subjectivities = []
        headlines = []

        for art in articles:
            title = art.get("title", "")
            if not title:
                continue

            # Keyword noise filter
            if not any(k in title.lower() for k in KEYWORDS):
                continue

            blob = TextBlob(title)
            sentiments.append(blob.sentiment.polarity)
            subjectivities.append(blob.sentiment.subjectivity)
            headlines.append(title)

        if not sentiments:
            return DEFAULT_SENTIMENT, DEFAULT_SUBJECTIVITY, []

        avg_sentiment = round(sum(sentiments) / len(sentiments), 2)
        avg_subjectivity = round(sum(subjectivities) / len(subjectivities), 2)

        return avg_sentiment, avg_subjectivity, headlines

    except Exception:
        # Absolute safety: news is CONTEXT, never a failure point
        return DEFAULT_SENTIMENT, DEFAULT_SUBJECTIVITY, []

# ----------------------------------------------------------
# FORMATTER (TELEGRAM FRIENDLY)
# ----------------------------------------------------------

def format_news_block(sentiment, subjectivity, headlines):
    """
    Formats news output for Telegram readability
    """

    if not headlines:
        return "📰 News: Neutral (No relevant macro headlines)"

    mood = (
        "🟢 Positive" if sentiment > 0.15 else
        "🔴 Negative" if sentiment < -0.15 else
        "🟡 Neutral"
    )

    tone = (
        "🔥 Opinion-Heavy" if subjectivity > 0.6 else
        "📘 Factual"
    )

    lines = [
        f"📰 News Sentiment: {sentiment:+.2f} ({mood})",
        f"🧠 News Tone: {tone} (Subjectivity: {subjectivity:.2f})"
    ]

    for h in headlines:
        lines.append(f"• {h[:75]}")

    return "\n".join(lines)
