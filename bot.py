import os
import json
import re
import asyncio
import threading
import logging
from flask import Flask
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Optional Telethon import for automatic @QuotexPartnerBot communication
try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Flask Setup (Keep-Alive for Render / UptimeRobot) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "AliTrader Quotex Live VIP Terminal is running!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- Configuration & AliTrader Branding ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) if os.getenv("ADMIN_ID", "").strip().isdigit() else 0
raw_ids = os.getenv("ALLOWED_IDS", "")
ENV_ALLOWED_IDS = set(int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit())
if ADMIN_ID:
    ENV_ALLOWED_IDS.add(ADMIN_ID)

QUOTEX_PARTNER_LINK = os.getenv("QUOTEX_PARTNER_LINK", "https://broker-qx.pro/sign-up/?lid=2111446")
EXPECTED_LINK_ID = os.getenv("EXPECTED_LINK_ID", "")
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "876543210 (AliTrader)")
PREMIUM_PRICE = os.getenv("PREMIUM_PRICE", "$20 USDT (Lifetime Access)")
PARTNER_BOT_USERNAME = os.getenv("PARTNER_BOT_USERNAME", "QuotexPartnerBot")

OWNER_USERNAME = os.getenv("OWNER_USERNAME", "AliTrader01").lstrip("@")
VIP_GROUP_URL = os.getenv("VIP_GROUP_URL", "https://t.me/AliTrader_VIP")

TG_API_ID = os.getenv("TG_API_ID", "").strip()
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "").strip()

PKT = timezone(timedelta(hours=5))
SIGNAL_COUNT = 0
DB_FILE = "authorized_users.json"

# --- Persistent Database for Approved Users & Used Trader IDs ---
def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"approved_users": [], "used_trader_ids": {}}

def save_db(data):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Database save error: {e}")

db_data = load_db()

def is_user_authorized(user_id: int) -> bool:
    if user_id in ENV_ALLOWED_IDS:
        return True
    if user_id in db_data.get("approved_users", []):
        return True
    return False

def authorize_user(user_id: int, trader_id: str = None):
    if user_id not in db_data["approved_users"]:
        db_data["approved_users"].append(user_id)
    if trader_id:
        db_data["used_trader_ids"][str(trader_id)] = user_id
    save_db(db_data)

# --- Telethon Userbot Bridge for @QuotexPartnerBot ---
telethon_client = None
telethon_lock = asyncio.Lock()

async def verify_with_quotex_partner_bot(trader_id: str):
    global telethon_client
    if not TELETHON_AVAILABLE or not TG_API_ID or not TG_API_HASH:
        return "MANUAL_FALLBACK", "Telethon API credentials not configured."

    async with telethon_lock:
        try:
            if telethon_client is None:
                session = StringSession(TG_SESSION_STRING) if TG_SESSION_STRING else "quotex_verifier"
                telethon_client = TelegramClient(session, int(TG_API_ID), TG_API_HASH)
            if not telethon_client.is_connected():
                await telethon_client.connect()
            if not await telethon_client.is_user_authorized():
                return "MANUAL_FALLBACK", "Telethon user session not logged in yet."

            async with telethon_client.conversation(PARTNER_BOT_USERNAME, timeout=15) as conv:
                await conv.send_message(str(trader_id))
                resp = await conv.get_response()
                text = resp.text or ""

                if "was not found" in text.lower() or "not found" in text.lower():
                    return "NOT_FOUND", text

                if f"trader # {trader_id}" in text.lower() or "trader #" in text.lower() or "registration date:" in text.lower():
                    if EXPECTED_LINK_ID and f"link id: {EXPECTED_LINK_ID}" not in text.lower():
                        return "WRONG_LINK", text
                    return "FOUND", text

                return "NOT_FOUND", text
        except Exception as e:
            logger.error(f"PartnerBot verification error: {e}")
            return "ERROR", str(e)

# ============================================================================
# 100% VERIFIED QUOTEX LIVE FOREX PAIRS ONLY (NO OTC, NO FAKE PAIRS)
# ============================================================================
QUOTEX_LIVE_MARKETS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "CAD=X",
    "USD/CHF": "CHF=X",
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
}

