import requests
import os
from textblob import TextBlob

# GNews API Setup (Get your free key at gnews.io)
GNEWS_API_KEY = os.environ.get('GNEWS_API_KEY')

def fetch_market_news(query="Nifty 50"):
    """Fetches top 5 relevant news articles for the given query."""
    url = f"https://gnews.io/api/v4/search?q={query}&lang=en&country=in&max=5&apikey={GNEWS_API_KEY}"
    
    try:
        response = requests.get(url)
        articles = response.json().get('articles', [])
        
        news_summary = []
        total_sentiment = 0
        
        for art in articles:
            title = art['title']
            # Perform quick sentiment analysis on the headline
            analysis = TextBlob(title)
            sentiment = analysis.sentiment.polarity
            
            total_sentiment += sentiment
            news_summary.append({
                "title": title,
                "url": art['url'],
                "sentiment": sentiment
            })
            
        # Average sentiment (Bullish if > 0.1, Bearish if < -0.1)
        avg_sentiment = total_sentiment / len(articles) if articles else 0
        return news_summary, round(avg_sentiment, 2)
        
    except Exception as e:
        print(f"News API Error: {e}")
        return [], 0
