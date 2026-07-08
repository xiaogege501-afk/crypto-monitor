#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crypto_core.py
--------------
加密货币多周期趋势监控 —— 核心逻辑（不含界面）。
供桌面版(tkinter)和网页版(Flask)共用。
数据来源：币安(Binance)公开行情接口，无需 API Key。
"""

import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BINANCE_BASE = "https://api.binance.com"

TIMEFRAMES = [
    ("15分钟", "15m"),
    ("1小时", "1h"),
    ("日线", "1d"),
    ("周线", "1w"),
]
KLINE_LIMIT = 300

STABLE_BASES = {"USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDP", "EUR", "GBP", "AEUR", "USTC"}
EXCLUDE_KEYWORDS = ["UP", "DOWN", "BULL", "BEAR"]

HEADERS = {"User-Agent": "Mozilla/5.0 (crypto-monitor-tool)"}

TREND_DISPLAY = {
    "多头": "多头↑",
    "空头": "空头↓",
    "震荡": "震荡→",
    "数据不足": "数据不足",
}

DEFAULT_CONFIG = {
    "holdings": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
    "proxy": "",
    "auto_refresh_minutes": 5,
    "recommend_count": 10,
}


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------
def load_config(config_path):
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config_path, cfg):
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 网络请求
# ---------------------------------------------------------------------------
def get_session(cfg):
    s = requests.Session()
    s.headers.update(HEADERS)
    proxy = (cfg or {}).get("proxy") or ""
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    return s


def fetch_klines(session, symbol, interval, limit=KLINE_LIMIT):
    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = session.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    return [float(k[4]) for k in data]


def fetch_all_tickers(session):
    url = f"{BINANCE_BASE}/api/v3/ticker/24hr"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# 技术指标
# ---------------------------------------------------------------------------
def calc_ema_series(closes, period):
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema_val = sum(closes[:period]) / period
    for price in closes[period:]:
        ema_val = price * k + ema_val * (1 - k)
    return ema_val


def judge_trend(closes):
    if not closes or len(closes) < 121:
        return "数据不足", None, None, None, (closes[-1] if closes else None)
    e20 = calc_ema_series(closes, 20)
    e60 = calc_ema_series(closes, 60)
    e120 = calc_ema_series(closes, 120)
    price = closes[-1]
    if e20 is None or e60 is None or e120 is None:
        return "数据不足", e20, e60, e120, price
    if e20 > e60 > e120:
        return "多头", e20, e60, e120, price
    elif e20 < e60 < e120:
        return "空头", e20, e60, e120, price
    else:
        return "震荡", e20, e60, e120, price


def get_symbol_multi_trend(session, symbol, cache=None):
    result = {"symbol": symbol, "trends": {}, "price": None, "error": None}
    try:
        for label, interval in TIMEFRAMES:
            cache_key = (symbol, interval)
            if cache is not None and cache_key in cache:
                closes = cache[cache_key]
            else:
                closes = fetch_klines(session, symbol, interval)
                if cache is not None:
                    cache[cache_key] = closes
            trend, e20, e60, e120, price = judge_trend(closes)
            result["trends"][label] = trend
            if interval == "1d":
                result["price"] = price
                result["daily_ema20"] = e20
    except Exception as e:
        result["error"] = str(e)
    return result


def compute_resonance(trends):
    vals = [trends.get(label) for label, _ in TIMEFRAMES]
    if all(v == "多头" for v in vals):
        return "买入", "buy"
    if all(v == "空头" for v in vals):
        return "做空", "short"
    return "-", "none"


def is_valid_usdt_symbol(symbol):
    if not symbol.endswith("USDT"):
        return False
    base = symbol[:-4]
    if base in STABLE_BASES:
        return False
    for kw in EXCLUDE_KEYWORDS:
        if kw in base:
            return False
    return True


def pick_recommend_symbols(session, top_n=10, stage1_pool=60, log=None):
    if log:
        log("正在获取市场行情...")
    tickers = fetch_all_tickers(session)
    candidates = [t for t in tickers if is_valid_usdt_symbol(t.get("symbol", ""))]
    candidates.sort(key=lambda t: float(t.get("quoteVolume", 0) or 0), reverse=True)
    candidates = candidates[:stage1_pool]

    if log:
        log(f"筛选日线多头排列品种（候选 {len(candidates)} 个）...")

    cache = {}
    daily_bullish = []

    def check_daily(sym):
        try:
            closes = fetch_klines(session, sym, "1d")
            cache[(sym, "1d")] = closes
            trend, e20, e60, e120, price = judge_trend(closes)
            return sym, trend, e20, price
        except Exception:
            return sym, None, None, None

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(check_daily, t["symbol"]) for t in candidates]
        for fut in as_completed(futures):
            sym, trend, e20, price = fut.result()
            if trend == "多头" and e20:
                strength = (price - e20) / e20 * 100
                daily_bullish.append((sym, strength))

    daily_bullish.sort(key=lambda x: x[1], reverse=True)
    shortlist = [s for s, _ in daily_bullish[:top_n]]

    if log:
        log(f"计算 {len(shortlist)} 个候选的多周期共振...")

    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(get_symbol_multi_trend, session, sym, cache): sym for sym in shortlist}
        for fut in as_completed(futures):
            results.append(fut.result())

    order = {sym: i for i, sym in enumerate(shortlist)}
    results.sort(key=lambda r: order.get(r["symbol"], 999))
    return results


def refresh_holdings_data(cfg, symbols):
    session = get_session(cfg)
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(get_symbol_multi_trend, session, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            results.append(fut.result())
    order = {sym: i for i, sym in enumerate(symbols)}
    results.sort(key=lambda r: order.get(r["symbol"], 999))
    return results


def serialize_result(r):
    """把 get_symbol_multi_trend 的结果转成前端友好的 JSON 结构"""
    symbol = r["symbol"]
    if r.get("error"):
        return {
            "symbol": symbol, "price": None, "error": r["error"],
            "tf15": "错误", "tf1h": "错误", "tfd": "错误", "tfw": "错误",
            "signal": "-", "signal_type": "none",
        }
    trends = r["trends"]
    signal_text, tag = compute_resonance(trends)
    price = r.get("price")
    return {
        "symbol": symbol,
        "price": round(price, 6) if isinstance(price, (int, float)) else None,
        "error": None,
        "tf15": TREND_DISPLAY.get(trends.get("15分钟"), "-"),
        "tf1h": TREND_DISPLAY.get(trends.get("1小时"), "-"),
        "tfd": TREND_DISPLAY.get(trends.get("日线"), "-"),
        "tfw": TREND_DISPLAY.get(trends.get("周线"), "-"),
        "signal": ("买入(四周期共振多头)" if tag == "buy" else
                   "做空(四周期共振空头)" if tag == "short" else "-"),
        "signal_type": tag,
    }