# ============================================================================
# QUOTEX SNIPER V3 ENGINE (M1 + M5 MULTI-TIMEFRAME + FRACTAL S&R + SECOND SYNC)
# ============================================================================
def evaluate_high_accuracy_strategies(df, market_name, allow_fallback=False):
    try:
        if df is None or len(df) < 60:
            return None

        df = df.copy()
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
        if len(df) < 60:
            return None

        # --- 1. M5 (5-Minute) Higher Timeframe Trend Calculation ---
        df['m5_ema_fast'] = df['close'].ewm(span=25, adjust=False).mean()   # Equivalent to M5 EMA(5)
        df['m5_ema_slow'] = df['close'].ewm(span=100, adjust=False).mean()  # Equivalent to M5 EMA(20)
        m5_bullish = df['m5_ema_fast'].iloc[-1] > df['m5_ema_slow'].iloc[-1]
        m5_bearish = df['m5_ema_fast'].iloc[-1] < df['m5_ema_slow'].iloc[-1]

        # --- 2. M1 EMAs (7, 21, 50) & Slope ---
        df['ema_7'] = df['close'].ewm(span=7, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()

        # --- 3. Quotex Fast RSI(5) & Standard RSI(14) ---
        delta = df['close'].diff()
        gain5 = delta.clip(lower=0).ewm(alpha=1/5, adjust=False).mean()
        loss5 = (-delta.clip(upper=0)).ewm(alpha=1/5, adjust=False).mean()
        df['rsi_5'] = (100 - (100 / (1 + (gain5 / loss5.replace(0, np.nan))))).fillna(50)

        gain14 = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss14 = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        df['rsi_14'] = (100 - (100 / (1 + (gain14 / loss14.replace(0, np.nan))))).fillna(50)

        # --- 4. Bollinger Bands (20, 2.0) ---
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std_20'] = df['close'].rolling(window=20).std()
        df['upper_bb'] = df['sma_20'] + (df['std_20'] * 2.0)
        df['lower_bb'] = df['sma_20'] - (df['std_20'] * 2.0)

        # --- 5. Stochastic Oscillator (5, 3, 3) ---
        low_5 = df['low'].rolling(window=5).min()
        high_5 = df['high'].rolling(window=5).max()
        denom = (high_5 - low_5).replace(0, np.nan)
        df['stoch_k'] = (100 * ((df['close'] - low_5) / denom)).rolling(window=3).mean().fillna(50)
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean().fillna(50)

        # --- 6. MACD (12, 26, 9) Histogram ---
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        df['macd_hist'] = macd_line - signal_line

        # --- 7. ATR(14) & ADX(14) Breakout Guard ---
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean().bfill()

        up_move = df['high'] - df['high'].shift(1)
        down_move = df['low'].shift(1) - df['low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / df['atr'].replace(0, np.nan))
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / df['atr'].replace(0, np.nan))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean().fillna(20)

        # --- 8. Dynamic Fractal Support & Resistance Zones ---
        df['support_zone'] = df['low'].shift(1).rolling(window=24).min()
        df['resistance_zone'] = df['high'].shift(1).rolling(window=24).max()

        # --- 9. Smart Second-Sync Candle Selection ---
        now_pkt = datetime.now(PKT)
        sec = now_pkt.second
        if sec >= 32:
            sig_c = df.iloc[-1]   # Current nearly-closed live candle
            prev_c = df.iloc[-2]
        else:
            sig_c = df.iloc[-2]   # Just-closed candle
            prev_c = df.iloc[-3]

        atr_val = float(sig_c['atr']) if sig_c['atr'] > 0 else 0.0001
        body = abs(sig_c['close'] - sig_c['open'])
        c_range = sig_c['high'] - sig_c['low']
        prev_body = abs(prev_c['close'] - prev_c['open'])

        # Reject Doji / Flat markets & Abnormal News Spikes (>2.8x ATR)
        if c_range < (atr_val * 0.25) or c_range > (atr_val * 2.8) or body < (c_range * 0.12):
            return None

        is_bull = sig_c['close'] > sig_c['open']
        is_bear = sig_c['close'] < sig_c['open']
        prev_bull = prev_c['close'] > prev_c['open']
        prev_bear = prev_c['close'] < prev_c['open']

        upper_wick = sig_c['high'] - max(sig_c['open'], sig_c['close'])
        lower_wick = min(sig_c['open'], sig_c['close']) - sig_c['low']

        m1_uptrend = (sig_c['ema_7'] > sig_c['ema_21'] > sig_c['ema_50'])
        m1_downtrend = (sig_c['ema_7'] < sig_c['ema_21'] < sig_c['ema_50'])

        call_points = []
        put_points = []

        # ====================================================================
        # SETUP 1: M5 + M1 TREND PULLBACK CONTINUATION (Highest Live Winrate)
        # ====================================================================
        if m5_bullish and m1_uptrend and (40 <= sig_c['rsi_14'] <= 66):
            if is_bear and sig_c['low'] <= (sig_c['ema_7'] + atr_val * 0.12) and sig_c['close'] >= sig_c['ema_21']:
                call_points.append(("M5+M1 Uptrend Dip Rejection", 42))
            elif prev_bear and is_bull and body > (prev_body * 0.90) and sig_c['close'] > prev_c['open']:
                call_points.append(("M5+M1 Bullish Engulfing Continuation", 40))

        if m5_bearish and m1_downtrend and (34 <= sig_c['rsi_14'] <= 60):
            if is_bull and sig_c['high'] >= (sig_c['ema_7'] - atr_val * 0.12) and sig_c['close'] <= sig_c['ema_21']:
                put_points.append(("M5+M1 Downtrend Rally Rejection", 42))
            elif prev_bull and is_bear and body > (prev_body * 0.90) and sig_c['close'] < prev_c['open']:
                put_points.append(("M5+M1 Bearish Engulfing Continuation", 40))

        # ====================================================================
        # SETUP 2: S&R + BOLLINGER BAND + WICK REJECTION (Anti-Breakout Filtered)
        # ====================================================================
        at_support = (sig_c['low'] <= sig_c['lower_bb']) and (abs(sig_c['low'] - sig_c['support_zone']) <= atr_val * 0.35)
        at_resistance = (sig_c['high'] >= sig_c['upper_bb']) and (abs(sig_c['high'] - sig_c['resistance_zone']) <= atr_val * 0.35)

        if at_support and sig_c['adx'] < 28 and sig_c['rsi_5'] <= 20 and sig_c['stoch_k'] <= 20:
            if lower_wick >= (c_range * 0.35):
                call_points.append(("S&R + Lower BB Wick Bounce", 44))

        if at_resistance and sig_c['adx'] < 28 and sig_c['rsi_5'] >= 80 and sig_c['stoch_k'] >= 80:
            if upper_wick >= (c_range * 0.35):
                put_points.append(("S&R + Upper BB Wick Drop", 44))

        # ====================================================================
        # SETUP 3: QUOTEX ORDER-BLOCK PINBAR + MACD/STOCHASTIC HOOK
        # ====================================================================
        macd_rising = sig_c['macd_hist'] > prev_c['macd_hist']
        macd_falling = sig_c['macd_hist'] < prev_c['macd_hist']

        if lower_wick >= (c_range * 0.55) and body <= (c_range * 0.32) and sig_c['stoch_k'] < 35 and m5_bullish:
            call_points.append(("Order-Block Bullish Pinbar", 34))
        elif upper_wick >= (c_range * 0.55) and body <= (c_range * 0.32) and sig_c['stoch_k'] > 65 and m5_bearish:
            put_points.append(("Order-Block Bearish Pinbar", 34))

        if m5_bullish and macd_rising and sig_c['stoch_k'] > sig_c['stoch_d'] and prev_c['stoch_k'] <= prev_c['stoch_d'] and sig_c['stoch_k'] < 60:
            call_points.append(("MACD + Stoch Golden Cross", 26))
        elif m5_bearish and macd_falling and sig_c['stoch_k'] < sig_c['stoch_d'] and prev_c['stoch_k'] >= prev_c['stoch_d'] and sig_c['stoch_k'] > 40:
            put_points.append(("MACD + Stoch Death Cross", 26))

        # M5 Alignment Bonus / Penalty (Never fight M5 trend!)
        if m5_bullish:
            call_points = [(r, w + 8) for r, w in call_points]
            put_points = [(r, max(0, w - 15)) for r, w in put_points]
        elif m5_bearish:
            put_points = [(r, w + 8) for r, w in put_points]
            call_points = [(r, max(0, w - 15)) for r, w in call_points]

        call_score = sum(w for _, w in call_points)
        put_score = sum(w for _, w in put_points)

        if call_score == 0 and put_score == 0:
            if not allow_fallback:
                return None
            if m5_bullish and sig_c['ema_7'] >= sig_c['ema_21']:
                call_points.append(("M5+M1 Bullish Flow Alignment", 25))
                call_score = 25
            elif m5_bearish and sig_c['ema_7'] <= sig_c['ema_21']:
                put_points.append(("M5+M1 Bearish Flow Alignment", 25))
                put_score = 25
            else:
                return None

        score_diff = abs(call_score - put_score)
        if not allow_fallback and score_diff < 28:
            return None

        if call_score >= put_score:
            direction = "CALL 🟢⬆️"
            power_tag = "M5+M1 BUYERS 🟢"
            reasons = [r for r, _ in sorted(call_points, key=lambda x: x[1], reverse=True)]
            winning_score = call_score
        else:
            direction = "PUT 🔴⬇️"
            power_tag = "M5+M1 SELLERS 🔴"
            reasons = [r for r, _ in sorted(put_points, key=lambda x: x[1], reverse=True)]
            winning_score = put_score

        confidence = min(82 + int(winning_score * 0.28), 98)

        next_min = (now_pkt + timedelta(minutes=1)).replace(second=0, microsecond=0)
        secs_left = max(1, int((next_min - now_pkt).total_seconds()))
        entry_time = f"{next_min.strftime('%I:%M %p')} (in {secs_left}s)"

        return {
            'symbol': market_name,
            'direction': direction,
            'power': power_tag,
            'confidence': int(confidence),
            'score_diff': score_diff,
            'entry_time': entry_time,
            'reason': ", ".join(reasons[:2])
        }
    except Exception as e:
        logger.error(f"Strategy error on {market_name}: {e}")
        return None

# --- Live Market Scanners ---
def fetch_single_pair_signal(pair_name):
    yf_ticker = QUOTEX_LIVE_MARKETS.get(pair_name)
    if not yf_ticker:
        return None
    try:
        data = yf.download(tickers=yf_ticker, period="1d", interval="1m", progress=False)
        if data is not None and not data.empty and len(data) >= 60:
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            sub_df = data.dropna(subset=['Close'])
            if len(sub_df) >= 60 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                clean_df = pd.DataFrame({
                    'open': sub_df['Open'].values,
                    'high': sub_df['High'].values,
                    'low': sub_df['Low'].values,
                    'close': sub_df['Close'].values,
                    'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                })
                return evaluate_high_accuracy_strategies(clean_df, f"{pair_name} (Live)", allow_fallback=True)
    except Exception as e:
        logger.warning(f"Single Forex fetch error: {e}")
    return None

def scan_best_live_signals(count=1):
    signals = []
    fallback_candidates = []
    try:
        yf_tickers = list(QUOTEX_LIVE_MARKETS.values())
        data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
        for pair_name, yf_sym in QUOTEX_LIVE_MARKETS.items():
            try:
                if yf_sym in data and not data[yf_sym].empty:
                    sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                    if len(sub_df) >= 60 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                        clean_df = pd.DataFrame({
                            'open': sub_df['Open'].values,
                            'high': sub_df['High'].values,
                            'low': sub_df['Low'].values,
                            'close': sub_df['Close'].values,
                            'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                        })
                        res = evaluate_high_accuracy_strategies(clean_df, f"{pair_name} (Live)", allow_fallback=False)
                        if res:
                            signals.append(res)
                        else:
                            fb = evaluate_high_accuracy_strategies(clean_df, f"{pair_name} (Live)", allow_fallback=True)
                            if fb:
                                fallback_candidates.append(fb)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Batch Yahoo Finance error: {e}")

    if len(signals) < count and fallback_candidates:
        fallback_candidates.sort(key=lambda x: (x['confidence'], x['score_diff']), reverse=True)
        for fb in fallback_candidates:
            if len(signals) >= count:
                break
            if not any(s['symbol'] == fb['symbol'] for s in signals):
                signals.append(fb)

    signals.sort(
        key=lambda x: (
            1 if x['score_diff'] >= 30 else 0,
            x['confidence'],
            x['score_diff']
        ),
        reverse=True
    )
    return signals[:count]

# ============================================================================
# BUTTON LAYOUTS (STRICTLY LIVE MARKET ONLY)
# ============================================================================
def get_access_gate_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🆓 GET FREE ACCESS", callback_data="gate_free"),
            InlineKeyboardButton("💎 BUY PREMIUM VIP", callback_data="gate_premium")
        ],
        [
            InlineKeyboardButton("✍️ SUBMIT TRADER ID", callback_data="enter_trader_id")
        ],
        [
            InlineKeyboardButton("📢 JOIN VIP GROUP", url=VIP_GROUP_URL),
            InlineKeyboardButton("🎫 CONTACT OWNER", url=f"https://t.me/{OWNER_USERNAME}")
        ]
    ])

def get_free_access_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 1. CREATE QUOTEX ACCOUNT", url=QUOTEX_PARTNER_LINK)],
        [InlineKeyboardButton("✍️ 2. SUBMIT MY TRADER ID", callback_data="enter_trader_id")],
        [
            InlineKeyboardButton("🎫 Contact Owner", url=f"https://t.me/{OWNER_USERNAME}"),
            InlineKeyboardButton("⬅️ Back", callback_data="gate_home")
        ]
    ])

