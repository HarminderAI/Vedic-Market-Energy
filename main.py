# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL PRODUCTION (v2.3)
# ==========================================================

import os, json, time, datetime, threading, io
import requests, pytz
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import gspread
from google.oauth2.service_account import Credentials
from concurrent.futures import ThreadPoolExecutor, as_completed

from keep_alive import keep_alive
from news_logic import fetch_market_news, format_news_block

# ==========================================================
# CONFIG & CONSTANTS
# ==========================================================
IST = pytz.timezone("Asia/Kolkata")
MAX_WORKERS = 5
SECTOR_CAP = 2          # Max buys per sector
MIN_VOL_FLOOR = 100000  # Minimum daily volume to trade
MIN_PRICE = 50          # Penny stock filter
STATE_RUN_KEY = "last_run_date" # Single source of truth

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

# ==========================================================
# HELPERS
# ==========================================================
def ist_now(): return datetime.datetime.now(IST)
def ist_today(): return ist_now().date().isoformat()

msg_lock = threading.Lock()

def send_msg(text):
    """Simple Telegram sender with error handling."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram Fail: {e}")

# ==========================================================
# GOOGLE SHEETS (BATCH OPTIMIZED)
# ==========================================================
def sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SERVICE_JSON, scopes=scopes)
    return gspread.authorize(creds)

gc = sheet_client()
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

def safe_sheet(name, headers):
    try:
        return sheet.worksheet(name)
    except:
        ws = sheet.add_worksheet(name, rows=1000, cols=len(headers))
        ws.append_row(headers)
        return ws

# Init Sheets
state_ws  = safe_sheet("state", ["key", "value"])
stocks_ws = safe_sheet("stocks", ["symbol","score","bucket","vol_ratio","stop_loss","target","sector"])
history_ws = safe_sheet("history", ["date","symbol","action","price","stop_loss","target","result"])
memory_ws = safe_sheet("memory", ["date", "squeezing_symbols_csv"])

# ==========================================================
# DATA ENGINE (HARDENED)
# ==========================================================
HARDCODED_FALLBACK = [
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
    "SBIN.NS","BHARTIARTL.NS","ITC.NS","KOTAKBANK.NS","LT.NS"
]

def load_nifty_200_and_sectors():
    """Returns symbol list and sector map with robust CSV handling and Anti-Bot Headers."""
    try:
        url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
        
        # 🛡️ FIX 1: Browser-Mimicking Headers (Bypasses NSE WAF)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://www.nseindia.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive"
        }
        
        r = requests.get(url, headers=headers, timeout=15)
        df = pd.read_csv(io.StringIO(r.text))
        
        # Robust Column Finder
        industry_col = next((c for c in df.columns if "Industry" in c), None)
        symbol_col = next((c for c in df.columns if "Symbol" in c), "Symbol")
        
        if not industry_col:
            # Fallback: Assume 3rd column is Industry if named weirdly
            industry_col = df.columns[2] if len(df.columns) > 2 else None

        df['Symbol'] = df[symbol_col] + ".NS"
        
        if industry_col:
            sector_map = dict(zip(df['Symbol'], df[industry_col]))
        else:
            sector_map = {s: "Unknown" for s in df['Symbol']}
            
        return df['Symbol'].tolist(), sector_map
    except Exception as e:
        print(f"NSE Download Error: {e}")
        return HARDCODED_FALLBACK, {s: "Bluechip" for s in HARDCODED_FALLBACK}

def safe_download(symbol, days=100):
    # Retry Logic for Yahoo Flakiness
    for attempt in range(2):
        try:
            # Random micro-throttle
            time.sleep(0.1 + (hash(symbol) % 10) / 100.0) 
            df = yf.download(symbol, period=f"{days}d", auto_adjust=True, progress=False)
            
            if df is None or df.empty: return None
            
            # Flatten MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
                
            return df.dropna()
        except:
            if attempt == 0: time.sleep(1) # Wait before retry
            continue
    return None

def batch_download(symbols):
    data = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(safe_download, s): s for s in symbols}
        for f in as_completed(futures):
            df = f.result()
            if df is not None:
                data[futures[f]] = df
    return data

# ==========================================================
# SMART ANALYST ENGINE (FIXED LOGIC)
# ==========================================================
def score_stock(df, was_squeezing):
    if len(df) < 50: return None
    
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    price = close.iloc[-1]

    # 1. Liquidity Floor
    avg_vol = volume.rolling(20).mean().iloc[-1]
    if avg_vol < MIN_VOL_FLOOR or price < MIN_PRICE:
        return None

    # 2. ATR & Risk Logic (NaN Fix)
    atr = ta.atr(high, low, close, length=14)
    if atr is not None and not atr.dropna().empty:
        current_atr = atr.dropna().iloc[-1]
    else:
        current_atr = price * 0.02 # Fallback 2%

    # Safety: Prevent zero ATR
    if current_atr < (price * 0.005): 
        current_atr = price * 0.005

    stop_loss = round(price - (2 * current_atr), 1)
    target_price = round(price + (4 * current_atr), 1)

    # 3. Score Components
    score = 0
    
    # RSI
    rsi = ta.rsi(close, length=14)
    rsi_val = int(rsi.dropna().iloc[-1]) if rsi is not None else 50
    if rsi_val >= 70: score += 30
    elif rsi_val >= 60: score += 20
    elif rsi_val >= 50: score += 10

    # Trend (EMA)
    ema = ta.ema(close, length=20)
    ema_val = ema.dropna().iloc[-1] if ema is not None else price
    stretch = (price - ema_val) / ema_val * 100
    
    if stretch > 5: score -= 15       
    elif stretch < -2: score += 10    
    elif stretch > 0: score += 25     

    # Volume
    vol_ratio = round(volume.iloc[-1] / avg_vol, 2)
    if vol_ratio >= 2.5: score += 20
    elif vol_ratio >= 1.5: score += 10

    # Volatility (Safe Column Selection)
    bb = ta.bbands(close, length=20, std=2)
    kc = ta.kc(high, low, close, length=20, scalar=1.5)
    
    squeeze_now = False
    breakout = False
    
    if bb is not None and kc is not None:
        try:
            # Deterministic Selection (No .item() crash)
            bbu = bb.filter(like="BBU").iloc[-1, 0]
            bbl = bb.filter(like="BBL").iloc[-1, 0]
            kcu = kc.filter(like="KCU").iloc[-1, 0]
            kcl = kc.filter(like="KCL").iloc[-1, 0]

            squeeze_now = (bbu < kcu) and (bbl > kcl)
            breakout = price > bbu
        except:
            pass

    if squeeze_now: score += 10
    if breakout: score += 10
    
    # Memory Bonus
    if was_squeezing and not squeeze_now and breakout:
        score += 15

    return {
        "score": min(100, max(0, score)),
        "vol_ratio": vol_ratio,
        "squeeze": squeeze_now,
        "breakout": breakout,
        "sl": stop_loss,
        "tgt": target_price,
        "price": price
    }

# ==========================================================
# MORNING RUN (BATCH PROCESS)
# ==========================================================
def morning_run():
    print("🚀 Starting Morning Run...")
    
    # 🛡️ FIX 2: Midnight Gate (Don't run before 8 AM IST)
    now = ist_now()
    if now.hour < 8:
        print("💤 Too early. Sleeping until 8 AM.")
        return

    try:
        # 1. FETCH STATE & HISTORY
        raw_state = state_ws.get_all_records()
        state_map = {r["key"]: r["value"] for r in raw_state}
        
        if state_map.get(STATE_RUN_KEY) == ist_today():
            print("✅ Already ran today.")
            return

        # Smart History Check (30 Day Window)
        hist_data = history_ws.get_all_values()
        open_positions = set()
        cutoff_date = (ist_now() - datetime.timedelta(days=30)).date()
        
        for row in hist_data[1:]:
            # [date, symbol, action, ...]
            if len(row) > 1 and row[2] == "BUY": 
                try:
                    entry_date = datetime.date.fromisoformat(row[0])
                    if entry_date >= cutoff_date:
                        open_positions.add(row[1])
                except:
                    pass # Ignore bad dates

        # Fetch Memory
        mem_rows = memory_ws.get_all_values()
        squeezing_yesterday = set()
        if len(mem_rows) > 1:
            last_csv = mem_rows[-1][1] 
            if last_csv:
                squeezing_yesterday = set(last_csv.split(","))

    except Exception as e:
        print(f"🔥 State Init Error: {e}")
        return

    send_msg("🌅 *Institutional Scan Initiated*")

    # 2. NEWS & MACRO
    news = fetch_market_news()
    send_msg(format_news_block(news))
    
    macro_risk_off = False
    if news.get("noise", 0) > 0.75:
        macro_risk_off = True
        send_msg("⚠️ *Macro Alert:* High Noise. Filtering aggressive setups.")

    # 3. PROCESS DATA
    symbols, sector_map = load_nifty_200_and_sectors()
    data = batch_download(symbols)
    
    results = []
    squeezing_today = []

    for sym, df in data.items():
        was_sq = sym in squeezing_yesterday
        res = score_stock(df, was_sq)
        
        if not res: continue

        res['symbol'] = sym
        res['sector'] = sector_map.get(sym, "Unknown")
        
        if res['squeeze']: 
            squeezing_today.append(sym)

        if res['score'] >= 55:
            results.append(res)

    # 4. SORT & FILTER
    results.sort(key=lambda x: x['score'], reverse=True)
    
    final_picks = []
    sector_counts = {}
    new_log_rows = []

    for r in results:
        bucket = "WATCHLIST"
        
        if r['score'] >= 80 and r['breakout'] and not macro_risk_off:
            bucket = "STRONG_BUY"
        elif r['score'] >= 65:
            bucket = "BUY"
            
        if "BUY" in bucket:
            sec = r['sector']
            count = sector_counts.get(sec, 0)
            if count >= SECTOR_CAP:
                bucket = "WATCHLIST"
            else:
                sector_counts[sec] = count + 1

        r['bucket'] = bucket
        final_picks.append(r)
        
        # Log Logic (Respects 30-day window)
        if "BUY" in bucket and r['symbol'] not in open_positions:
            new_log_rows.append([
                ist_today(), 
                r['symbol'], 
                bucket, 
                r['price'], 
                r['sl'], 
                r['tgt'], 
                "OPEN"
            ])
            open_positions.add(r['symbol'])

    # 5. TELEGRAM
    buys = [x for x in final_picks if "BUY" in x['bucket']]
    
    if buys:
        msg = ["🏛️ *Actionable Setups*", ""]
        for b in buys[:5]:
            icon = "🚀" if b['bucket'] == "STRONG_BUY" else "✅"
            msg.append(f"{icon} *{b['symbol']}* (Score: {b['score']})")
            msg.append(f"   📊 {b['sector']}")
            msg.append(f"   🎯 {b['tgt']} | 🛑 {b['sl']}")
            msg.append("")
        send_msg("\n".join(msg))
    else:
        send_msg("⚠️ *Risk-Off Day*: No high-quality setups found.")

    # 6. BATCH WRITES
    try:
        stocks_ws.clear()
        stocks_ws.append_row(["symbol","score","bucket","vol_ratio","stop_loss","target","sector"])
        stock_rows = [[
            p['symbol'], p['score'], p['bucket'], p['vol_ratio'], p['sl'], p['tgt'], p['sector']
        ] for p in final_picks]
        
        if stock_rows:
            stocks_ws.append_rows(stock_rows)

        if new_log_rows:
            history_ws.append_rows(new_log_rows)

        csv_string = ",".join(squeezing_today)
        memory_ws.append_row([ist_today(), csv_string])

        try:
            cell = state_ws.find(STATE_RUN_KEY)
            state_ws.update_cell(cell.row, 2, ist_today())
        except:
            state_ws.append_row([STATE_RUN_KEY, ist_today()])
            
    except Exception as e:
        print(f"🔥 Batch Write Error: {e}")
        send_msg(f"⚠️ Data Save Error: {e}")

    print("🏁 Run Complete.")

# ==========================================================
# BOOTSTRAP
# ==========================================================
if __name__ == "__main__":
    keep_alive()
    print("🤖 Bot Online. Waiting for morning trigger...")
    while True:
        try:
            morning_run()
        except Exception as e:
            # 🛡️ FIX 3: Anti-Death Spiral (15 min sleep on crash)
            print(f"💀 CRITICAL CRASH: {e}")
            send_msg(f"💀 Bot Crash Alert: {e}") 
            time.sleep(900) 
        
        time.sleep(3600)
        
