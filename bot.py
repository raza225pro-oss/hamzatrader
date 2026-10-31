import os
import asyncio
import threading
import logging
from flask import Flask
import ccxt
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Flask Setup ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Hamza Trader Quotex Live Scanner Bot is running!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- Bot & Branding Setup ---
TOKEN = os.getenv("BOT_TOKEN")
raw_ids = os.getenv("ALLOWED_IDS", "")
ALLOWED_IDS = [int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit()]
PKT = timezone(timedelta(hours=5))

# --- Quotex Live Forex, Commodities & Indices (via Yahoo Finance) ---
QUOTEX_FOREX_MARKETS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "CAD=X",
    "USD/CHF": "CHF=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X",
    "EUR/AUD": "EURAUD=X",
    "EUR/CAD": "EURCAD=X",
    "GBP/AUD": "GBPAUD=X",
    "GBP/CAD": "GBPCAD=X",
    "GBP/CHF": "GBPCHF=X",
    "AUD/CAD": "AUDCAD=X",
    "AUD/CHF": "AUDCHF=X",
    "CAD/JPY": "CADJPY=X",
    "CHF/JPY": "CHFJPY=X",
    "EUR/CHF": "EURCHF=X",
    "NZD/JPY": "NZDJPY=X",
    "XAU/USD (Gold)": "GC=F",
    "XAG/USD (Silver)": "SI=F",
    "UKBrent (Oil)": "CL=F"
}

# --- Quotex Live Crypto Markets (24/7 via CCXT) ---
QUOTEX_CRYPTO_MARKETS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOGE/USDT', 'LTC/USDT', 'DOT/USDT', 'LINK/USDT',
    'AVAX/USDT', 'TRX/USDT', 'TON/USDT', 'SHIB/USDT', 'PEPE/USDT'
]

def init_exchange():
    for ex_name in ['binance', 'bybit', 'kucoin']:
        try:
            ex = getattr(ccxt, ex_name)({'enableRateLimit': True})
            ex.fetch_ohlcv('BTC/USDT', timeframe='1m', limit=5)
            return ex
        except Exception:
            continue
    return ccxt.bybit({'enableRateLimit': True})

exchange = init_exchange()

def get_next_entry_timing():
    """Calculates exact current time with seconds and ensures at least 20s preparation time for the next candle"""
    now_pkt = datetime.now(PKT)
    current_time_str = now_pkt.strftime("%I:%M:%S %p")
    
    # Calculate next exact minute boundary (00 seconds)
    next_min = (now_pkt + timedelta(minutes=1)).replace(second=0, microsecond=0)
    wait_seconds = int((next_min - now_pkt).total_seconds())
    
    # Agar candle close hone mein 20 sec se kam hain to us se agle minute ki trade dega
    if wait_seconds < 20:
        next_min = next_min + timedelta(minutes=1)
        wait_seconds = int((next_min - now_pkt).total_seconds())
        
    entry_time_str = next_min.strftime("%I:%M:%S %p")
    return current_time_str, entry_time_str, wait_seconds