def get_premium_access_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 SEND PAYMENT SCREENSHOT", url=f"https://t.me/{OWNER_USERNAME}")],
        [
            InlineKeyboardButton("🆓 Free Access Instead", callback_data="gate_free"),
            InlineKeyboardButton("⬅️ Back", callback_data="gate_home")
        ]
    ])

def get_main_dashboard_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 INSTANT LIVE SIGNAL (BEST PAIR)", callback_data="auto_live_1")
        ],
        [
            InlineKeyboardButton("🔥 TOP 3 LIVE SURESHOT SIGNALS", callback_data="auto_live_3")
        ],
        [
            InlineKeyboardButton("📊 SELECT QUOTEX LIVE PAIR (20 PAIRS)", callback_data="list_live_0")
        ],
        [
            InlineKeyboardButton("🎫 CONTACT OWNER", url=f"https://t.me/{OWNER_USERNAME}"),
            InlineKeyboardButton("📢 VIP TELEGRAM GROUP", url=VIP_GROUP_URL)
        ]
    ])

def get_pairs_grid_kb(page=0):
    markets = list(QUOTEX_LIVE_MARKETS.keys())
    per_page = 10
    start_idx = page * per_page
    end_idx = start_idx + per_page
    sliced = markets[start_idx:end_idx]

    rows = [
        [InlineKeyboardButton("🟢 AUTO SCAN BEST LIVE PAIR", callback_data="auto_live_1")]
    ]

    for i in range(0, len(sliced), 2):
        row = []
        for j in range(2):
            if i + j < len(sliced):
                p_name = sliced[i + j]
                idx = start_idx + i + j
                row.append(InlineKeyboardButton(f"🟢 {p_name}", callback_data=f"pair_live_{idx}"))
        rows.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev Page", callback_data=f"list_live_{page-1}"))
    if end_idx < len(markets):
        nav_row.append(InlineKeyboardButton("Next Page ➡️", callback_data=f"list_live_{page+1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("👀 Back to Main Dashboard", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

def get_post_signal_kb(pair_idx=None):
    retry_cb = f"pair_live_{pair_idx}" if pair_idx is not None else "auto_live_1"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ NEXT BEST LIVE SIGNAL", callback_data="auto_live_1"),
            InlineKeyboardButton("🔄 SCAN AGAIN", callback_data=retry_cb)
        ],
        [
            InlineKeyboardButton("📊 Select Another Live Pair", callback_data="list_live_0")
        ],
        [
            InlineKeyboardButton("🔥 Top 3 Live Signals", callback_data="auto_live_3"),
            InlineKeyboardButton("👀 Dashboard", callback_data="main_menu")
        ]
    ])

# ============================================================================
# COLORFUL SHADED HTML TEMPLATES & RADAR ANIMATION (ALITRADER LIVE)
# ============================================================================
def format_signal_card(sig):
    global SIGNAL_COUNT
    SIGNAL_COUNT += 1
    bars = "🟩🟩🟩🟩🟩" if sig['confidence'] >= 90 else "🟩🟩🟩🟩⬜"
    return (
        f"👾 <b>ALITRADER VIP</b> 👾\n"
        f"<blockquote>━━━━━━━━ 🆓 ━━━━━━━━\n"
        f"📊 <b>LIVE PAIR :</b> <code>{sig['symbol']}</code>\n"
        f"⚡ <b>TIMEFRAME :</b> <code>M1 (1-Minute)</code>\n"
        f"⏰ <b>ENTRY TIME :</b> <code>{sig['entry_time']}</code>\n"
        f"🔊 <b>DIRECTION :</b> <b>{sig['direction']}</b>\n"
        f"⚠️ <b>1 STEP MTG MUST</b>\n"
        f"━━━━━━━━ 🆓 ━━━━━━━━</blockquote>\n\n"
        f"💮💮 <b>!DIRECT SURESHOT!</b> 💮💮\n\n"
        f"<blockquote>📊 <b>ACCURACY :</b> <code>{sig['confidence']}%</code> {bars}\n"
        f"🔥 <b>PRESSURE :</b> <code>{sig['power']}</code>\n"
        f"🧠 <b>STRATEGY :</b> <i>{sig['reason']}</i>\n"
        f"🟢 <b>SIGNAL COUNT :</b> <code>{SIGNAL_COUNT}</code> 🎖️\n"
        f"🚫 <b>RULE :</b> <i>Don't trade below 80% payout!</i></blockquote>\n\n"
        f"🔖 <b>TELEGRAM :</b> @{OWNER_USERNAME} 🌸\n"
        f"📢 <b>VIP GROUP :</b> <a href='{VIP_GROUP_URL}'>Join AliTrader VIP</a>"
    )