def evaluate_high_accuracy_strategies(df, market_name):
    """Upgraded Sniper Accuracy Engine with Supertrend, S/R Zones, Candle Dominance & Anti-Chop Filters"""
    try:
        if df is None or len(df) < 55:
            return None

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
        if len(df) < 55:
            return None

        # 1. EMAs (5, 9, 21, 50)
        df['ema_5'] = df['close'].ewm(span=5, adjust=False).mean()
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()

        # 2. Fast Binary RSI (7)
        delta = df['close'].diff()
        gain = delta.clip(lower=0).ewm(alpha=1/7, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/7, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        df['rsi'] = (100 - (100 / (1 + rs))).fillna(50)

        # 3. MACD (12, 26, 9)
        df['macd'] = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']

        # 4. Bollinger Bands (20, 2)
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std'] = df['close'].rolling(window=20).std()
        df['upper_band'] = df['sma_20'] + (df['std'] * 2)
        df['lower_band'] = df['sma_20'] - (df['std'] * 2)

        # 5. Stochastic Oscillator (14, 3) & Williams %R (14)
        df['low_14'] = df['low'].rolling(window=14).min()
        df['high_14'] = df['high'].rolling(window=14).max()
        denom = (df['high_14'] - df['low_14']).replace(0, np.nan)
        df['stoch_k'] = (100 * ((df['close'] - df['low_14']) / denom)).fillna(50)
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean().fillna(50)
        df['will_r'] = (-100 * ((df['high_14'] - df['close']) / denom)).fillna(-50)

        # 6. ATR (14) & ADX (14)
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean()

        up_move = df['high'] - df['high'].shift(1)
        down_move = df['low'].shift(1) - df['low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / df['atr'].replace(0, np.nan))
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / df['atr'].replace(0, np.nan))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean().fillna(20)

        # 7. Dynamic Support & Resistance (Last 20 Candles High/Low)
        df['res_20'] = df['high'].shift(1).rolling(window=20).max()
        df['sup_20'] = df['low'].shift(1).rolling(window=20).min()

        # 8. Supertrend Approximation (Factor 3.0)
        hl2 = (df['high'] + df['low']) / 2
        df['st_upper'] = hl2 + (2.5 * df['atr'])
        df['st_lower'] = hl2 - (2.5 * df['atr'])

        closed = df.iloc[-2]
        prev = df.iloc[-3]
        live = df.iloc[-1]

        # --- STRICT ANTI-CHOP & DOJI FILTERS ---
        candle_range = closed['high'] - closed['low']
        candle_body = abs(closed['close'] - closed['open'])

        # 1. Reject dead or low-volatility candles
        if pd.isna(closed['atr']) or closed['atr'] == 0 or candle_range < (closed['atr'] * 0.45):
            return None

        # 2. Reject indecisive spinning tops / weak body candles (unless clear hammer/pinbar)
        body_ratio = candle_body / candle_range if candle_range > 0 else 0
        lower_wick = min(closed['close'], closed['open']) - closed['low']
        upper_wick = closed['high'] - max(closed['close'], closed['open'])
        is_pinbar = (lower_wick > candle_body * 2.0) or (upper_wick > candle_body * 2.0)

        if body_ratio < 0.35 and not is_pinbar:
            return None

        # 3. Reject flat/ranging market (ADX too weak AND Bollinger Bands squeezed)
        if live['adx'] < 20 and (closed['upper_band'] - closed['lower_band']) < (closed['atr'] * 2.2):
            return None

        up_score = 0
        down_score = 0

        # --- SNIPER CONFLUENCE SCORING ---

        # 1. Multi-EMA Trend Alignment + Supertrend Filter
        if closed['close'] > closed['ema_21'] > closed['ema_50'] and closed['ema_5'] > closed['ema_9']:
            up_score += 20
        elif closed['close'] < closed['ema_21'] < closed['ema_50'] and closed['ema_5'] < closed['ema_9']:
            down_score += 20

        # 2. Confirmed EMA 5/9 Fresh Crossover
        if prev['ema_5'] <= prev['ema_9'] and closed['ema_5'] > closed['ema_9'] and live['close'] > closed['ema_5']:
            up_score += 18
        elif prev['ema_5'] >= prev['ema_9'] and closed['ema_5'] < closed['ema_9'] and live['close'] < closed['ema_5']:
            down_score += 18

        # 3. Strong Momentum Candle Continuation (Marubozu / Breakout)
        if closed['close'] > closed['open'] and body_ratio >= 0.65 and closed['close'] > closed['ema_9']:
            up_score += 15
        elif closed['close'] < closed['open'] and body_ratio >= 0.65 and closed['close'] < closed['ema_9']:
            down_score += 15

        # 4. Bollinger Band + Support/Resistance Sureshot Rejection
        if closed['low'] <= closed['lower_band'] and closed['close'] > closed['open'] and closed['rsi'] < 35:
            up_score += 22
        elif closed['high'] >= closed['upper_band'] and closed['close'] < closed['open'] and closed['rsi'] > 65:
            down_score += 22

        # 5. Support / Resistance Level Bounce
        if closed['low'] <= closed['sup_20'] * 1.0003 and closed['close'] > closed['sup_20'] and lower_wick > candle_body:
            up_score += 16
        elif closed['high'] >= closed['res_20'] * 0.9997 and closed['close'] < closed['res_20'] and upper_wick > candle_body:
            down_score += 16

        # 6. RSI (7) + Stochastic + Williams %R Triple Oscillator Hook
        if closed['rsi'] < 28 and live['rsi'] > closed['rsi'] and closed['stoch_k'] < 25 and closed['will_r'] < -78:
            up_score += 20
        elif closed['rsi'] > 72 and live['rsi'] < closed['rsi'] and closed['stoch_k'] > 75 and closed['will_r'] > -22:
            down_score += 20
        elif 53 <= closed['rsi'] <= 67 and closed['rsi'] > prev['rsi'] and closed['macd_hist'] > prev['macd_hist']:
            up_score += 12
        elif 33 <= closed['rsi'] <= 47 and closed['rsi'] < prev['rsi'] and closed['macd_hist'] < prev['macd_hist']:
            down_score += 12

        # 7. Confirmed Engulfing Pattern
        if closed['close'] > closed['open'] and prev['close'] < prev['open'] and closed['close'] > prev['open'] and candle_body > abs(prev['close'] - prev['open']):
            up_score += 16
        elif closed['close'] < closed['open'] and prev['close'] > prev['open'] and closed['close'] < prev['open'] and candle_body > abs(prev['close'] - prev['open']):
            down_score += 16

        # 8. ADX Trend Power Confirmation
        if live['adx'] >= 26:
            if closed['ema_5'] > closed['ema_21']:
                up_score += 10
            elif closed['ema_5'] < closed['ema_21']:
                down_score += 10

        # 9. Live Candle Pressure Check (Block signal if live candle is strongly reversing against setup)
        if up_score > down_score and live['close'] < closed['low']:
            return None
        if down_score > up_score and live['close'] > closed['high']:
            return None

        # Require high minimum score (38+) and clear dominance gap (20+)
        score_diff = abs(up_score - down_score)
        max_score = max(up_score, down_score)

        if max_score < 38 or score_diff < 20:
            return None

        if up_score > down_score:
            direction = "UP 🟢⬆️"
        else:
            direction = "DOWN 🔴⬇️"

        return {
            'symbol': market_name,
            'direction': direction,
            'score': max_score
        }
    except Exception as e:
        logger.error(f"Strategy error on {market_name}: {e}")
        return None

def scan_all_quotex_markets():
    signals = []

    # 1. Scan Quotex Live Forex & Commodities via Yahoo Finance
    try:
        yf_tickers = list(QUOTEX_FOREX_MARKETS.values())
        data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
        
        for market_name, yf_sym in QUOTEX_FOREX_MARKETS.items():
            try:
                if yf_sym in data and not data[yf_sym].empty:
                    sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                    if len(sub_df) >= 55:
                        # Ensure market is actively moving (not closed on weekend)
                        if sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                            clean_df = pd.DataFrame({
                                'open': sub_df['Open'].values,
                                'high': sub_df['High'].values,
                                'low': sub_df['Low'].values,
                                'close': sub_df['Close'].values,
                                'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                            })
                            res = evaluate_high_accuracy_strategies(clean_df, market_name)
                            if res:
                                res['is_forex'] = True
                                signals.append(res)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Yahoo Finance Forex fetch issue: {e}")

    # 2. Scan Quotex Live Crypto Pairs (24/7 Active)
    for sym in QUOTEX_CRYPTO_MARKETS:
        try:
            ohlcv = exchange.fetch_ohlcv(sym, timeframe='1m', limit=70)
            if ohlcv and len(ohlcv) >= 55:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                clean_name = sym.replace('/USDT', '/USD')
                res = evaluate_high_accuracy_strategies(df, clean_name)
                if res:
                    res['is_forex'] = False
                    signals.append(res)
        except Exception:
            continue

    return signals

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🔥 *HAMZA TRADER QUOTEX BOT* 🔥\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ *Timeframe:* `1 Minute (M1)`\n"
        "🎯 *Mode:* `Sniper High-Accuracy`\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "👉 Signal lene ke liye `/scan` send karein."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text(
        "🔍 *HAMZA TRADER SCANNING...*\n"
        "Best sureshot signal filter ho raha hai, wait karein...",
        parse_mode='Markdown'
    )

    found_signals = await asyncio.to_thread(scan_all_quotex_markets)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if not found_signals:
        await update.message.reply_text(
            "⚠️ *Abhi koi strong signal nahi mila!*\n"
            "Market choppy hai. 20-30 seconds baad dobara `/scan` karein.",
            parse_mode='Markdown'
        )
        return

    # Sort by highest score first, prioritizing Live Forex when open
    found_signals.sort(key=lambda x: (1 if x.get('is_forex') else 0, x['score']), reverse=True)

    # Top 3 strongest signals
    top_signals = found_signals[:3]
    current_time_str, entry_time_str, wait_seconds = get_next_entry_timing()

    for sig in top_signals:
        short_msg = (
            f"🔥 *HAMZA TRADER SIGNAL* 🔥\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Pair:* `{sig['symbol']}`\n"
            f"⏳ *Time:* `1 Minute`\n"
            f"🎯 *Direction:* *{sig['direction']}*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🕒 *Scan Time:* `{current_time_str}`\n"
            f"⏰ *Trade Time:* `{entry_time_str}` _(Wait {wait_seconds}s)_"
        )

        await update.message.reply_text(short_msg, parse_mode='Markdown')
        await asyncio.sleep(0.3)

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ Access Denied. Yeh bot sirf Hamza Trader ke authorized users ke liye hai.")

def run_bot():
    logger.info("Starting Hamza Trader Quotex Telegram Bot...")
    telegram_app = Application.builder().token(TOKEN).build()

    if ALLOWED_IDS:
        telegram_app.add_handler(MessageHandler(~filters.User(ALLOWED_IDS), unauthorized))

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))

    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    run_bot()