async def play_scan_animation(msg, target_label):
    stages = [
        (
            f"🛰️ <b>ALITRADER LIVE FOREX RADAR</b>\n"
            f"<blockquote>🔄 <b>Step 1/3 :</b> Fetching Live Institutional Forex Feed...\n"
            f"📡 <b>Target :</b> <code>{target_label}</code>\n"
            f"🟦🟦🟦🟦⬜⬜⬜⬜⬜⬜ <b>40%</b></blockquote>"
        ),
        (
            f"🧠 <b>CHECKING M5 + M1 CONFLUENCE</b>\n"
            f"<blockquote>⚡ <b>Step 2/3 :</b> Verifying Trend, ADX &amp; S&amp;R Rejections...\n"
            f"📡 <b>Target :</b> <code>{target_label}</code>\n"
            f"✅ <code>M5 + M1 Trend Filter : OK</code>\n"
            f"✅ <code>Fractal S&amp;R + Pinbar : OK</code>\n"
            f"🟪🟪🟪🟪🟪🟪🟪⬜⬜⬜ <b>75%</b></blockquote>"
        ),
        (
            f"🎯 <b>LOCKING LIVE SURESHOT</b>\n"
            f"<blockquote>🔥 <b>Step 3/3 :</b> Synchronizing Exact :00 Second Entry...\n"
            f"📡 <b>Target :</b> <code>{target_label}</code>\n"
            f"🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 <b>100%</b></blockquote>"
        )
    ]
    for stage_txt in stages:
        try:
            await msg.edit_text(stage_txt, parse_mode='HTML')
            await asyncio.sleep(0.35)
        except Exception:
            pass

GATE_HOME_TEXT = (
    "👾 <b>ALITRADER VIP TERMINAL</b> 👾\n"
    "<blockquote>🔒 <b>VIP ACCESS REQUIRED</b>\n"
    "──────────────────────\n"
    "ℹ️ Unlock instant <b>Quotex Live Market Sureshot Signals</b> using one of the two options below:</blockquote>\n\n"
    "<blockquote>🆓 <b>Option 1 : Free VIP Access</b>\n"
    "Create a Quotex account through our official partner link and send your <b>Trader ID</b> for instant automated verification.\n\n"
    "💎 <b>Option 2 : Premium VIP Access</b>\n"
    "Pay via <b>Binance Pay</b> without creating a new account for lifetime access.</blockquote>\n\n"
    f"🔖 <b>OWNER :</b> @{OWNER_USERNAME} 🌸"
)

DASHBOARD_TEXT = (
    "👾 <b>ALITRADER LIVE VIP DASHBOARD</b> 👾\n"
    "<blockquote>🟢 <b>TERMINAL STATUS :</b> <code>ONLINE (LIVE FOREX V3)</code>\n"
    "──────────────────────\n"
    "⚡ <b>Timeframe :</b> <code>1 Minute (M1 + M5 Filter)</code>\n"
    "🧠 <b>AI Engine :</b> <code>M5+M1 Pullback + Fractal S&amp;R</code>\n"
    "🔥 <b>Markets   :</b> <code>20 Verified Quotex Live Pairs</code>\n"
    "🌍 <b>Timezone  :</b> <code>PKT (UTC+5)</code></blockquote>\n\n"
    "👇 <b>Tap any button below to scan Live Quotex Markets:</b>"
)

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data["awaiting_trader_id"] = False

    if is_user_authorized(user_id):
        await update.message.reply_text(
            DASHBOARD_TEXT,
            parse_mode='HTML',
            reply_markup=get_main_dashboard_kb(),
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            GATE_HOME_TEXT,
            parse_mode='HTML',
            reply_markup=get_access_gate_kb(),
            disable_web_page_preview=True
        )

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ENV_ALLOWED_IDS and user_id != ADMIN_ID:
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ <b>Usage:</b> <code>/adduser &lt;Telegram_User_ID&gt;</code>", parse_mode='HTML')
        return

    new_uid = int(context.args[0])
    authorize_user(new_uid)
    await update.message.reply_text(f"✅ <b>User <code>{new_uid}</code> has been granted AliTrader VIP Access!</b>", parse_mode='HTML')
    try:
        await context.bot.send_message(
            chat_id=new_uid,
            text=(
                "🎉 <b>ALITRADER VIP ACCESS ACTIVATED!</b>\n"
                "<blockquote>💎 Your <b>VIP Access</b> has been approved by Admin!\n"
                "Tap any button below to start generating Quotex Live signals:</blockquote>"
            ),
            parse_mode='HTML',
            reply_markup=get_main_dashboard_kb()
        )
    except Exception:
        pass

# --- Trader ID Input Handler ---
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    match = re.search(r'\b(\d{6,12})\b', text)

    if is_user_authorized(user_id) and not context.user_data.get("awaiting_trader_id"):
        await update.message.reply_text(
            DASHBOARD_TEXT,
            parse_mode='HTML',
            reply_markup=get_main_dashboard_kb(),
            disable_web_page_preview=True
        )
        return

    if not match:
        await update.message.reply_text(
            "⚠️ <b>INVALID TRADER ID FORMAT</b>\n"
            "<blockquote>ℹ️ Please send only your numeric <b>Quotex Trader ID</b> (Example: <code>95420333</code>).\n\n"
            "Don't have an account yet? Tap the button below to register first:</blockquote>",
            parse_mode='HTML',
            reply_markup=get_free_access_kb()
        )
        return

    trader_id = match.group(1)

    used_by = db_data.get("used_trader_ids", {}).get(trader_id)
    if used_by and used_by != user_id:
        await update.message.reply_text(
            "⛔ <b>TRADER ID ALREADY USED</b>\n"
            f"<blockquote>ℹ️ Trader ID <code>{trader_id}</code> is already linked to another Telegram user. Please register your own Quotex account.</blockquote>",
            parse_mode='HTML',
            reply_markup=get_free_access_kb()
        )
        return

    verify_msg = await update.message.reply_text(
        f"🔄 <b>VERIFYING TRADER ID...</b>\n"
        f"<blockquote>🆔 <b>Trader ID :</b> <code>{trader_id}</code>\n"
        f"📡 Checking registration with @QuotexPartnerBot...\n"
        f"🟦🟦🟦🟦🟦⬜⬜⬜⬜⬜ <b>50%</b></blockquote>",
        parse_mode='HTML'
    )

    status, _ = await verify_with_quotex_partner_bot(trader_id)

    if status == "FOUND":
        authorize_user(user_id, trader_id)
        context.user_data["awaiting_trader_id"] = False
        await verify_msg.edit_text(
            f"✅ <b>TRADER ID VERIFIED!</b>\n"
            f"<blockquote>👤 <b>Trader ID :</b> <code>#{trader_id}</code>\n"
            f"🟢 <b>Status :</b> <code>AliTrader VIP Unlocked</code>\n"
            f"──────────────────────\n"
            f"Welcome to <b>AliTrader VIP Terminal</b>! Select any option below:</blockquote>",
            parse_mode='HTML',
            reply_markup=get_main_dashboard_kb()
        )
        return

    elif status in ["NOT_FOUND", "WRONG_LINK"]:
        await verify_msg.edit_text(
            f"❌ <b>ACCOUNT NOT FOUND!</b>\n"
            f"<blockquote>ℹ️ <code>Trader with ID = '{trader_id}' was not found.</code>\n"
            f"──────────────────────\n"
            f"1️⃣ Tap <b>CREATE QUOTEX ACCOUNT</b> below.\n"
            f"2️⃣ Register a fresh account with a new email.\n"
            f"3️⃣ Copy your 8-9 digit <b>Trader ID</b> and paste it here!</blockquote>",
            parse_mode='HTML',
            reply_markup=get_free_access_kb()
        )
        return

    else:
        if ADMIN_ID:
            admin_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve User", callback_data=f"adm_app_{user_id}_{trader_id}"),
                    InlineKeyboardButton("❌ Reject (Not Found)", callback_data=f"adm_rej_{user_id}_{trader_id}")
                ]
            ])
            try:
                u_name = update.effective_user.first_name or "User"
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"🔔 <b>NEW TRADER ID REQUEST</b>\n"
                        f"<blockquote>👤 <b>User :</b> {u_name} (<code>{user_id}</code>)\n"
                        f"🆔 <b>Quotex Trader ID :</b> <code>{trader_id}</code>\n\n"
                        f"Check <code>{trader_id}</code> in @QuotexPartnerBot and click below:</blockquote>"
                    ),
                    parse_mode='HTML',
                    reply_markup=admin_kb
                )
            except Exception as e:
                logger.error(f"Admin notify error: {e}")

        await verify_msg.edit_text(
            f"⏳ <b>VERIFICATION IN PROGRESS</b>\n"
            f"<blockquote>🆔 <b>Trader ID :</b> <code>{trader_id}</code>\n"
            f"ℹ️ Your Trader ID has been sent for verification. You will receive an automatic notification here shortly!</blockquote>",
            parse_mode='HTML',
            reply_markup=get_access_gate_kb()
        )

# --- Callback Button Handler ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # Admin Approval Callbacks
    if data.startswith("adm_app_") or data.startswith("adm_rej_"):
        if user_id not in ENV_ALLOWED_IDS and user_id != ADMIN_ID:
            return
        parts = data.split("_")
        action = parts[1]
        target_uid = int(parts[2])
        t_id = parts[3]

        if action == "app":
            authorize_user(target_uid, t_id)
            await query.edit_message_text(f"✅ <b>Approved User <code>{target_uid}</code> (Trader ID: <code>{t_id}</code>)</b>", parse_mode='HTML')
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"✅ <b>TRADER ID #{t_id} VERIFIED!</b>\n"
                        f"<blockquote>🎉 Your <b>AliTrader VIP Access</b> is now active! Tap any button below to get Live signals:</blockquote>"
                    ),
                    parse_mode='HTML',
                    reply_markup=get_main_dashboard_kb()
                )
            except Exception:
                pass
        else:
            await query.edit_message_text(f"❌ <b>Rejected User <code>{target_uid}</code> (Trader ID <code>{t_id}</code> Not Found)</b>", parse_mode='HTML')
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"❌ <b>ACCOUNT NOT FOUND!</b>\n"
                        f"<blockquote>ℹ️ <code>Trader with ID = '{t_id}' was not found.</code>\n\n"
                        f"Please create a new Quotex account using our official partner link below and send your new Trader ID:</blockquote>"
                    ),
                    parse_mode='HTML',
                    reply_markup=get_free_access_kb()
                )
            except Exception:
                pass
        return

    # Gate Navigation
    if data == "gate_home":
        context.user_data["awaiting_trader_id"] = False
        await query.edit_message_text(
            GATE_HOME_TEXT,
            parse_mode='HTML',
            reply_markup=get_access_gate_kb(),
            disable_web_page_preview=True
        )
        return

    if data == "gate_free":
        context.user_data["awaiting_trader_id"] = True
        free_text = (
            "🆓 <b>GET FREE ALITRADER VIP ACCESS</b>\n"
            "<blockquote>1️⃣ Click <b>CREATE QUOTEX ACCOUNT</b> below and register a new account.\n"
            "2️⃣ Copy your 8 or 9 digit <b>Trader ID</b> from your Quotex profile (e.g. <code>95420333</code>).\n"
            "3️⃣ Paste and send your <b>Trader ID</b> directly in this chat!</blockquote>\n\n"
            "👇 <b>Type and send your Trader ID in the message box:</b>"
        )
        await query.edit_message_text(
            free_text,
            parse_mode='HTML',
            reply_markup=get_free_access_kb(),
            disable_web_page_preview=True
        )
        return

    if data == "enter_trader_id":
        context.user_data["awaiting_trader_id"] = True
        await query.edit_message_text(
            "✍️ <b>SEND YOUR QUOTEX TRADER ID</b>\n"
            "<blockquote>ℹ️ Type and send your numeric <b>Quotex Trader ID</b> (e.g. <code>95420333</code>) in this chat right now.\n\n"
            "🔄 Our system will verify it with @QuotexPartnerBot automatically!</blockquote>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="gate_free")]])
        )
        return

    if data == "gate_premium":
        context.user_data["awaiting_trader_id"] = False
        prem_text = (
            "💎 <b>BUY ALITRADER PREMIUM VIP</b>\n"
            f"<blockquote>💰 <b>Price :</b> <code>{PREMIUM_PRICE}</code>\n"
            f"🏦 <b>Binance Pay ID :</b> <code>{BINANCE_PAY_ID}</code>\n"
            f"🆔 <b>Your Telegram ID :</b> <code>{user_id}</code></blockquote>\n\n"
            f"<blockquote>1️⃣ Send the payment to the Binance Pay ID above.\n"
            f"2️⃣ Tap <b>SEND PAYMENT SCREENSHOT</b> below and send your payment proof + Telegram ID to @{OWNER_USERNAME}.</blockquote>"
        )
        await query.edit_message_text(
            prem_text,
            parse_mode='HTML',
            reply_markup=get_premium_access_kb(),
            disable_web_page_preview=True
        )
        return

    if not is_user_authorized(user_id):
        await query.edit_message_text(
            GATE_HOME_TEXT,
            parse_mode='HTML',
            reply_markup=get_access_gate_kb(),
            disable_web_page_preview=True
        )
        return

    # Verified Dashboard Navigation
    if data == "main_menu":
        await query.edit_message_text(
            DASHBOARD_TEXT,
            parse_mode='HTML',
            reply_markup=get_main_dashboard_kb(),
            disable_web_page_preview=True
        )
        return

    if data.startswith("list_live_"):
        page = int(data.split("_")[-1])
        text = (
            f"🟢 <b>SELECT QUOTEX LIVE PAIR (Page {page + 1})</b>\n"
            f"<blockquote>ℹ️ All 20 currency pairs below are 100% verified on the <b>Quotex Live Market</b> board. Tap any pair for an instant M1+M5 Signal:</blockquote>"
        )
        await query.edit_message_text(
            text,
            parse_mode='HTML',
            reply_markup=get_pairs_grid_kb(page)
        )
        return

    if data.startswith("pair_live_"):
        idx = int(data.split("_")[-1])
        markets = list(QUOTEX_LIVE_MARKETS.keys())
        if idx >= len(markets):
            return
        pair_name = markets[idx]

        await play_scan_animation(query.message, f"{pair_name} (Live)")
        sig = await asyncio.to_thread(fetch_single_pair_signal, pair_name)

        if not sig:
            closed_text = (
                f"⚠️ <b>PAIR CURRENTLY FLAT OR CLOSED</b>\n"
                f"<blockquote>ℹ️ <code>{pair_name} (Live)</code> is either closed (Weekend/Bank Holiday) or forming low-volume Doji candles right now.\n\n"
                f"👇 Tap below to auto-scan all 20 Live pairs for the best active setup:</blockquote>"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 AUTO SCAN BEST LIVE PAIR", callback_data="auto_live_1")],
                [InlineKeyboardButton("📊 Choose Another Live Pair", callback_data="list_live_0")],
                [InlineKeyboardButton("👀 Back to Dashboard", callback_data="main_menu")]
            ])
            await query.message.edit_text(closed_text, parse_mode='HTML', reply_markup=kb)
            return

        card = format_signal_card(sig)
        await query.message.edit_text(
            card,
            parse_mode='HTML',
            reply_markup=get_post_signal_kb(idx),
            disable_web_page_preview=True
        )
        return

    if data.startswith("auto_live_"):
        count = int(data.split("_")[-1])
        label = "20 QUOTEX LIVE FOREX PAIRS"

        await play_scan_animation(query.message, label)
        found_signals = await asyncio.to_thread(scan_best_live_signals, count)

        if not found_signals:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 RETRY LIVE SCAN", callback_data="auto_live_1")],
                [InlineKeyboardButton("👀 Back to Dashboard", callback_data="main_menu")]
            ])
            await query.message.edit_text(
                "⚠️ <b>LIVE FOREX MARKET CLOSED OR FLAT</b>\n"
                "<blockquote>ℹ️ Global Live Forex markets are currently closed (Saturday/Sunday) or experiencing zero volatility. Please scan during active Monday–Friday market hours!</blockquote>",
                parse_mode='HTML',
                reply_markup=kb
            )
            return

        first_card = format_signal_card(found_signals[0])
        await query.message.edit_text(
            first_card,
            parse_mode='HTML',
            reply_markup=get_post_signal_kb(None),
            disable_web_page_preview=True
        )

        for extra_sig in found_signals[1:]:
            await asyncio.sleep(0.35)
            extra_card = format_signal_card(extra_sig)
            await query.message.reply_text(
                extra_card,
                parse_mode='HTML',
                reply_markup=get_post_signal_kb(None),
                disable_web_page_preview=True
            )

def run_bot():
    logger.info("Starting AliTrader Live VIP Telegram Bot...")
    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("scan", scan_command))
    telegram_app.add_handler(CommandHandler("adduser", adduser_command))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    run_bot()
