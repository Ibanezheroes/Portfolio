#!/usr/bin/env python3
"""Standalone Swiss-style ETF Portfolio Dashboard generator + 5-min Auto-Refresh Server.

Features:
1. Fetches historical market data and live quotes via TradingView Scanner and yfinance.
2. Includes a real-time Global Market Sentiment Barometer with zoomable historical trend lines 
   (1D, 1W, 1M, 1Y) for VIX, VVIX, MOVE, CNN Fear & Greed, and the Global Health Index.
3. Includes interactive modal popups with X-axis date labels, timeframe zoom bar, and large detailed graphs.
4. Includes a robust DCA & Future Simulator with responsive Future Basis selection (1Y, 5Y, 10Y, 15Y, 25Y, Max).
5. Tracks portfolio core ETFs plus informational 0% holdings (COPX, NASDAQ:PLTR, LSE:SMT).
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
import webbrowser
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

REFRESH_INTERVAL_SECONDS = 300  # 5 minutes

RANGES: dict[str, dict] = {
    "1d":  {"label": "1D",  "title": "1 day (5m close)",            "period": "1d",  "interval": "5m",  "field": "Close",     "intraday": True,  "max_pts": 110},
    "1w":  {"label": "1W",  "title": "1 week (30m close)",          "period": "5d",  "interval": "30m", "field": "Close",     "intraday": True,  "max_pts": 110},
    "1mo": {"label": "1M",  "title": "1 month (daily adj. close)",  "period": "1mo", "interval": "1d",  "field": "Adj Close", "intraday": False, "max_pts": 60},
    "6mo": {"label": "6M",  "title": "6 months (daily adj. close)", "period": "6mo", "interval": "1d",  "field": "Adj Close", "intraday": False, "max_pts": 140},
    "1y":  {"label": "1Y",  "title": "1 year (daily adj. close)",   "period": "1y",  "interval": "1d",  "field": "Adj Close", "intraday": False, "max_pts": 160},
    "5y":  {"label": "5Y",  "title": "5 years (daily adj. close)",  "period": "5y",  "interval": "1d",  "field": "Adj Close", "intraday": False, "max_pts": 180},
    "max": {"label": "Max", "title": "Max history (daily adj.)",    "period": "max", "interval": "1d",  "field": "Adj Close", "intraday": False, "max_pts": 220},
}

SLEEVES: list[dict] = [
    {
        "id": "actions",
        "label": "Actions",
        "labeledWeight": 77,
        "holdingsWeight": 78,
        "note": "Equities sleeve labeled 77%. The seven equity ETF weights sum to 78%.",
    },
    {
        "id": "immobilier",
        "label": "Immobilier",
        "labeledWeight": 22,
        "holdingsWeight": 21,
        "note": "Real estate sleeve labeled 22%. The two holdings are 19% and 2%, summing to 21%.",
    },
    {
        "id": "ressources",
        "label": "Idées / Satellites (Information only)",
        "labeledWeight": 0,
        "holdingsWeight": 0,
        "note": "Informational assets (0% weight): COPX (Copper), PLTR (Palantir), and SMT (Scottish Mortgage Trust).",
    },
]

ETFS: list[dict] = [
    {
        "id": "xd9u",
        "sleeve": "actions",
        "weight": 37,
        "name": "Xtrackers MSCI USA UCITS ETF 1C",
        "shortName": "MSCI USA",
        "color": "#60a5fa",
        "candidates": [
            {"ticker": "XD9U.L", "currency": "USD", "note": None},
            {"ticker": "XD9U.SW", "currency": "CHF", "note": "Fallback listing XD9U.SW (CHF)."},
            {"ticker": "XD9U.DE", "currency": "EUR", "note": "Fallback listing XD9U.DE (EUR)."},
        ],
    },
    {
        "id": "chspi",
        "sleeve": "actions",
        "weight": 11,
        "name": "iShares Core SPI ETF (CH)",
        "shortName": "SPI",
        "color": "#38bdf8",
        "candidates": [{"ticker": "CHSPI.SW", "currency": "CHF", "note": None}],
    },
    {
        "id": "is3n",
        "sleeve": "actions",
        "weight": 8,
        "name": "iShares Core MSCI EM IMI UCITS ETF USD (Acc)",
        "shortName": "MSCI EM IMI",
        "color": "#a78bfa",
        "candidates": [
            {"ticker": "EIMI.L", "currency": "USD", "note": None},
            {"ticker": "EIMI.SW", "currency": "USD", "note": "SIX USD listing EIMI.SW."},
            {"ticker": "IS3N.DE", "currency": "EUR", "note": "Fallback listing IS3N.DE (EUR)."},
        ],
    },
    {
        "id": "e50eua",
        "sleeve": "actions",
        "weight": 8,
        "name": "UBS Core EURO STOXX 50 UCITS ETF EUR dis",
        "shortName": "EURO STOXX 50",
        "color": "#34d399",
        "candidates": [
            {"ticker": "E50EUA.SW", "currency": "CHF", "note": "Primary SIX Swiss Exchange listing (CHF)."},
            {"ticker": "E50EUA.MI", "currency": "EUR", "note": "Milan listing (EUR)."},
        ],
    },
    {
        "id": "vapx",
        "sleeve": "actions",
        "weight": 8,
        "name": "Vanguard FTSE Developed Asia Pacific ex Japan UCITS ETF",
        "shortName": "Asia Pac ex JP",
        "color": "#fbbf24",
        "candidates": [
            {"ticker": "VAPU.L", "currency": "USD", "note": None},
            {"ticker": "VAPX.SW", "currency": "CHF", "note": "Fallback listing VAPX.SW (CHF)."},
            {"ticker": "VAPX.L", "currency": "GBP", "note": "Fallback listing VAPX.L (GBP)."},
        ],
    },
    {
        "id": "vjpn",
        "sleeve": "actions",
        "weight": 4,
        "name": "Vanguard FTSE Japan UCITS ETF",
        "shortName": "Japan",
        "color": "#f472b6",
        "candidates": [
            {"ticker": "VDJP.L", "currency": "USD", "note": None},
            {"ticker": "VJPN.SW", "currency": "CHF", "note": "Fallback listing VJPN.SW (CHF)."},
            {"ticker": "VJPN.L", "currency": "GBP", "note": "Fallback listing VJPN.L (GBP)."},
        ],
    },
    {
        "id": "isf",
        "sleeve": "actions",
        "weight": 2,
        "name": "iShares Core FTSE 100 UCITS ETF GBP (Dist)",
        "shortName": "FTSE 100",
        "color": "#fb923c",
        "candidates": [
            {"ticker": "ISF.L", "currency": "GBp", "note": "Yahoo quotes ISF.L in pence (GBp)."},
        ],
    },
    {
        "id": "srfcha",
        "sleeve": "immobilier",
        "weight": 19,
        "name": "UBS SXI Real Estate ETF A-dis",
        "shortName": "SXI Real Estate",
        "color": "#2dd4bf",
        "candidates": [{"ticker": "SRFCHA.SW", "currency": "CHF", "note": None}],
    },
    {
        "id": "hprd",
        "sleeve": "immobilier",
        "weight": 2,
        "name": "HSBC FTSE EPRA NAREIT Developed UCITS ETF",
        "shortName": "EPRA NAREIT",
        "color": "#818cf8",
        "candidates": [
            {"ticker": "HPRD.L", "currency": "USD", "note": None},
            {"ticker": "HPRD.SW", "currency": "USD", "note": "Fallback listing HPRD.SW (USD)."},
        ],
    },
    {
        "id": "copx",
        "sleeve": "ressources",
        "weight": 0,
        "name": "Global X Copper Miners UCITS ETF (COPX)",
        "shortName": "COPX (Info)",
        "color": "#f87171",
        "candidates": [
            {"ticker": "COPX.L", "currency": "USD", "note": "For information only (0% weight)."},
            {"ticker": "COPX.SW", "currency": "CHF", "note": "For information only (0% weight)."},
            {"ticker": "4COP.DE", "currency": "EUR", "note": "For information only (0% weight)."},
            {"ticker": "COPX", "currency": "USD", "note": "For information only (0% weight)."},
        ],
    },
    {
        "id": "pltr",
        "sleeve": "ressources",
        "weight": 0,
        "name": "Palantir Technologies Inc. (NASDAQ)",
        "shortName": "PLTR (Info)",
        "color": "#a855f7",
        "candidates": [
            {"ticker": "PLTR", "currency": "USD", "note": "For information only (0% weight)."},
            {"ticker": "PTX.DE", "currency": "EUR", "note": "Xetra EUR listing PTX.DE."},
            {"ticker": "PTX.F", "currency": "EUR", "note": "Frankfurt EUR listing PTX.F."},
        ],
    },
    {
        "id": "smt",
        "sleeve": "ressources",
        "weight": 0,
        "name": "Scottish Mortgage Investment Trust Plc (LSE)",
        "shortName": "SMT (Info)",
        "color": "#ec4899",
        "candidates": [
            {"ticker": "SMT.L", "currency": "GBp", "note": "London Stock Exchange (pences GBp / 100)."},
        ],
    },
]

FX_PAIRS: list[dict] = [
    {
        "id": "usdchf",
        "currency": "USD",
        "pair": "USD/CHF",
        "ticker": "USDCHF=X",
        "name": "US Dollar to Swiss Franc",
        "color": "#34d399",
        "exposureWeight": 59,
        "exposureNote": "XD9U.L (37%), EIMI.L (8%), VAPU.L (8%), VDJP.L (4%), HPRD.L (2%) — 59% of portfolio",
    },
    {
        "id": "eurchf",
        "currency": "EUR",
        "pair": "EUR/CHF",
        "ticker": "EURCHF=X",
        "name": "Euro to Swiss Franc",
        "color": "#38bdf8",
        "exposureWeight": 8,
        "exposureNote": "E50EUA.SW / E50EUA.MI (8%) — 8% of portfolio",
    },
    {
        "id": "gbpchf",
        "currency": "GBP",
        "pair": "GBP/CHF",
        "ticker": "GBPCHF=X",
        "name": "British Pound to Swiss Franc",
        "color": "#fb923c",
        "exposureWeight": 2,
        "exposureNote": "ISF.L (2%, quoted in GBp pence ÷ 100) — 2% of portfolio",
    },
]

TV_SYMBOL_MAP: dict[str, str] = {
    "XD9U.L": "LSE:XD9U", "XD9U.SW": "SIX:XD9U", "XD9U.DE": "XETR:XD9U",
    "CHSPI.SW": "SIX:CHSPI", "EIMI.L": "LSE:EIMI", "EIMI.SW": "SIX:EIMI",
    "IS3N.DE": "XETR:IS3N", "E50EUA.SW": "SIX:E50EUA", "E50EUA.MI": "MIL:E50EUA",
    "VAPU.L": "LSE:VAPU", "VAPX.SW": "SIX:VAPX", "VAPX.L": "LSE:VAPX",
    "VDJP.L": "LSE:VDJP", "VJPN.SW": "SIX:VJPN", "VJPN.L": "LSE:VJPN",
    "ISF.L": "LSE:ISF", "SRFCHA.SW": "SIX:SRFCHA", "HPRD.L": "LSE:HPRD",
    "HPRD.SW": "SIX:HPRD", "COPX.L": "LSE:COPX", "COPX.SW": "SIX:COPX",
    "4COP.DE": "XETR:4COP", "COPX": "NYSEARCA:COPX", "PLTR": "NASDAQ:PLTR",
    "PTX.DE": "XETR:PTX", "SMT.L": "LSE:SMT",
    "USDCHF=X": "FX_IDC:USDCHF", "EURCHF=X": "FX_IDC:EURCHF", "GBPCHF=X": "FX_IDC:GBPCHF",
}


def _fetch_tradingview_live_quotes() -> dict[str, float]:
    tv_to_yf = {tv: yf_t for yf_t, tv in TV_SYMBOL_MAP.items()}
    payload = {"symbols": {"tickers": list(tv_to_yf.keys()), "query": {"types": []}}, "columns": ["close"]}
    quotes: dict[str, float] = {}
    try:
        resp = requests.post("https://scanner.tradingview.com/global/scan", json=payload, timeout=10)
        resp.raise_for_status()
        for row in resp.json().get("data", []):
            tv_sym = row.get("s")
            val = (row.get("d") or [None])[0]
            if tv_sym in tv_to_yf and val is not None and math.isfinite(float(val)) and float(val) > 0:
                quotes[tv_to_yf[tv_sym]] = float(val)
    except Exception as exc:
        print(f"  [TradingView Scanner] Warning: {exc}")
    return quotes


def _fetch_sentiment_history() -> dict[str, dict]:
    tickers = {"vix": "^VIX", "vvix": "^VVIX", "move": "^MOVE"}

    daily_dfs = {}
    for key, tkr in tickers.items():
        try:
            df = yf.download(tkr, period="1y", interval="1d", progress=False, timeout=15)
            s, _ = _extract_series(df, tkr, "Close")
            if not s.empty:
                daily_dfs[key] = s
        except Exception:
            pass

    week_dfs = {}
    for key, tkr in tickers.items():
        try:
            df = yf.download(tkr, period="5d", interval="30m", progress=False, timeout=15)
            s, _ = _extract_series(df, tkr, "Close")
            if not s.empty:
                week_dfs[key] = s
        except Exception:
            pass

    day_dfs = {}
    for key, tkr in tickers.items():
        try:
            df = yf.download(tkr, period="1d", interval="5m", progress=False, timeout=15)
            s, _ = _extract_series(df, tkr, "Close")
            if not s.empty:
                day_dfs[key] = s
        except Exception:
            pass

    fng_series = pd.Series(dtype="float64")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=10)
        if resp.ok:
            data = resp.json()
            if "fear_and_greed_historical" in data and "data" in data["fear_and_greed_historical"]:
                items = data["fear_and_greed_historical"]["data"]
                dates_list = [datetime.fromtimestamp(int(item["x"]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for item in items]
                scores_list = [float(item["y"]) for item in items]
                fng_series = pd.Series(scores_list, index=pd.to_datetime(dates_list))
                fng_series = fng_series[~fng_series.index.duplicated(keep="last")].sort_index()
    except Exception:
        pass

    def build_slice(dfs_dict: dict, intraday: bool, lookback_days: int | None = None) -> dict:
        out = {"dates": [], "vix": [], "vvix": [], "move": [], "fng": [], "global": []}
        if not dfs_dict:
            return out
        comb = pd.DataFrame(dfs_dict).dropna(how="all").ffill()
        if lookback_days is not None:
            cutoff = pd.Timestamp.now(tz="UTC" if intraday else None) - pd.DateOffset(days=lookback_days)
            comb = comb.loc[comb.index >= cutoff]
        if comb.empty:
            return out

        if not fng_series.empty:
            norm_idx = pd.to_datetime(comb.index).tz_localize(None).normalize()
            mapped_fng = fng_series.reindex(norm_idx, method="ffill").bfill()
            comb["fng"] = mapped_fng.to_numpy()
        else:
            comb["fng"] = 50.0

        comb = comb.dropna()
        if not comb.empty:
            vix_s = 100 - ((comb["vix"] - 10).clip(lower=0) * 2.5) if "vix" in comb else pd.Series(50, index=comb.index)
            vvix_s = 100 - ((comb["vvix"] - 70).clip(lower=0) * 0.8) if "vvix" in comb else pd.Series(50, index=comb.index)
            move_s = 100 - ((comb["move"] - 60).clip(lower=0) * 0.8) if "move" in comb else pd.Series(50, index=comb.index)
            fng_s = comb["fng"].clip(0, 100)

            glob = (vix_s * 0.35) + (fng_s * 0.35) + (move_s * 0.15) + (vvix_s * 0.15)
            comb["global"] = glob.clip(0, 100)

            fmt_pattern = "%H:%M" if intraday and lookback_days == 1 else ("%m-%d %H:%M" if intraday else "%Y-%m-%d")
            out["dates"] = [pd.Timestamp(ts).strftime(fmt_pattern) for ts in comb.index]
            out["vix"] = [round(float(v), 2) for v in comb["vix"].to_numpy()] if "vix" in comb else []
            out["vvix"] = [round(float(v), 2) for v in comb["vvix"].to_numpy()] if "vvix" in comb else []
            out["move"] = [round(float(v), 2) for v in comb["move"].to_numpy()] if "move" in comb else []
            out["fng"] = [round(float(v), 2) for v in comb["fng"].to_numpy()]
            out["global"] = [round(float(v), 1) for v in comb["global"].to_numpy()]
        return out

    return {
        "1y": build_slice(daily_dfs, intraday=False),
        "1mo": build_slice(daily_dfs, intraday=False, lookback_days=31),
        "1w": build_slice(week_dfs if week_dfs else daily_dfs, intraday=bool(week_dfs), lookback_days=7),
        "1d": build_slice(day_dfs if day_dfs else daily_dfs, intraday=bool(day_dfs), lookback_days=1),
    }


def _extract_series(frame: pd.DataFrame | None, ticker: str, field: str) -> tuple[pd.Series, str]:
    empty = pd.Series(dtype="float64")
    if frame is None or frame.empty:
        return empty, field
    used = field
    if isinstance(frame.columns, pd.MultiIndex):
        available = set(frame.columns.get_level_values(0))
        if field not in available and field == "Adj Close" and "Close" in available:
            field = used = "Close"
        if field not in available:
            return empty, used
        block = frame[field]
        series = block[ticker] if isinstance(block, pd.DataFrame) and ticker in block.columns else (
            block.iloc[:, 0] if isinstance(block, pd.DataFrame) else block
        )
    else:
        if field not in frame.columns and field == "Adj Close" and "Close" in frame.columns:
            field = used = "Close"
        if field not in frame.columns:
            return empty, used
        series = frame[field]
    series = pd.to_numeric(series, errors="coerce").dropna()
    series = series[series > 0]
    series = series[~series.index.duplicated(keep="last")].sort_index()
    if series.empty and used == "Adj Close":
        return _extract_series(frame, ticker, "Close")
    return series, used


def _fetch_etf(etf: dict, spec: dict) -> dict:
    for idx, cand in enumerate(etf["candidates"]):
        ticker = cand["ticker"]
        try:
            frame = yf.download(
                ticker, period=spec["period"], interval=spec["interval"],
                auto_adjust=False, progress=False, threads=False, timeout=30,
            )
            series, used_field = _extract_series(frame, ticker, spec["field"])
            if not series.empty:
                return {
                    "ticker": ticker, "currency": cand["currency"], "note": cand["note"],
                    "fallbackUsed": idx > 0, "field": used_field, "series": series,
                }
        except Exception:
            continue
    c0 = etf["candidates"][0]
    return {
        "ticker": c0["ticker"], "currency": c0["currency"], "note": c0["note"],
        "fallbackUsed": False, "field": spec["field"], "series": pd.Series(dtype="float64"),
    }


def _fetch_fx(fx: dict, spec: dict) -> dict:
    ticker = fx["ticker"]
    try:
        frame = yf.download(
            ticker, period=spec["period"], interval=spec["interval"],
            auto_adjust=False, progress=False, threads=False, timeout=30,
        )
        series, used_field = _extract_series(frame, ticker, spec["field"])
        if not series.empty:
            return {
                "id": fx["id"], "currency": fx["currency"], "pair": fx["pair"],
                "ticker": ticker, "field": used_field, "series": series,
            }
    except Exception:
        pass
    return {
        "id": fx["id"], "currency": fx["currency"], "pair": fx["pair"],
        "ticker": ticker, "field": spec["field"], "series": pd.Series(dtype="float64"),
    }


def _align_series(s: pd.Series, intraday: bool) -> pd.Series:
    if s.empty:
        return s
    aligned = s.copy()
    if intraday:
        aligned.index = pd.to_datetime(aligned.index, utc=True)
    else:
        aligned.index = pd.to_datetime(aligned.index).tz_localize(None).normalize()
    return aligned.groupby(level=0).last().sort_index()


def _convert_series_to_chf(
    s: pd.Series, currency: str, fx_aligned_by_ccy: dict[str, pd.Series], intraday: bool,
) -> pd.Series:
    s_aligned = _align_series(s, intraday)
    if s_aligned.empty or currency == "CHF":
        return s_aligned
    fx_ccy = "GBP" if currency in ("GBP", "GBp") else currency
    scale = 0.01 if currency == "GBp" else 1.0
    fx_s = fx_aligned_by_ccy.get(fx_ccy)
    if fx_s is None or fx_s.empty:
        return s_aligned * scale
    union_idx = s_aligned.index.union(fx_s.index).sort_values()
    fx_on_s = fx_s.reindex(union_idx).ffill().bfill().reindex(s_aligned.index)
    out = (s_aligned * scale * fx_on_s).dropna()
    return out[out > 0]


def _sample_indices(n: int, max_pts: int, extra: set[int] | None = None) -> list[int]:
    if n <= max_pts:
        return list(range(n))
    picks = {0, n - 1} | (extra or set())
    for i in range(1, max_pts - 1):
        picks.add(round(i * (n - 1) / (max_pts - 1)))
    return sorted(i for i in picks if 0 <= i < n)


def _fmt_ts(ts, intraday: bool) -> str:
    stamp = pd.Timestamp(ts)
    if intraday:
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        return stamp.strftime("%Y-%m-%d %H:%M")
    return stamp.strftime("%Y-%m-%d")


def _compute_book_block(
    raw_results: list[dict], fx_aligned_by_ccy: dict[str, pd.Series],
    intraday: bool, max_pts: int, in_chf: bool,
) -> dict:
    pct_columns: dict[str, pd.Series] = {}
    etf_data: dict[str, dict] = {}

    for etf, res in zip(ETFS, raw_results):
        raw_s: pd.Series = res["series"]
        s = (
            _convert_series_to_chf(raw_s, res["currency"], fx_aligned_by_ccy, intraday)
            if in_chf
            else _align_series(raw_s, intraday)
        )
        disp_ccy = "CHF" if in_chf else res["currency"]
        if s.empty:
            etf_data[etf["id"]] = {
                "ticker": res["ticker"], "currency": disp_ccy, "nativeCurrency": res["currency"],
                "note": res["note"], "fallbackUsed": res["fallbackUsed"], "field": res["field"],
                "startPrice": 0, "lastPrice": 0, "lowPrice": 0, "highPrice": 0,
                "changePct": 0, "pointCount": 0, "startDate": "", "endDate": "", "t": [], "p": [], "pct": [],
            }
            continue

        pct_s = (s / float(s.iloc[0]) - 1.0) * 100.0
        pct_columns[etf["id"]] = pct_s

        prices_list = [float(x) for x in s.to_numpy()]
        pcts_list = [float(x) for x in pct_s.to_numpy()]
        ts_list = [_fmt_ts(t, intraday) for t in s.index]

        min_idx = int(prices_list.index(min(prices_list)))
        max_idx = int(prices_list.index(max(prices_list)))
        idxs = _sample_indices(len(prices_list), max_pts, {min_idx, max_idx})

        etf_data[etf["id"]] = {
            "ticker": res["ticker"], "currency": disp_ccy, "nativeCurrency": res["currency"],
            "note": res["note"], "fallbackUsed": res["fallbackUsed"], "field": res["field"],
            "startPrice": round(prices_list[0], 4), "lastPrice": round(prices_list[-1], 4),
            "lowPrice": round(min(prices_list), 4), "highPrice": round(max(prices_list), 4),
            "changePct": round(pcts_list[-1], 2), "pointCount": len(prices_list),
            "startDate": ts_list[0][:10], "endDate": ts_list[-1][:10],
            "t": [ts_list[i] for i in idxs],
            "p": [round(prices_list[i], 4) for i in idxs],
            "pct": [round(pcts_list[i], 2) for i in idxs],
        }

    frame = pd.DataFrame(pct_columns).sort_index()
    weights = pd.Series({e["id"]: e["weight"] for e in ETFS if e.get("weight", 0) > 0 and e["id"] in frame.columns})
    filled = frame.ffill()
    avail = filled.notna()
    w_sum = avail.mul(weights, axis=1).sum(axis=1)
    port_series = filled.fillna(0).mul(weights, axis=1).sum(axis=1).where(w_sum > 0) / w_sum.where(w_sum > 0)

    c_idxs = _sample_indices(len(frame), max_pts)
    comp_t = [_fmt_ts(frame.index[i], intraday) for i in c_idxs]
    comp_series: dict[str, list[float | None]] = {}
    for eid in [e["id"] for e in ETFS]:
        if eid in filled.columns:
            col = filled[eid].to_numpy()
            comp_series[eid] = [
                round(float(col[i]), 2) if pd.notna(col[i]) and math.isfinite(float(col[i])) else None
                for i in c_idxs
            ]
        else:
            comp_series[eid] = [None for _ in c_idxs]

    p_arr = port_series.to_numpy()
    comp_series["portfolio"] = [
        round(float(p_arr[i]), 2) if pd.notna(p_arr[i]) and math.isfinite(float(p_arr[i])) else None
        for i in c_idxs
    ]

    finite_port = port_series.dropna()
    port_change = round(float(finite_port.iloc[-1]), 2) if not finite_port.empty else 0.0
    start_date_str = comp_t[0][:10] if comp_t else ""
    end_date_str = comp_t[-1][:10] if comp_t else ""

    return {
        "portfolioChangePct": port_change, "startDate": start_date_str, "endDate": end_date_str,
        "compT": comp_t, "compSeries": comp_series, "etfs": etf_data,
    }


def _compute_fx_block(fx_results: list[dict], intraday: bool, max_pts: int) -> dict:
    fx_pct_cols: dict[str, pd.Series] = {}
    fx_pairs_data: dict[str, dict] = {}

    for fx_cfg, res in zip(FX_PAIRS, fx_results):
        fid = fx_cfg["id"]
        s = _align_series(res["series"], intraday)
        if s.empty:
            fx_pairs_data[fid] = {
                "id": fid, "pair": fx_cfg["pair"], "currency": fx_cfg["currency"],
                "ticker": fx_cfg["ticker"], "field": res["field"], "startPrice": 0,
                "lastPrice": 0, "lowPrice": 0, "highPrice": 0, "changePct": 0,
                "pointCount": 0, "startDate": "", "endDate": "", "t": [], "p": [], "pct": [],
            }
            continue

        pct_s = (s / float(s.iloc[0]) - 1.0) * 100.0
        fx_pct_cols[fid] = pct_s

        prices_list = [float(x) for x in s.to_numpy()]
        pcts_list = [float(x) for x in pct_s.to_numpy()]
        ts_list = [_fmt_ts(t, intraday) for t in s.index]

        min_idx = int(prices_list.index(min(prices_list)))
        max_idx = int(prices_list.index(max(prices_list)))
        idxs = _sample_indices(len(prices_list), max_pts, {min_idx, max_idx})

        fx_pairs_data[fid] = {
            "id": fid, "pair": fx_cfg["pair"], "currency": fx_cfg["currency"],
            "ticker": fx_cfg["ticker"], "field": res["field"],
            "startPrice": round(prices_list[0], 4), "lastPrice": round(prices_list[-1], 4),
            "lowPrice": round(min(prices_list), 4), "highPrice": round(max(prices_list), 4),
            "changePct": round(pcts_list[-1], 2), "pointCount": len(prices_list),
            "startDate": ts_list[0][:10], "endDate": ts_list[-1][:10],
            "t": [ts_list[i] for i in idxs], "p": [round(prices_list[i], 4) for i in idxs],
            "pct": [round(pcts_list[i], 2) for i in idxs],
        }

    frame = pd.DataFrame(fx_pct_cols).sort_index()
    filled = frame.ffill()
    c_idxs = _sample_indices(len(frame), max_pts)
    fx_comp_t = [_fmt_ts(frame.index[i], intraday) for i in c_idxs]
    fx_comp_series: dict[str, list[float | None]] = {}
    for fx_cfg in FX_PAIRS:
        fid = fx_cfg["id"]
        if fid in filled.columns:
            col = filled[fid].to_numpy()
            fx_comp_series[fid] = [
                round(float(col[i]), 2) if pd.notna(col[i]) and math.isfinite(float(col[i])) else None
                for i in c_idxs
            ]
        else:
            fx_comp_series[fid] = [None for _ in c_idxs]

    return {"compT": fx_comp_t, "compSeries": fx_comp_series, "pairs": fx_pairs_data}


def build_range_payload(
    range_id: str, spec: dict,
    raw_results: list[dict] | None = None, fx_results: list[dict] | None = None,
) -> tuple[dict, list[dict], list[dict]]:
    if raw_results is None or fx_results is None:
        with ThreadPoolExecutor(max_workers=7) as pool:
            etf_futs = [pool.submit(_fetch_etf, e, spec) for e in ETFS]
            fx_futs = [pool.submit(_fetch_fx, fx, spec) for fx in FX_PAIRS]
            raw_results = [f.result() for f in etf_futs]
            fx_results = [f.result() for f in fx_futs]

    intraday = spec["intraday"]
    max_pts = spec["max_pts"]

    fx_aligned_by_ccy: dict[str, pd.Series] = {}
    for res in fx_results:
        fx_aligned_by_ccy[res["currency"]] = _align_series(res["series"], intraday)

    native_block = _compute_book_block(raw_results, fx_aligned_by_ccy, intraday, max_pts, in_chf=False)
    chf_block = _compute_book_block(raw_results, fx_aligned_by_ccy, intraday, max_pts, in_chf=True)
    fx_block = _compute_fx_block(fx_results, intraday, max_pts)

    return {
        "range": range_id, "label": spec["label"], "title": spec["title"], "priceField": spec["field"],
        "portfolioChangePct": native_block["portfolioChangePct"], "startDate": native_block["startDate"],
        "endDate": native_block["endDate"], "compT": native_block["compT"], "compSeries": native_block["compSeries"],
        "etfs": native_block["etfs"], "chf": chf_block, "fx": fx_block,
    }, raw_results, fx_results


def build_daily_history_matrix(max_raw_results: list[dict], max_fx_results: list[dict]) -> dict:
    price_cols: dict[str, pd.Series] = {}
    etf_meta_daily: dict[str, dict] = {}
    for etf, res in zip(ETFS, max_raw_results):
        s: pd.Series = res["series"]
        etf_meta_daily[etf["id"]] = {
            "ticker": res["ticker"], "currency": res["currency"],
            "note": res["note"], "fallbackUsed": res["fallbackUsed"], "field": res["field"],
        }
        if not s.empty:
            price_cols[etf["id"]] = _align_series(s, intraday=False)

    fx_cols: dict[str, pd.Series] = {}
    for fx_cfg, res in zip(FX_PAIRS, max_fx_results):
        s: pd.Series = res["series"]
        if not s.empty:
            fx_cols[fx_cfg["id"]] = _align_series(s, intraday=False)

    if not price_cols:
        return {"dates": [], "prices": {}, "fx": {}, "meta": etf_meta_daily}

    df = pd.DataFrame(price_cols).sort_index()
    dates = [pd.Timestamp(ts).strftime("%Y-%m-%d") for ts in df.index]
    prices_map: dict[str, list[float | None]] = {}
    for etf in ETFS:
        eid = etf["id"]
        if eid in df.columns:
            arr = df[eid].to_numpy()
            prices_map[eid] = [
                round(float(v), 4) if pd.notna(v) and math.isfinite(float(v)) else None
                for v in arr
            ]
        else:
            prices_map[eid] = [None for _ in dates]

    fx_map: dict[str, list[float | None]] = {}
    fx_series_aligned: dict[str, pd.Series] = {}
    for fx_cfg in FX_PAIRS:
        fid = fx_cfg["id"]
        s_fx = fx_cols.get(fid)
        if s_fx is not None and not s_fx.empty:
            union_idx = df.index.union(s_fx.index).sort_values()
            aligned_fx = s_fx.reindex(union_idx).ffill().bfill().reindex(df.index)
            fx_series_aligned[fx_cfg["currency"]] = aligned_fx
            arr = aligned_fx.to_numpy()
            fx_map[fid] = [
                round(float(v), 4) if pd.notna(v) and math.isfinite(float(v)) else None
                for v in arr
            ]
        else:
            fx_map[fid] = [None for _ in dates]

    chf_ret_cols: dict[str, pd.Series] = {}
    for etf in ETFS:
        eid = etf["id"]
        if etf.get("weight", 0) <= 0 or eid not in df.columns:
            continue
        s_p = df[eid].ffill()
        ccy = etf_meta_daily[eid]["currency"]
        if ccy == "CHF":
            s_chf = s_p
        else:
            fx_ccy = "GBP" if ccy in ("GBP", "GBp") else ccy
            scale = 0.01 if ccy == "GBp" else 1.0
            s_fx_c = fx_series_aligned.get(fx_ccy)
            s_chf = s_p * scale * s_fx_c if s_fx_c is not None else s_p * scale
        
        ret_s = s_chf.pct_change()
        ret_s = ret_s.replace([float("inf"), float("-inf")], float("nan"))
        chf_ret_cols[eid] = ret_s

    ret_df = pd.DataFrame(chf_ret_cols, index=df.index)
    weights = pd.Series({e["id"]: e["weight"] for e in ETFS if e.get("weight", 0) > 0 and e["id"] in ret_df.columns})
    avail = ret_df.notna()
    w_sum = avail.mul(weights, axis=1).sum(axis=1)
    port_daily_ret = (
        ret_df.fillna(0.0).mul(weights, axis=1).sum(axis=1).where(w_sum > 0)
        / w_sum.where(w_sum > 0)
    ).fillna(0.0)
    
    port_daily_ret = port_daily_ret.clip(lower=-0.15, upper=0.15)
    if len(port_daily_ret) > 0:
        port_daily_ret.iloc[0] = 0.0

    port_chf_daily_returns = [
        round(float(r), 6) if pd.notna(r) and math.isfinite(float(r)) else 0.0
        for r in port_daily_ret.to_numpy()
    ]

    return {
        "dates": dates, "prices": prices_map, "fx": fx_map,
        "meta": etf_meta_daily, "portfolioChfDailyReturns": port_chf_daily_returns,
    }


DAILY_LOOKBACK_DAYS: dict[str, int | None] = {
    "1mo": 31, "6mo": 183, "1y": 366, "5y": 365 * 5 + 2, "max": None,
}


def _slice_results_by_days(results: list[dict], days: int | None) -> list[dict]:
    if days is None:
        return results
    cutoff = (pd.Timestamp.now(tz="Europe/Zurich").tz_localize(None).normalize() - pd.DateOffset(days=int(days)))
    sliced: list[dict] = []
    for res in results:
        s: pd.Series = res["series"]
        sub = s.loc[s.index >= cutoff] if not s.empty else s
        sliced.append({**res, "series": sub})
    return sliced


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Portefeuille — Swiss ETF Book</title>
<style>
  :root {
    --bg: #0b0d12;
    --card: #12161f;
    --card-alt: #171c28;
    --border: rgba(255, 255, 255, 0.09);
    --text: #f1f5f9;
    --muted: #94a3b8;
    --dim: #64748b;
    --pos: #34d399;
    --neg: #fb7185;
    --accent: #38bdf8;
    --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
    --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font-sans); line-height: 1.5; padding: 24px 16px 64px; }
  .container { max-width: 1320px; margin: 0 auto; display: flex; flex-direction: column; gap: 22px; }
  header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--border); padding-bottom: 18px; }
  .eyebrow { font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
  h1 { font-size: 30px; font-weight: 650; letter-spacing: -0.02em; margin-top: 4px; }
  .subtitle { color: var(--muted); font-size: 14px; max-width: 650px; margin-top: 6px; }
  .controls-col { display: flex; flex-direction: column; align-items: flex-end; gap: 10px; }
  .controls-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; justify-content: flex-end; }
  .range-bar { display: inline-flex; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 4px; gap: 4px; }
  .range-btn { background: transparent; color: var(--muted); border: none; padding: 7px 13px; font-size: 13px; font-weight: 600; border-radius: 7px; cursor: pointer; font-family: var(--font-mono); transition: all 0.12s ease; }
  .range-btn:hover { color: var(--text); background: rgba(255,255,255,0.05); }
  .range-btn.active { background: var(--text); color: var(--bg); }
  .date-picker-box { display: inline-flex; align-items: center; gap: 8px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 5px 10px; font-size: 12px; font-family: var(--font-mono); }
  .date-picker-box.active { border-color: var(--accent); box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.35); }
  .date-picker-lbl { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }
  .date-input { background: var(--card-alt); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-family: var(--font-mono); font-size: 12px; color-scheme: dark; cursor: pointer; }
  .date-input:focus { outline: none; border-color: var(--accent); }
  .date-until { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .btn-sm { background: var(--card-alt); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 9px; font-size: 11px; font-family: var(--font-mono); cursor: pointer; transition: all 0.12s ease; }
  .btn-sm:hover { background: rgba(255,255,255,0.1); border-color: rgba(255,255,255,0.22); }
  .refresh-bar { display: inline-flex; align-items: center; gap: 10px; flex-wrap: wrap; background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 5px 11px; font-size: 11px; font-family: var(--font-mono); color: var(--muted); }
  .dot-live { width: 8px; height: 8px; border-radius: 50%; background: var(--pos); display: inline-block; box-shadow: 0 0 8px rgba(52, 211, 153, 0.7); }
  .dot-paused { width: 8px; height: 8px; border-radius: 50%; background: var(--dim); display: inline-block; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 20px; }
  .card-header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
  .card-title { font-size: 17px; font-weight: 600; }
  .card-note { font-size: 13px; color: var(--muted); margin-top: 3px; max-width: 780px; }
  .kpi-val { font-family: var(--font-mono); font-size: 28px; font-weight: 650; text-align: right; }
  .kpi-lbl { font-size: 12px; color: var(--muted); text-align: right; }
  .pos { color: var(--pos); }
  .neg { color: var(--neg); }
  .badges { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
  .badge { font-size: 11px; font-family: var(--font-mono); padding: 3px 8px; border-radius: 6px; background: var(--card-alt); border: 1px solid var(--border); color: var(--muted); }
  .badge-accent { border-color: rgba(56, 189, 248, 0.4); color: var(--accent); }
  .chart-wrap { position: relative; width: 100%; user-select: none; }
  svg.chart-svg { width: 100%; display: block; overflow: visible; }
  .legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .legend-item { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 4px 9px; border-radius: 6px; background: var(--card-alt); border: 1px solid var(--border); cursor: pointer; color: var(--text); }
  .legend-item.off { opacity: 0.35; }
  .swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
  .alloc-bar { display: flex; height: 34px; width: 100%; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); margin: 14px 0 12px; }
  .alloc-slice { height: 100%; transition: opacity 0.15s; position: relative; }
  .alloc-slice:hover { opacity: 0.82; }
  .alloc-unalloc { background-color: rgba(255,255,255,0.04); background-image: repeating-linear-gradient(135deg, rgba(255,255,255,0.45) 0 1px, transparent 1px 6px); }
  .sleeve-sec { display: flex; flex-direction: column; gap: 12px; }
  .sleeve-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 8px; }
  .sleeve-title { font-size: 19px; font-weight: 600; }
  .sleeve-sub { font-size: 13px; color: var(--muted); font-family: var(--font-mono); margin-left: 8px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; }
  .etf-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
  .etf-short { font-size: 15px; font-weight: 600; }
  .etf-name { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .etf-ticker { font-size: 11px; font-family: var(--font-mono); color: var(--dim); margin-top: 3px; }
  .etf-metrics { display: flex; justify-content: space-between; align-items: flex-end; margin: 12px 0 8px; }
  .price-big { font-family: var(--font-mono); font-size: 20px; font-weight: 600; }
  .ccy { font-size: 13px; color: var(--muted); font-weight: 400; margin-left: 4px; }
  .meta-row { display: flex; justify-content: space-between; font-size: 11px; font-family: var(--font-mono); color: var(--muted); margin-top: 8px; }
  .tooltip { position: absolute; pointer-events: none; background: rgba(15, 19, 28, 0.96); border: 1px solid rgba(255,255,255,0.16); border-radius: 8px; padding: 8px 10px; font-size: 12px; font-family: var(--font-mono); z-index: 30; min-width: 195px; box-shadow: 0 8px 24px rgba(0,0,0,0.45); display: none; }
  .tt-date { color: var(--muted); font-size: 11px; margin-bottom: 4px; border-bottom: 1px solid var(--border); padding-bottom: 3px; }
  .tt-row { display: flex; justify-content: space-between; gap: 12px; padding: 1px 0; }
  .sim-controls-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 14px; margin-bottom: 14px; }
  .sim-panel { background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
  .sim-panel-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
  .sim-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; font-family: var(--font-mono); }
  .sim-input, .sim-select { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-family: var(--font-mono); font-size: 12px; color-scheme: dark; }
  .sim-input:focus, .sim-select:focus { outline: none; border-color: var(--accent); }
  .sim-kpi-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 10px; margin-bottom: 14px; }
  .sim-kpi-box { background: var(--card-alt); border: 1px solid var(--border); border-radius: 9px; padding: 9px 12px; }
  .sim-kpi-lbl { font-size: 11px; color: var(--muted); }
  .sim-kpi-val { font-size: 16px; font-family: var(--font-mono); font-weight: 600; margin-top: 2px; }
  .sim-cf-list { display: flex; flex-direction: column; gap: 6px; max-height: 155px; overflow-y: auto; }
  .sim-cf-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 11px; font-family: var(--font-mono); }
  footer { font-size: 12px; color: var(--dim); border-top: 1px solid var(--border); padding-top: 16px; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <div class="eyebrow">Swiss-style Multi-Asset ETF Book</div>
      <h1>Portefeuille</h1>
      <p class="subtitle" id="headerSubtitle">
        Nine active portfolio ETFs across equities and real estate, plus Global X Copper Miners UCITS ETF (COPX), Palantir (PLTR), and Scottish Mortgage (SMT) at 0% for information only.
        Every chart shares the active time window or custom Start Date until today and can be viewed in local currency or integrated in CHF.
      </p>
    </div>
    <div class="controls-col">
      <div class="controls-row">
        <div class="range-bar" id="rangeBar"></div>
        <div class="date-picker-box" id="customDateBox">
          <span class="date-picker-lbl">Start date</span>
          <input type="date" id="startDateInput" class="date-input" aria-label="Custom start date until today" />
          <span class="date-until" id="untilTodayLbl">&rarr; Today</span>
          <button type="button" class="btn-sm" id="applyDateBtn">Apply</button>
        </div>
        <div class="date-picker-box" id="chfToggleBox">
          <span class="date-picker-lbl">Integrate in CHF</span>
          <div class="range-bar" style="padding:2px;border-radius:7px;">
            <button type="button" class="range-btn active" id="chfNoBtn" style="padding:4px 10px;font-size:12px;">No</button>
            <button type="button" class="range-btn" id="chfYesBtn" style="padding:4px 10px;font-size:12px;">Yes</button>
          </div>
        </div>
      </div>
      <div class="refresh-bar">
        <span class="dot-live" id="refreshDot"></span>
        <span>Auto-refresh (5m):</span>
        <button type="button" class="btn-sm" id="toggleRefreshBtn">ON</button>
        <span id="countdownLbl">Next in 05:00</span>
        <span>&middot;</span>
        <span id="lastUpdatedLbl">Updated —</span>
        <button type="button" class="btn-sm" id="refreshNowBtn">Refresh now</button>
      </div>
    </div>
  </header>

  <!-- ==================== MARKET SENTIMENT & RISK BAROMETER ==================== -->
  <section class="card" id="sentimentBarSection" style="padding: 16px 20px; border-color: rgba(56,189,248,0.25);">
    <div style="display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 15px; font-weight: 650; display: flex; align-items: center; gap: 8px;">
          <span>Global Market Sentiment Barometer &amp; Historical Trend Graphs</span>
          <span id="globalSentimentBadge" class="badge badge-accent" style="font-size: 11px;">Loading...</span>
        </div>
        <div style="font-size: 12px; color: var(--muted); margin-top: 2px;">
          Real-time composite of equity volatility, bond stress, and live CNN Fear &amp; Greed. Click any card to expand history graph.
        </div>
      </div>
      <div style="text-align: right; font-family: var(--font-mono);">
        <div id="compositeScoreVal" style="font-size: 20px; font-weight: 650;">— / 100</div>
        <div style="font-size: 10px; color: var(--muted);">Global Health Index</div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px;">
      <div class="sentiment-card" onclick="openIndicatorModal('vix')" style="background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; cursor: pointer; transition: border-color 0.15s;">
        <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:var(--muted);">
          <span>CBOE VIX (Equity Fear)</span>
          <span id="vixStatusDot" class="swatch" style="background:var(--pos)"></span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:flex-end; margin: 6px 0 2px;">
          <div>
            <div id="vixVal" style="font-family:var(--font-mono); font-size:18px; font-weight:600;">—</div>
            <div id="vixBias" style="font-size:10px; font-family:var(--font-mono);" class="pos">Neutral</div>
          </div>
          <svg viewBox="0 0 90 28" style="width:90px; height:28px; overflow:visible;">
            <path id="vixSparkPath" d="" fill="none" stroke="#fb7185" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>

      <div class="sentiment-card" onclick="openIndicatorModal('vvix')" style="background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; cursor: pointer; transition: border-color 0.15s;">
        <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:var(--muted);">
          <span>CBOE VVIX (Vol-of-Vol)</span>
          <span id="vvixStatusDot" class="swatch" style="background:var(--pos)"></span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:flex-end; margin: 6px 0 2px;">
          <div>
            <div id="vvixVal" style="font-family:var(--font-mono); font-size:18px; font-weight:600;">—</div>
            <div id="vvixBias" style="font-size:10px; font-family:var(--font-mono);" class="pos">Neutral</div>
          </div>
          <svg viewBox="0 0 90 28" style="width:90px; height:28px; overflow:visible;">
            <path id="vvixSparkPath" d="" fill="none" stroke="#fb923c" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>

      <div class="sentiment-card" onclick="openIndicatorModal('move')" style="background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; cursor: pointer; transition: border-color 0.15s;">
        <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:var(--muted);">
          <span>MOVE Index (Bond Stress)</span>
          <span id="moveStatusDot" class="swatch" style="background:var(--pos)"></span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:flex-end; margin: 6px 0 2px;">
          <div>
            <div id="moveVal" style="font-family:var(--font-mono); font-size:18px; font-weight:600;">—</div>
            <div id="moveBias" style="font-size:10px; font-family:var(--font-mono);" class="pos">Neutral</div>
          </div>
          <svg viewBox="0 0 90 28" style="width:90px; height:28px; overflow:visible;">
            <path id="moveSparkPath" d="" fill="none" stroke="#a78bfa" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>

      <div class="sentiment-card" onclick="openIndicatorModal('fng')" style="background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; cursor: pointer; transition: border-color 0.15s;">
        <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:var(--muted);">
          <span>CNN Fear &amp; Greed (Live)</span>
          <span id="fngStatusDot" class="swatch" style="background:var(--pos)"></span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:flex-end; margin: 6px 0 2px;">
          <div>
            <div id="fngVal" style="font-family:var(--font-mono); font-size:18px; font-weight:600;">—</div>
            <div id="fngBias" style="font-size:10px; font-family:var(--font-mono);" class="pos">Neutral</div>
          </div>
          <svg viewBox="0 0 90 28" style="width:90px; height:28px; overflow:visible;">
            <path id="fngSparkPath" d="" fill="none" stroke="#34d399" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>

      <div class="sentiment-card" onclick="openIndicatorModal('global')" style="background: rgba(56,189,248,0.06); border: 1px solid rgba(56,189,248,0.25); border-radius: 10px; padding: 12px 14px; cursor: pointer; display:flex; flex-direction:column; justify-content:space-between;">
        <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:var(--accent); font-weight:600; text-transform:uppercase;">
          <span>Global Direction</span>
          <span style="font-size:9px; color:var(--muted);">Click to expand</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:flex-end; margin: 6px 0 2px;">
          <div>
            <div id="globalTrendText" style="font-size:16px; font-weight:650; font-family:var(--font-mono);">Analyzing...</div>
            <div style="font-size:10px; color:var(--muted);">Aggregated Risk Appetite</div>
          </div>
          <svg viewBox="0 0 90 28" style="width:90px; height:28px; overflow:visible;">
            <path id="globalSparkPath" d="" fill="none" stroke="#38bdf8" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>
    </div>
  </section>

  <!-- Indicator Explanation & Large Graph Modal Popup (with 1D, 1W, 1M, 1Y Zoom Controls) -->
  <div id="indicatorModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.8); z-index:100; align-items:center; justify-content:center; padding:16px;">
    <div style="background:var(--card); border:1px solid var(--border); border-radius:14px; max-width:760px; width:100%; padding:22px; box-shadow:0 12px 32px rgba(0,0,0,0.6); display:flex; flex-direction:column; gap:14px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <h3 id="modalTitle" style="font-size:18px; font-weight:650;">Indicator Details</h3>
          <span id="modalActiveRangeLbl" style="font-size:11px; color:var(--muted); font-family:var(--font-mono);">Timeframe: 1 Year</span>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <div class="range-bar" id="modalRangeBar" style="padding:2px; border-radius:8px;">
            <button type="button" class="btn-sm" data-mrange="1d" onclick="setModalRange('1d')">1D</button>
            <button type="button" class="btn-sm" data-mrange="1w" onclick="setModalRange('1w')">1W</button>
            <button type="button" class="btn-sm" data-mrange="1mo" onclick="setModalRange('1mo')">1M</button>
            <button type="button" class="btn-sm" data-mrange="1y" onclick="setModalRange('1y')">1Y</button>
          </div>
          <button type="button" class="btn-sm" onclick="closeIndicatorModal()">✕ Close</button>
        </div>
      </div>
      
      <div class="chart-wrap" style="background:var(--card-alt); border:1px solid var(--border); border-radius:10px; padding:12px;">
        <svg id="modalLargeSvg" class="chart-svg" viewBox="0 0 660 220"></svg>
      </div>

      <p id="modalDesc" style="font-size:13px; color:var(--muted); line-height:1.5;"></p>
      <div id="modalInterpretation" style="background:var(--card-alt); border-left:3px solid var(--accent); padding:10px 14px; font-size:12px; font-family:var(--font-mono);"></div>
    </div>
  </div>

  <section class="card" id="globalPerfSection" style="border-color:rgba(56,189,248,0.35);">
    <div class="card-header">
      <div>
        <div class="card-title">Global Portfolio Performance in CHF &amp; % — DCA &amp; Future Simulator</div>
        <p class="card-note" id="simSubtitle">
          Starts at <strong>0.00% = 50&rsquo;000 CHF</strong> on <strong>2026-10-08</strong> using actual historical CHF returns up to Today, with optional regular/dated cash additions (DCA) and reproducible future simulation for any End Date.
        </p>
      </div>
      <div>
        <div class="kpi-val" id="simHeadlineVal">—</div>
        <div class="kpi-lbl" id="simHeadlineSub">Final portfolio value &amp; return</div>
      </div>
    </div>

    <div class="sim-controls-grid">
      <div class="sim-panel">
        <div class="sim-panel-title">
          <span>1. Capital, Start Date &amp; Horizon (Future Simulation)</span>
          <button type="button" class="btn-sm" id="simResetDatesBtn">Reset to 08.10.2026 &rarr; Today</button>
        </div>
        <div class="sim-row">
          <label for="simInitCapInput" style="color:var(--muted)">Initial (0%):</label>
          <input type="number" id="simInitCapInput" class="sim-input" style="width:115px" value="50000" min="100" step="1000" />
          <span style="color:var(--muted)">CHF</span>
          <span style="color:var(--dim)">|</span>
          <label for="simStartInput" style="color:var(--muted)">Start:</label>
          <input type="date" id="simStartInput" class="sim-input" value="2026-10-08" min="2000-01-02" max="2056-12-31" />
        </div>
        <div class="sim-row">
          <label for="simEndInput" style="color:var(--muted)">End Date:</label>
          <input type="date" id="simEndInput" class="sim-input" min="2000-01-03" max="2065-12-31" />
          <div style="display:inline-flex;flex-wrap:wrap;gap:4px;" id="simHorizonBtns">
            <button type="button" class="btn-sm" data-horizon="today">Today</button>
            <button type="button" class="btn-sm" data-horizon="3m">+3M</button>
            <button type="button" class="btn-sm" data-horizon="6m">+6M</button>
            <button type="button" class="btn-sm" data-horizon="1y">+1Y</button>
            <button type="button" class="btn-sm" data-horizon="3y">+3Y</button>
            <button type="button" class="btn-sm" data-horizon="5y">+5Y</button>
            <button type="button" class="btn-sm" data-horizon="10y">+10Y</button>
            <button type="button" class="btn-sm" data-horizon="15y">+15Y</button>
            <button type="button" class="btn-sm" data-horizon="25y">+25Y</button>
          </div>
        </div>
        <div class="sim-row">
          <label for="simHistBasisSelect" style="color:var(--muted)">Future basis:</label>
          <select id="simHistBasisSelect" class="sim-select">
            <option value="max">Max Historical Portfolio (Synchronized)</option>
            <option value="25y">25 Years Historical Portfolio</option>
            <option value="15y">15 Years Historical Portfolio</option>
            <option value="10y" selected>10 Years Historical Portfolio</option>
            <option value="5y">5 Years Historical Portfolio</option>
            <option value="1y">1 Year Historical Portfolio</option>
          </select>
          <select id="simModelSelect" class="sim-select">
            <option value="cagr" selected>Smooth CAGR + Cone</option>
            <option value="replay">Historical Replay (Seeded)</option>
            <option value="cholesky">Monte Carlo Cholesky (Seeded)</option>
          </select>
        </div>
        <div class="sim-row">
          <label for="simYMaxInput" style="color:var(--muted)">Y-Axis Max (Zoom):</label>
          <input type="text" inputmode="numeric" id="simYMaxInput" class="sim-input" style="width:125px" placeholder="Auto (e.g. 250000)" />
          <span style="color:var(--muted)">CHF</span>
          <label for="simYMinInput" style="color:var(--muted)">Min:</label>
          <input type="text" inputmode="numeric" id="simYMinInput" class="sim-input" style="width:105px" placeholder="Auto" />
          <div style="display:inline-flex;flex-wrap:wrap;gap:4px;" id="simYZoomBtns">
            <button type="button" class="btn-sm" data-ymax="auto">Auto Y</button>
            <button type="button" class="btn-sm" data-ymax="50000">50k</button>
            <button type="button" class="btn-sm" data-ymax="100000">100k</button>
            <button type="button" class="btn-sm" data-ymax="250000">250k</button>
            <button type="button" class="btn-sm" data-ymax="500000">500k</button>
            <button type="button" class="btn-sm" data-ymax="1000000">1M</button>
          </div>
        </div>
      </div>

      <div class="sim-panel">
        <div class="sim-panel-title">
          <span>2. Regular DCA Plan (Add / Remove Funds Regularly)</span>
          <span id="simDcaBadge" class="badge">Off</span>
        </div>
        <div class="sim-row">
          <select id="simDcaSignSelect" class="sim-select">
            <option value="1" selected>+ Add funds</option>
            <option value="-1">&minus; Remove funds</option>
          </select>
          <input type="number" id="simDcaAmtInput" class="sim-input" style="width:100px" value="0" min="0" step="100" />
          <span style="color:var(--muted)">CHF</span>
          <select id="simDcaFreqSelect" class="sim-select">
            <option value="none">No regular DCA</option>
            <option value="weekly">Weekly</option>
            <option value="biweekly">Bi-weekly (14d)</option>
            <option value="monthly" selected>Monthly</option>
            <option value="quarterly">Quarterly</option>
            <option value="yearly">Yearly</option>
          </select>
        </div>
        <div class="sim-row">
          <label for="simDcaStartInput" style="color:var(--muted)">First DCA date:</label>
          <input type="date" id="simDcaStartInput" class="sim-input" value="2026-11-01" />
          <button type="button" class="btn-sm" id="simPresetDca1kBtn">+1&rsquo;000/mo</button>
          <button type="button" class="btn-sm" id="simPresetDca2kBtn">+2&rsquo;000/mo</button>
          <button type="button" class="btn-sm" id="simPresetWithdrawBtn">&minus;1&rsquo;000/mo</button>
          <button type="button" class="btn-sm" id="simPresetDcaOffBtn">Clear DCA</button>
        </div>
      </div>

      <div class="sim-panel">
        <div class="sim-panel-title">
          <span>3. Scheduled Cash Flows at Specific Dates (Past or Future)</span>
          <button type="button" class="btn-sm" id="simClearCustomCfBtn">Clear all</button>
        </div>
        <div class="sim-row">
          <input type="date" id="simCfDateInput" class="sim-input" value="2027-01-15" />
          <select id="simCfSignSelect" class="sim-select">
            <option value="1" selected>+ Add</option>
            <option value="-1">&minus; Remove</option>
          </select>
          <input type="number" id="simCfAmtInput" class="sim-input" style="width:95px" value="5000" min="1" step="500" />
          <span style="color:var(--muted)">CHF</span>
          <select id="simCfFreqSelect" class="sim-select">
            <option value="once" selected>One-time</option>
            <option value="monthly">Monthly from date</option>
            <option value="quarterly">Quarterly from date</option>
            <option value="yearly">Yearly from date</option>
          </select>
          <button type="button" class="btn-sm" id="simAddCfBtn" style="border-color:rgba(56,189,248,0.5);color:var(--accent)">+ Add rule</button>
        </div>
        <div class="sim-cf-list" id="simCfList"></div>
      </div>
    </div>

    <div class="sim-kpi-strip" id="simKpiStrip"></div>
    <div class="badges" id="simBadges"></div>

    <div class="chart-wrap" id="simChartWrap">
      <svg class="chart-svg" id="simSvg" viewBox="0 0 1100 350"></svg>
      <div class="tooltip" id="simTip"></div>
    </div>
    <div class="legend" id="simLegend"></div>
  </section>

  <section class="card">
    <div class="card-header">
      <div>
        <div class="card-title" id="compTitle">All ETFs &amp; Weighted Portfolio — Cumulative Return (%)</div>
        <p class="card-note" id="compNote">Percent change from the first observation in the selected window.</p>
      </div>
      <div>
        <div class="kpi-val" id="portPct">—</div>
        <div class="kpi-lbl" id="portLbl">Portfolio weighted return</div>
      </div>
    </div>
    <div class="badges" id="compBadges"></div>
    <div class="chart-wrap" id="compWrap">
      <svg class="chart-svg" id="compSvg" viewBox="0 0 1100 360"></svg>
      <div class="tooltip" id="compTip"></div>
    </div>
    <div class="legend" id="compLegend"></div>
  </section>

  <section class="card" id="fxSection">
    <div class="card-header">
      <div>
        <div class="card-title">Portfolio Currencies vs. CHF (USD/CHF &middot; EUR/CHF &middot; GBP/CHF)</div>
        <p class="card-note">Exchange rates of non-CHF portfolio currencies against the Swiss Franc.</p>
      </div>
      <div class="badges" id="fxBadges" style="margin-bottom:0"></div>
    </div>
    <div class="chart-wrap" id="fxCompWrap">
      <svg class="chart-svg" id="fxCompSvg" viewBox="0 0 1100 260"></svg>
      <div class="tooltip" id="fxCompTip"></div>
    </div>
    <div class="legend" id="fxCompLegend"></div>
    <div class="grid" id="fxGrid" style="margin-top:16px"></div>
  </section>

  <section class="card">
    <div class="card-header" style="margin-bottom:6px">
      <div>
        <div class="card-title">Portfolio Allocation (99% Allocated &middot; 1% Unallocated &middot; Satellites 0% Info)</div>
        <p class="card-note">Actions 77% &middot; Immobilier 22% &middot; COPX, PLTR &amp; SMT 0% info &middot; Unallocated 1%.</p>
      </div>
      <span class="badge">99% / 100% allocated</span>
    </div>
    <div class="alloc-bar" id="allocBar"></div>
    <div class="legend" id="allocLegend"></div>
  </section>

  <div id="sleevesContainer" style="display:flex;flex-direction:column;gap:28px;"></div>

  <footer>
    <span id="footerStamp"></span>
    <span>Generated by etf_dashboard.py &middot; Auto-refreshes every 5 minutes</span>
  </footer>
</div>

<script>
let PAYLOAD = __PAYLOAD_JSON__;
const REFRESH_SECONDS = 300;
let currentMode = "preset";
let currentRange = "1y";
let customStartDate = "";
let integrateChf = false;
let autoRefreshEnabled = true;
let secondsLeft = REFRESH_SECONDS;
const hiddenSeries = new Set();
const hiddenFxSeries = new Set();

let currentModalKey = "vix";
let currentModalRange = "1y";

const INDICATOR_INFO = {
  vix: {
    title: "CBOE Volatility Index (VIX)",
    desc: "Measures 30-day expected volatility of the S&P 500 derived from options pricing. Often called the 'Fear Gauge'.",
    bullish: "Low VIX (< 18) indicates market complacency and stability (Bullish for equities).",
    bearish: "High VIX (> 25) signals panic, hedging demand, and market sell-offs (Bearish).",
    color: "#fb7185"
  },
  vvix: {
    title: "CBOE Volatility of Volatility (VVIX)",
    desc: "Measures the expected volatility of the VIX itself. A rising VVIX signals that traders are aggressively panic-buying VIX options.",
    bullish: "Low, stable VVIX (< 90) indicates calm options markets.",
    bearish: "Spiking VVIX (> 110) often precedes sharp equity corrections and sudden liquidity shocks.",
    color: "#fb923c"
  },
  move: {
    title: "ICE BofA MOVE Index (Bond Market Volatility)",
    desc: "Measures implied volatility in U.S. Treasury options. It is effectively the 'VIX for the bond market'.",
    bullish: "Low MOVE (< 90) means stable interest rates and healthy fixed-income liquidity.",
    bearish: "Elevated MOVE (> 115) indicates severe bond market stress or rapid interest rate uncertainty.",
    color: "#a78bfa"
  },
  fng: {
    title: "CNN Fear & Greed Index (Live API)",
    desc: "A behavioral composite index tracking 7 market dimensions (momentum, safe-haven demand, junk bond spreads, put/call ratios, etc.).",
    bullish: "Moderate/Extreme Greed (> 55) supports ongoing upward market trends.",
    bearish: "Fear / Extreme Fear (< 45) signals investor panic or capitulation.",
    color: "#34d399"
  },
  global: {
    title: "Global Health Index (Aggregated Barometer)",
    desc: "A composite score blending equity volatility (VIX, VVIX), bond stress (MOVE), and behavioral sentiment (Fear & Greed) into a 0–100 scale.",
    bullish: "Score >= 60 indicates an expansionary, risk-on market environment.",
    bearish: "Score < 40 indicates defensive positioning and systemic stress.",
    color: "#38bdf8"
  }
};

function openIndicatorModal(key) {
  currentModalKey = key;
  const info = INDICATOR_INFO[key];
  if (!info) return;
  document.getElementById("modalTitle").textContent = info.title;
  document.getElementById("modalDesc").textContent = info.desc;
  document.getElementById("modalInterpretation").innerHTML = `<strong>Interpretation:</strong><br/>${info.bullish}<br/>${info.bearish}`;
  
  updateModalGraph();
  document.getElementById("indicatorModal").style.display = "flex";
}

function setModalRange(rangeKey) {
  currentModalRange = rangeKey;
  updateModalGraph();
}

function updateModalGraph() {
  const info = INDICATOR_INFO[currentModalKey];
  if (!info) return;

  const rangeLabels = { "1d": "1 Day (5m)", "1w": "1 Week (30m)", "1mo": "1 Month", "1y": "1 Year" };
  document.getElementById("modalActiveRangeLbl").textContent = `Timeframe: ${rangeLabels[currentModalRange] || currentModalRange.toUpperCase()}`;

  document.querySelectorAll("#modalRangeBar button").forEach(btn => {
    const isAct = btn.getAttribute("data-mrange") === currentModalRange;
    btn.style.background = isAct ? "var(--text)" : "transparent";
    btn.style.color = isAct ? "var(--bg)" : "var(--muted)";
  });

  const sentimentHistObj = PAYLOAD.sentimentHistory || {};
  const activeDataset = sentimentHistObj[currentModalRange] || sentimentHistObj["1y"] || {};
  const datesArr = activeDataset.dates || [];
  const dataArr = activeDataset[currentModalKey] || [];

  drawLargeModalGraph(document.getElementById("modalLargeSvg"), dataArr, datesArr, info.color);
}

function closeIndicatorModal() {
  document.getElementById("indicatorModal").style.display = "none";
}

function drawSparkline(pathEl, dataArray, color) {
  if (!pathEl || !dataArray || dataArray.length < 2) return;
  const W = 90, H = 28;
  const valid = dataArray.filter(v => v != null && Number.isFinite(v));
  if (valid.length < 2) return;

  let minV = Math.min(...valid), maxV = Math.max(...valid);
  if (maxV === minV) { maxV += 1; minV -= 1; }
  const span = maxV - minV;

  const xAt = i => (i / (valid.length - 1)) * W;
  const yAt = v => H - ((v - minV) / span) * (H - 4) - 2;

  let d = "";
  valid.forEach((v, i) => {
    d += (i === 0 ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
  });
  pathEl.setAttribute("d", d);
  pathEl.setAttribute("stroke", color);
}

function drawLargeModalGraph(svgEl, dataArray, datesArray, color) {
  if (!svgEl) return;
  svgEl.innerHTML = "";
  const W = 660, H = 220, padL = 50, padR = 20, padT = 15, padB = 35;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const valid = dataArray.filter(v => v != null && Number.isFinite(v));
  if (valid.length < 2) {
    svgEl.innerHTML = `<text x="${W/2}" y="${H/2}" text-anchor="middle" fill="#64748b" font-size="12">No data points available for this timeframe.</text>`;
    return;
  }

  let minV = Math.min(...valid), maxV = Math.max(...valid);
  if (maxV === minV) { maxV += 1; minV -= 1; }
  const span = maxV - minV;
  minV -= span * 0.05; maxV += span * 0.05;

  const xAt = i => padL + (i / (valid.length - 1)) * plotW;
  const yAt = v => padT + plotH - ((v - minV) / (maxV - minV)) * plotH;

  let html = "";
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const val = minV + (i / ticks) * (maxV - minV);
    const y = yAt(val);
    html += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(255,255,255,0.08)" stroke-dasharray="3 3"/>`;
    html += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="11" font-family="monospace">${val.toFixed(1)}</text>`;
  }

  const dateSteps = Math.min(5, valid.length - 1);
  for (let i = 0; i <= dateSteps; i++) {
    const idx = Math.round((i / dateSteps) * (valid.length - 1));
    const x = xAt(idx);
    const dateStr = (datesArray && datesArray[idx]) ? datesArray[idx] : `Pt ${idx}`;
    html += `<text x="${x}" y="${H - 8}" text-anchor="middle" fill="#64748b" font-size="11" font-family="monospace">${dateStr}</text>`;
  }

  let d = "";
  valid.forEach((v, i) => {
    d += (i === 0 ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
  });
  const areaD = `${d} L${xAt(valid.length - 1).toFixed(1)},${(padT + plotH).toFixed(1)} L${padL},${(padT + plotH).toFixed(1)} Z`;
  html += `<path d="${areaD}" fill="${color}" fill-opacity="0.12"/>`;
  html += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`;

  svgEl.innerHTML = html;
}

async function updateSentimentBar() {
  let fngVal = 21.0;
  try {
    const resp = await fetch('https://production.dataviz.cnn.io/index/fearandgreed/graphdata');
    if (resp.ok) {
      const data = await resp.json();
      if (data && data.fear_and_greed && data.fear_and_greed.score) {
        fngVal = parseFloat(data.fear_and_greed.score);
      }
    }
  } catch (e) {}

  const sentimentHist = (PAYLOAD.sentimentHistory && PAYLOAD.sentimentHistory["1y"]) || PAYLOAD.sentimentHistory || {};
  const vixSeries = sentimentHist.vix || [];
  const vvixSeries = sentimentHist.vvix || [];
  const moveSeries = sentimentHist.move || [];
  const fngSeries = sentimentHist.fng || [];
  const globalSeries = sentimentHist.global || [];

  const vixVal = vixSeries.length > 0 ? vixSeries[vixSeries.length - 1] : 14.85;
  const vvixVal = vvixSeries.length > 0 ? vvixSeries[vvixSeries.length - 1] : 88.5;
  const moveVal = moveSeries.length > 0 ? moveSeries[moveSeries.length - 1] : 92.4;

  setIndicatorCard("vix", vixVal, vixVal < 20 ? "Bullish" : "Bearish", vixVal < 20 ? "pos" : "neg");
  setIndicatorCard("vvix", vvixVal, vvixVal < 100 ? "Bullish" : "Bearish", vvixVal < 100 ? "pos" : "neg");
  setIndicatorCard("move", moveVal, moveVal < 100 ? "Bullish" : "Bearish", moveVal < 100 ? "pos" : "neg");

  let fngBias = "Neutral"; let fngClass = "";
  if (fngVal > 55) { fngBias = "Bullish"; fngClass = "pos"; }
  else if (fngVal < 45) { fngBias = "Bearish"; fngClass = "neg"; }
  setIndicatorCard("fng", fngVal, fngBias, fngClass);

  drawSparkline(document.getElementById("vixSparkPath"), vixSeries, "#fb7185");
  drawSparkline(document.getElementById("vvixSparkPath"), vvixSeries, "#fb923c");
  drawSparkline(document.getElementById("moveSparkPath"), moveSeries, "#a78bfa");
  drawSparkline(document.getElementById("fngSparkPath"), fngSeries.length ? fngSeries : [fngVal, fngVal], "#34d399");
  drawSparkline(document.getElementById("globalSparkPath"), globalSeries, "#38bdf8");

  const vixScore = Math.max(0, Math.min(100, 100 - ((vixVal - 10) * 2.5)));
  const vvixScore = Math.max(0, Math.min(100, 100 - ((vvixVal - 70) * 0.8)));
  const moveScore = Math.max(0, Math.min(100, 100 - ((moveVal - 60) * 0.8)));
  const fngScore = fngVal;

  const composite = globalSeries.length > 0 ? globalSeries[globalSeries.length - 1] : Math.round((vixScore * 0.35) + (fngScore * 0.35) + (moveScore * 0.15) + (vvixScore * 0.15));

  document.getElementById("compositeScoreVal").textContent = `${composite} / 100`;
  const trendEl = document.getElementById("globalTrendText");
  const badgeEl = document.getElementById("globalSentimentBadge");

  if (composite >= 60) {
    trendEl.textContent = "BULLISH (Risk-On)"; trendEl.className = "pos"; badgeEl.textContent = "Expansionary";
  } else if (composite >= 40) {
    trendEl.textContent = "NEUTRAL / MIXED"; trendEl.className = ""; badgeEl.textContent = "Consolidating";
  } else {
    trendEl.textContent = "BEARISH (Risk-Off)"; trendEl.className = "neg"; badgeEl.textContent = "Defensive Stance";
  }
}

function setIndicatorCard(id, val, bias, cssClass) {
  const valEl = document.getElementById(`${id}Val`);
  if (valEl) valEl.textContent = val.toFixed(2);
  const biasEl = document.getElementById(`${id}Bias`);
  if (biasEl) {
    biasEl.textContent = bias;
    biasEl.className = cssClass;
  }
  const dotEl = document.getElementById(`${id}StatusDot`);
  if (dotEl) dotEl.style.background = cssClass === "pos" ? "var(--pos)" : "var(--neg)";
}

function saveViewState() {
  try {
    const st = { mode: currentMode, range: currentRange, startDate: customStartDate, integrateChf, autoRefresh: autoRefreshEnabled };
    localStorage.setItem("portefeuille_state_v1", JSON.stringify(st));
    const chfSuffix = integrateChf ? "&chf=1" : "";
    if (currentMode === "custom" && customStartDate) {
      history.replaceState(null, "", `#start=${customStartDate}${chfSuffix}`);
    } else {
      history.replaceState(null, "", `#range=${currentRange}${chfSuffix}`);
    }
  } catch (e) {}
}

function loadViewState() {
  try {
    const raw = localStorage.getItem("portefeuille_state_v1");
    if (raw) {
      const parsed = JSON.parse(raw);
      if (typeof parsed.autoRefresh === "boolean") autoRefreshEnabled = parsed.autoRefresh;
      if (typeof parsed.integrateChf === "boolean") integrateChf = parsed.integrateChf;
      if (parsed.mode === "custom" && parsed.startDate) {
        currentMode = "custom";
        customStartDate = parsed.startDate;
      } else if (parsed.range && PAYLOAD.ranges[parsed.range]) {
        currentMode = "preset";
        currentRange = parsed.range;
      }
    }
    const hash = window.location.hash.replace(/^#/, "");
    if (hash) {
      const params = new URLSearchParams(hash);
      if (params.get("chf") === "1" || params.get("chf") === "yes") integrateChf = true;
      if (params.get("chf") === "0" || params.get("chf") === "no") integrateChf = false;
      const stDt = params.get("start");
      const rg = params.get("range");
      if (stDt && /^\\d{4}-\\d{2}-\\d{2}$/.test(stDt)) {
        currentMode = "custom";
        customStartDate = stDt;
        return;
      }
      if (rg && PAYLOAD.ranges[rg]) {
        currentMode = "preset";
        currentRange = rg;
        return;
      }
    }
  } catch (e) {}
  if (PAYLOAD.initialStartDate && currentMode !== "custom") {
    currentMode = "custom";
    customStartDate = PAYLOAD.initialStartDate;
  }
}

function fmtNum(v, digits = 2) {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("de-CH", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtPct(v) {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v > 0 ? "+" : "") + v.toFixed(2) + "%";
}
function pctClass(v) { return v > 0 ? "pos" : v < 0 ? "neg" : ""; }

function sampleIndices(n, maxPts, extraSet) {
  if (n <= maxPts) return Array.from({ length: n }, (_, i) => i);
  const s = new Set([0, n - 1]);
  if (extraSet) extraSet.forEach(i => { if (i >= 0 && i < n) s.add(i); });
  for (let i = 1; i < maxPts - 1; i++) {
    s.add(Math.round((i * (n - 1)) / (maxPts - 1)));
  }
  return Array.from(s).sort((a, b) => a - b);
}

function getFxRateForCurrencyAt(fxSlices, ccy, idx) {
  if (!ccy || ccy === "CHF") return 1.0;
  if (ccy === "EUR") return fxSlices.eurchf ? fxSlices.eurchf[idx] : 1.0;
  if (ccy === "GBP") return fxSlices.gbpchf ? fxSlices.gbpchf[idx] : 1.0;
  if (ccy === "GBp") {
    const r = fxSlices.gbpchf ? fxSlices.gbpchf[idx] : null;
    return r != null ? r * 0.01 : 0.01;
  }
  if (ccy === "USD") return fxSlices.usdchf ? fxSlices.usdchf[idx] : 1.0;
  return 1.0;
}

function buildCustomBookSlice(sliceDates, startIdx, daily, fxSlices, inChf) {
  const M = sliceDates.length;
  const maxPts = 180;
  const etfOut = {};
  const pctAligned = {};

  PAYLOAD.etfs.forEach(etf => {
    const eid = etf.id;
    const meta = (daily.meta && daily.meta[eid]) || {};
    const nativeCcy = meta.currency || "CHF";
    const dispCcy = inChf ? "CHF" : nativeCcy;
    const fullPrices = (daily.prices && daily.prices[eid]) || [];
    const subPrices = fullPrices.slice(startIdx);

    const convertedPrices = new Array(M).fill(null);
    const validPrices = [];
    const validDates = [];
    for (let i = 0; i < M; i++) {
      const rawP = subPrices[i];
      if (rawP != null && Number.isFinite(rawP) && rawP > 0) {
        const fxRate = inChf ? getFxRateForCurrencyAt(fxSlices, nativeCcy, i) : 1.0;
        if (fxRate != null && Number.isFinite(fxRate) && fxRate > 0) {
          const p = rawP * fxRate;
          convertedPrices[i] = p;
          validPrices.push(p);
          validDates.push(sliceDates[i]);
        }
      }
    }

    if (validPrices.length === 0) {
      etfOut[eid] = {
        ticker: meta.ticker || etf.requestedTicker, currency: dispCcy, nativeCurrency: nativeCcy,
        note: meta.note || null, fallbackUsed: Boolean(meta.fallbackUsed), field: meta.field || "Adj Close",
        startPrice: 0, lastPrice: 0, lowPrice: 0, highPrice: 0, changePct: 0, pointCount: 0,
        startDate: sliceDates[0] || "", endDate: sliceDates[sliceDates.length - 1] || "", t: [], p: [], pct: [],
      };
      pctAligned[eid] = new Array(M).fill(null);
      return;
    }

    const p0 = validPrices[0];
    const validPcts = validPrices.map(p => ((p / p0) - 1) * 100);
    const alignedArr = new Array(M).fill(null);
    let lastPct = null;
    for (let i = 0; i < M; i++) {
      const p = convertedPrices[i];
      if (p != null && Number.isFinite(p) && p > 0) {
        lastPct = ((p / p0) - 1) * 100;
      }
      alignedArr[i] = lastPct;
    }
    pctAligned[eid] = alignedArr;

    let minP = validPrices[0], maxP = validPrices[0], minIdx = 0, maxIdx = 0;
    validPrices.forEach((v, idx) => {
      if (v < minP) { minP = v; minIdx = idx; }
      if (v > maxP) { maxP = v; maxIdx = idx; }
    });

    const pickIdxs = sampleIndices(validPrices.length, maxPts, [minIdx, maxIdx]);
    etfOut[eid] = {
      ticker: meta.ticker || etf.requestedTicker, currency: dispCcy, nativeCurrency: nativeCcy,
      note: meta.note || null, fallbackUsed: Boolean(meta.fallbackUsed), field: meta.field || "Adj Close",
      startPrice: Number(p0.toFixed(4)), lastPrice: Number(validPrices[validPrices.length - 1].toFixed(4)),
      lowPrice: Number(minP.toFixed(4)), highPrice: Number(maxP.toFixed(4)),
      changePct: Number(validPcts[validPcts.length - 1].toFixed(2)), pointCount: validPrices.length,
      startDate: validDates[0], endDate: validDates[validDates.length - 1],
      t: pickIdxs.map(i => validDates[i]), p: pickIdxs.map(i => Number(validPrices[i].toFixed(4))),
      pct: pickIdxs.map(i => Number(validPcts[i].toFixed(2))),
    };
  });

  const portFull = new Array(M).fill(null);
  for (let i = 0; i < M; i++) {
    let wSum = 0; let num = 0;
    PAYLOAD.etfs.forEach(etf => {
      if (etf.weight > 0) {
        const val = pctAligned[etf.id][i];
        if (val != null && Number.isFinite(val)) {
          wSum += etf.weight; num += val * etf.weight;
        }
      }
    });
    portFull[i] = wSum > 0 ? num / wSum : null;
  }

  const cIdxs = sampleIndices(M, maxPts);
  const compT = cIdxs.map(i => sliceDates[i]);
  const compSeries = { portfolio: cIdxs.map(i => portFull[i] != null ? Number(portFull[i].toFixed(2)) : null) };
  PAYLOAD.etfs.forEach(etf => {
    const arr = pctAligned[etf.id];
    compSeries[etf.id] = cIdxs.map(i => arr[i] != null ? Number(arr[i].toFixed(2)) : null);
  });

  let lastPort = 0;
  for (let i = M - 1; i >= 0; i--) {
    if (portFull[i] != null && Number.isFinite(portFull[i])) {
      lastPort = Number(portFull[i].toFixed(2));
      break;
    }
  }

  return {
    portfolioChangePct: lastPort, startDate: sliceDates[0] || "", endDate: sliceDates[sliceDates.length - 1] || "",
    compT, compSeries, etfs: etfOut,
  };
}

function buildCustomFxSlice(sliceDates, fxSlices) {
  const M = sliceDates.length;
  const maxPts = 180;
  const fxPairsOut = {};
  const fxPctAligned = {};
  const fxList = PAYLOAD.fxPairs || [];

  fxList.forEach(fx => {
    const fid = fx.id;
    const subPrices = fxSlices[fid] || [];
    const validPrices = []; const validDates = [];
    for (let i = 0; i < M; i++) {
      const p = subPrices[i];
      if (p != null && Number.isFinite(p) && p > 0) {
        validPrices.push(p); validDates.push(sliceDates[i]);
      }
    }

    if (validPrices.length === 0) {
      fxPairsOut[fid] = {
        id: fid, pair: fx.pair, currency: fx.currency, ticker: fx.ticker, field: "Close",
        startPrice: 0, lastPrice: 0, lowPrice: 0, highPrice: 0, changePct: 0, pointCount: 0,
        startDate: sliceDates[0] || "", endDate: sliceDates[sliceDates.length - 1] || "", t: [], p: [], pct: [],
      };
      fxPctAligned[fid] = new Array(M).fill(null);
      return;
    }

    const p0 = validPrices[0];
    const validPcts = validPrices.map(p => ((p / p0) - 1) * 100);
    const alignedArr = new Array(M).fill(null);
    let lastPct = null;
    for (let i = 0; i < M; i++) {
      const p = subPrices[i];
      if (p != null && Number.isFinite(p) && p > 0) {
        lastPct = ((p / p0) - 1) * 100;
      }
      alignedArr[i] = lastPct;
    }
    fxPctAligned[fid] = alignedArr;

    let minP = validPrices[0], maxP = validPrices[0], minIdx = 0, maxIdx = 0;
    validPrices.forEach((v, idx) => {
      if (v < minP) { minP = v; minIdx = idx; }
      if (v > maxP) { maxP = v; maxIdx = idx; }
    });

    const pickIdxs = sampleIndices(validPrices.length, maxPts, [minIdx, maxIdx]);
    fxPairsOut[fid] = {
      id: fid, pair: fx.pair, currency: fx.currency, ticker: fx.ticker, field: "Close",
      startPrice: Number(p0.toFixed(4)), lastPrice: Number(validPrices[validPrices.length - 1].toFixed(4)),
      lowPrice: Number(minP.toFixed(4)), highPrice: Number(maxP.toFixed(4)),
      changePct: Number(validPcts[validPcts.length - 1].toFixed(2)), pointCount: validPrices.length,
      startDate: validDates[0], endDate: validDates[validDates.length - 1],
      t: pickIdxs.map(i => validDates[i]), p: pickIdxs.map(i => Number(validPrices[i].toFixed(4))),
      pct: pickIdxs.map(i => Number(validPcts[i].toFixed(2))),
    };
  });

  const cIdxs = sampleIndices(M, maxPts);
  const compT = cIdxs.map(i => sliceDates[i]);
  const compSeries = {};
  fxList.forEach(fx => {
    const arr = fxPctAligned[fx.id] || [];
    compSeries[fx.id] = cIdxs.map(i => arr[i] != null ? Number(arr[i].toFixed(2)) : null);
  });

  return { compT, compSeries, pairs: fxPairsOut };
}

function buildCustomIntradayBookSlice(srcBlock, startDateStr) {
  let compT = (srcBlock.compT || []).filter(ts => ts.slice(0, 10) >= startDateStr);
  if (compT.length < 2 && srcBlock.compT && srcBlock.compT.length >= 2) {
    compT = srcBlock.compT.slice(-2);
  }
  const M = compT.length;
  const etfOut = {};
  const pctAligned = {};

  PAYLOAD.etfs.forEach(etf => {
    const eid = etf.id;
    const srcEtf = (srcBlock.etfs && srcBlock.etfs[eid]) || {};
    const srcT = srcEtf.t || []; const srcP = srcEtf.p || [];
    const validDates = []; const validPrices = [];

    for (let i = 0; i < srcT.length; i++) {
      if (srcT[i].slice(0, 10) >= startDateStr && srcP[i] != null && Number.isFinite(srcP[i]) && srcP[i] > 0) {
        validDates.push(srcT[i]); validPrices.push(srcP[i]);
      }
    }
    if (validPrices.length === 0 && srcP.length > 0) {
      const startK = Math.max(0, srcP.length - 2);
      for (let i = startK; i < srcP.length; i++) {
        if (srcP[i] != null && Number.isFinite(srcP[i]) && srcP[i] > 0) {
          validDates.push(srcT[i]); validPrices.push(srcP[i]);
        }
      }
    }

    if (validPrices.length === 0) {
      etfOut[eid] = {
        ...srcEtf, startPrice: 0, lastPrice: 0, lowPrice: 0, highPrice: 0, changePct: 0, pointCount: 0,
        startDate: startDateStr, endDate: startDateStr, t: [], p: [], pct: [],
      };
      pctAligned[eid] = new Array(M).fill(null);
      return;
    }

    const p0 = validPrices[0];
    const validPcts = validPrices.map(p => ((p / p0) - 1) * 100);
    const pctByTs = {};
    for (let i = 0; i < validDates.length; i++) { pctByTs[validDates[i]] = validPcts[i]; }

    const alignedArr = new Array(M).fill(null);
    let lastPct = null;
    for (let i = 0; i < M; i++) {
      const ts = compT[i];
      if (Object.prototype.hasOwnProperty.call(pctByTs, ts)) { lastPct = pctByTs[ts]; }
      alignedArr[i] = lastPct;
    }
    pctAligned[eid] = alignedArr;

    let minP = validPrices[0], maxP = validPrices[0];
    validPrices.forEach(v => { if (v < minP) minP = v; if (v > maxP) maxP = v; });

    etfOut[eid] = {
      ...srcEtf, startPrice: Number(p0.toFixed(4)), lastPrice: Number(validPrices[validPrices.length - 1].toFixed(4)),
      lowPrice: Number(minP.toFixed(4)), highPrice: Number(maxP.toFixed(4)),
      changePct: Number(validPcts[validPcts.length - 1].toFixed(2)), pointCount: validPrices.length,
      startDate: validDates[0].slice(0, 10), endDate: validDates[validDates.length - 1].slice(0, 10),
      t: validDates, p: validPrices.map(v => Number(v.toFixed(4))), pct: validPcts.map(v => Number(v.toFixed(2))),
    };
  });

  const portFull = new Array(M).fill(null);
  for (let i = 0; i < M; i++) {
    let wSum = 0; let num = 0;
    PAYLOAD.etfs.forEach(etf => {
      if (etf.weight > 0) {
        const val = pctAligned[etf.id][i];
        if (val != null && Number.isFinite(val)) { wSum += etf.weight; num += val * etf.weight; }
      }
    });
    portFull[i] = wSum > 0 ? num / wSum : null;
  }

  const compSeries = { portfolio: portFull.map(v => v != null ? Number(v.toFixed(2)) : null) };
  PAYLOAD.etfs.forEach(etf => {
    const arr = pctAligned[etf.id];
    compSeries[etf.id] = arr.map(v => v != null ? Number(v.toFixed(2)) : null);
  });

  let lastPort = 0;
  for (let i = M - 1; i >= 0; i--) {
    if (portFull[i] != null && Number.isFinite(portFull[i])) { lastPort = Number(portFull[i].toFixed(2)); break; }
  }

  return {
    portfolioChangePct: lastPort, startDate: compT[0] ? compT[0].slice(0, 10) : startDateStr,
    endDate: compT[compT.length - 1] ? compT[compT.length - 1].slice(0, 10) : startDateStr,
    compT, compSeries, etfs: etfOut,
  };
}

function buildCustomIntradayFxSlice(srcFxBlock, startDateStr) {
  if (!srcFxBlock) return { compT: [], compSeries: {}, pairs: {} };
  let compT = (srcFxBlock.compT || []).filter(ts => ts.slice(0, 10) >= startDateStr);
  if (compT.length < 2 && srcFxBlock.compT && srcFxBlock.compT.length >= 2) {
    compT = srcFxBlock.compT.slice(-2);
  }
  const M = compT.length;
  const fxPairsOut = {}; const fxPctAligned = {};
  const fxList = PAYLOAD.fxPairs || [];

  fxList.forEach(fx => {
    const fid = fx.id;
    const srcPair = (srcFxBlock.pairs && srcFxBlock.pairs[fid]) || {};
    const srcT = srcPair.t || []; const srcP = srcPair.p || [];
    const validDates = []; const validPrices = [];

    for (let i = 0; i < srcT.length; i++) {
      if (srcT[i].slice(0, 10) >= startDateStr && srcP[i] != null && Number.isFinite(srcP[i]) && srcP[i] > 0) {
        validDates.push(srcT[i]); validPrices.push(srcP[i]);
      }
    }
    if (validPrices.length === 0 && srcP.length > 0) {
      const startK = Math.max(0, srcP.length - 2);
      for (let i = startK; i < srcP.length; i++) {
        if (srcP[i] != null && Number.isFinite(srcP[i]) && srcP[i] > 0) {
          validDates.push(srcT[i]); validPrices.push(srcP[i]);
        }
      }
    }

    if (validPrices.length === 0) {
      fxPairsOut[fid] = {
        ...srcPair, id: fid, pair: fx.pair, currency: fx.currency, ticker: fx.ticker, field: "Close",
        startPrice: 0, lastPrice: 0, lowPrice: 0, highPrice: 0, changePct: 0, pointCount: 0,
        startDate: startDateStr, endDate: startDateStr, t: [], p: [], pct: [],
      };
      fxPctAligned[fid] = new Array(M).fill(null);
      return;
    }

    const p0 = validPrices[0];
    const validPcts = validPrices.map(p => ((p / p0) - 1) * 100);
    const pctByTs = {};
    for (let i = 0; i < validDates.length; i++) { pctByTs[validDates[i]] = validPcts[i]; }

    const alignedArr = new Array(M).fill(null);
    let lastPct = null;
    for (let i = 0; i < M; i++) {
      const ts = compT[i];
      if (Object.prototype.hasOwnProperty.call(pctByTs, ts)) { lastPct = pctByTs[ts]; }
      alignedArr[i] = lastPct;
    }
    fxPctAligned[fid] = alignedArr;

    let minP = validPrices[0], maxP = validPrices[0];
    validPrices.forEach(v => { if (v < minP) minP = v; if (v > maxP) maxP = v; });

    fxPairsOut[fid] = {
      ...srcPair, id: fid, pair: fx.pair, currency: fx.currency, ticker: fx.ticker, field: "Close",
      startPrice: Number(p0.toFixed(4)), lastPrice: Number(validPrices[validPrices.length - 1].toFixed(4)),
      lowPrice: Number(minP.toFixed(4)), highPrice: Number(maxP.toFixed(4)),
      changePct: Number(validPcts[validPcts.length - 1].toFixed(2)), pointCount: validPrices.length,
      startDate: validDates[0].slice(0, 10), endDate: validDates[validDates.length - 1].slice(0, 10),
      t: validDates, p: validPrices.map(v => Number(v.toFixed(4))), pct: validPcts.map(v => Number(v.toFixed(2))),
    };
  });

  const compSeries = {};
  fxList.forEach(fx => {
    const arr = fxPctAligned[fx.id] || [];
    compSeries[fx.id] = arr.map(v => v != null ? Number(v.toFixed(2)) : null);
  });

  return { compT, compSeries, pairs: fxPairsOut };
}

function buildCustomRangeData(startDateStr) {
  const r1w = PAYLOAD.ranges && PAYLOAD.ranges["1w"];
  const earliest1w = r1w && r1w.startDate ? r1w.startDate : "";
  if (r1w && earliest1w && startDateStr >= earliest1w) {
    const nativeBlock = buildCustomIntradayBookSlice(r1w, startDateStr);
    const chfBlock = r1w.chf ? buildCustomIntradayBookSlice(r1w.chf, startDateStr) : nativeBlock;
    const fxBlock = buildCustomIntradayFxSlice(r1w.fx, startDateStr);
    const actualStart = nativeBlock.startDate || startDateStr;
    const actualEnd = nativeBlock.endDate || startDateStr;
    return {
      range: "custom", label: `${actualStart} → Today`, title: `${actualStart} to ${actualEnd} (30m close)`,
      priceField: r1w.priceField || "Close", portfolioChangePct: nativeBlock.portfolioChangePct,
      startDate: actualStart, endDate: actualEnd, compT: nativeBlock.compT, compSeries: nativeBlock.compSeries,
      etfs: nativeBlock.etfs, chf: chfBlock, fx: fxBlock,
    };
  }

  const daily = PAYLOAD.daily;
  if (!daily || !daily.dates || daily.dates.length === 0) {
    return PAYLOAD.ranges["1y"];
  }
  const allDates = daily.dates;
  let startIdx = allDates.findIndex(d => d >= startDateStr);
  if (startIdx === -1) startIdx = Math.max(0, allDates.length - 2);
  if (startIdx >= allDates.length - 1 && allDates.length >= 2) {
    startIdx = allDates.length - 2;
  }
  const sliceDates = allDates.slice(startIdx);
  const M = sliceDates.length;

  const fxSlices = {};
  (PAYLOAD.fxPairs || []).forEach(fx => {
    const fullFx = (daily.fx && daily.fx[fx.id]) || [];
    fxSlices[fx.id] = fullFx.slice(startIdx);
  });

  const nativeBlock = buildCustomBookSlice(sliceDates, startIdx, daily, fxSlices, false);
  const chfBlock = buildCustomBookSlice(sliceDates, startIdx, daily, fxSlices, true);
  const fxBlock = buildCustomFxSlice(sliceDates, fxSlices);

  const actualStart = sliceDates[0] || startDateStr;
  const actualEnd = sliceDates[sliceDates.length - 1] || startDateStr;

  return {
    range: "custom", label: `${actualStart} → Today`, title: `${actualStart} to ${actualEnd} (${M} trading days, daily adj. close)`,
    priceField: "Adj Close", portfolioChangePct: nativeBlock.portfolioChangePct, startDate: actualStart, endDate: actualEnd,
    compT: nativeBlock.compT, compSeries: nativeBlock.compSeries, etfs: nativeBlock.etfs, chf: chfBlock, fx: fxBlock,
  };
}

function getEffectiveBookData(rData) {
  if (integrateChf && rData.chf) {
    return {
      ...rData, portfolioChangePct: rData.chf.portfolioChangePct, startDate: rData.chf.startDate || rData.startDate,
      endDate: rData.chf.endDate || rData.endDate, compT: rData.chf.compT, compSeries: rData.chf.compSeries, etfs: rData.chf.etfs,
    };
  }
  return rData;
}

function initAllocation() {
  const bar = document.getElementById("allocBar");
  const leg = document.getElementById("allocLegend");
  bar.innerHTML = ""; leg.innerHTML = "";
  PAYLOAD.etfs.forEach(etf => {
    if (etf.weight > 0) {
      const slice = document.createElement("div");
      slice.className = "alloc-slice";
      slice.style.flexGrow = etf.weight;
      slice.style.backgroundColor = etf.color;
      slice.title = `${etf.shortName} (${etf.ticker || etf.requestedTicker}) · ${etf.weight}%`;
      bar.appendChild(slice);
    }
    const item = document.createElement("div");
    item.className = "legend-item"; item.style.cursor = "default";
    const wLbl = etf.weight === 0 ? "0% (Info)" : `${etf.weight}%`;
    item.innerHTML = `<span class="swatch" style="background:${etf.color}"></span><span>${etf.shortName}</span><span style="color:var(--muted);font-family:var(--font-mono)">${wLbl}</span>`;
    leg.appendChild(item);
  });
  const un = document.createElement("div");
  un.className = "alloc-slice alloc-unalloc"; un.style.flexGrow = 1; un.title = "Unallocated · 1%";
  bar.appendChild(un);
  const unItem = document.createElement("div");
  unItem.className = "legend-item"; unItem.style.cursor = "default";
  unItem.innerHTML = `<span class="swatch alloc-unalloc" style="border:1px solid rgba(255,255,255,0.3)"></span><span>Unallocated</span><span style="color:var(--muted);font-family:var(--font-mono)">1%</span>`;
  leg.appendChild(unItem);
}

function renderRangeBar(activeData) {
  const bar = document.getElementById("rangeBar");
  bar.innerHTML = "";
  PAYLOAD.rangeOrder.forEach(rid => {
    const r = PAYLOAD.ranges[rid];
    const btn = document.createElement("button");
    btn.className = "range-btn" + (currentMode === "preset" && rid === currentRange ? " active" : "");
    btn.textContent = r.label;
    btn.onclick = () => { currentMode = "preset"; currentRange = rid; saveViewState(); renderAll(); };
    bar.appendChild(btn);
  });

  const dateInput = document.getElementById("startDateInput");
  const dateBox = document.getElementById("customDateBox");
  const untilLbl = document.getElementById("untilTodayLbl");
  const allDates = (PAYLOAD.daily && PAYLOAD.daily.dates) || [];
  const minDate = allDates[0] || "2000-01-02";
  const maxDate = allDates[allDates.length - 1] || new Date().toISOString().slice(0, 10);
  dateInput.min = minDate; dateInput.max = maxDate;
  untilLbl.innerHTML = `&rarr; Today (${maxDate})`;

  if (currentMode === "custom" && customStartDate) {
    dateInput.value = customStartDate; dateBox.classList.add("active");
  } else {
    dateInput.value = activeData.startDate || maxDate; dateBox.classList.remove("active");
  }

  const chfNoBtn = document.getElementById("chfNoBtn");
  const chfYesBtn = document.getElementById("chfYesBtn");
  const chfBox = document.getElementById("chfToggleBox");
  if (chfNoBtn && chfYesBtn) {
    chfNoBtn.className = "range-btn" + (!integrateChf ? " active" : "");
    chfYesBtn.className = "range-btn" + (integrateChf ? " active" : "");
    if (chfBox) {
      if (integrateChf) chfBox.classList.add("active"); else chfBox.classList.remove("active");
    }
  }
}

function renderComparison(rData) {
  const pctEl = document.getElementById("portPct");
  pctEl.textContent = fmtPct(rData.portfolioChangePct);
  pctEl.className = "kpi-val " + pctClass(rData.portfolioChangePct);
  document.getElementById("portLbl").textContent = `Portfolio (${rData.title}${integrateChf ? " · in CHF" : ""})`;

  const compTitle = document.getElementById("compTitle");
  const compNote = document.getElementById("compNote");
  if (compTitle) {
    compTitle.innerHTML = integrateChf ? "All ETFs &amp; Weighted Portfolio — Cumulative Return in CHF (%)" : "All ETFs &amp; Weighted Portfolio — Cumulative Return (%)";
  }
  if (compNote) {
    compNote.innerHTML = integrateChf
      ? "Percent change from first observation after converting every ETF price into Swiss Francs (CHF) via USD/CHF, EUR/CHF, and GBP/CHF. The bold white line is the 99%-weighted portfolio return in CHF."
      : "Percent change from first observation: (price / price[0] &minus; 1) &times; 100. The bold white line is the 99%-weighted portfolio average.";
  }

  document.getElementById("compBadges").innerHTML = `
    <span class="badge ${currentMode === 'custom' ? 'badge-accent' : ''}">Window: ${rData.title}</span>
    <span class="badge">Period: ${rData.startDate || '—'} &rarr; ${rData.endDate || 'Today'}</span>
    <span class="badge">Field: ${rData.priceField}</span>
    <span class="badge ${integrateChf ? 'badge-accent' : ''}">${integrateChf ? 'Integrated in CHF (FX-converted)' : 'Not FX-converted to CHF'}</span>
  `;

  const svg = document.getElementById("compSvg");
  const tip = document.getElementById("compTip");
  const W = 1100, H = 360, padL = 56, padR = 20, padT = 16, padB = 30;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const T = rData.compT; const N = T.length;
  if (N < 2) { svg.innerHTML = ""; return; }

  const activeSeries = [];
  if (!hiddenSeries.has("portfolio")) {
    activeSeries.push({ id: "portfolio", label: "Portfolio (99%)", color: "#ffffff", width: 2.8, data: rData.compSeries.portfolio });
  }
  PAYLOAD.etfs.forEach(etf => {
    if (!hiddenSeries.has(etf.id)) {
      activeSeries.push({ id: etf.id, label: etf.shortName, color: etf.color, width: 1.6, data: rData.compSeries[etf.id] || [] });
    }
  });

  let minV = 0, maxV = 0;
  activeSeries.forEach(s => s.data.forEach(v => {
    if (v != null && Number.isFinite(v)) { if (v < minV) minV = v; if (v > maxV) maxV = v; }
  }));
  if (maxV === minV) { maxV += 1; minV -= 1; }
  const span = maxV - minV; minV -= span * 0.06; maxV += span * 0.06;

  const xAt = i => padL + (i / (N - 1)) * plotW;
  const yAt = v => padT + plotH - ((v - minV) / (maxV - minV)) * plotH;

  let html = "";
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const val = minV + (i / ticks) * (maxV - minV);
    const y = yAt(val);
    html += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(255,255,255,0.07)" stroke-dasharray="3 3"/>`;
    html += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="11" font-family="monospace">${val > 0 ? "+" : ""}${val.toFixed(1)}%</text>`;
  }
  if (minV <= 0 && maxV >= 0) {
    const y0 = yAt(0);
    html += `<line x1="${padL}" y1="${y0}" x2="${W - padR}" y2="${y0}" stroke="rgba(255,255,255,0.28)" stroke-width="1"/>`;
  }

  const xSteps = Math.min(6, N - 1);
  for (let i = 0; i <= xSteps; i++) {
    const idx = Math.round((i / xSteps) * (N - 1));
    const x = xAt(idx);
    html += `<text x="${x}" y="${H - 6}" text-anchor="middle" fill="#64748b" font-size="11" font-family="monospace">${T[idx]}</text>`;
  }

  activeSeries.forEach(s => {
    let d = "";
    s.data.forEach((v, i) => {
      if (v == null || !Number.isFinite(v)) return;
      d += (d === "" ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
    });
    if (d) { html += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width}" stroke-linecap="round" stroke-linejoin="round"/>`; }
  });

  html += `<line id="compCursor" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="rgba(255,255,255,0.35)" stroke-width="1" visibility="hidden"/>`;
  html += `<rect id="compHit" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor:crosshair"/>`;
  svg.innerHTML = html;

  const hit = document.getElementById("compHit");
  const cur = document.getElementById("compCursor");
  hit.onmousemove = (ev) => {
    const rect = svg.getBoundingClientRect();
    const relX = ((ev.clientX - rect.left) / rect.width) * W;
    const idx = Math.max(0, Math.min(N - 1, Math.round(((relX - padL) / plotW) * (N - 1))));
    const cx = xAt(idx);
    cur.setAttribute("x1", cx); cur.setAttribute("x2", cx); cur.setAttribute("visibility", "visible");

    let rowsHtml = `<div class="tt-date">${T[idx]}</div>`;
    activeSeries.forEach(s => {
      const v = s.data[idx];
      rowsHtml += `<div class="tt-row"><span style="color:${s.color}">&#9632; ${s.label}</span><span class="${pctClass(v)}">${fmtPct(v)}</span></div>`;
    });
    tip.innerHTML = rowsHtml; tip.style.display = "block";
    const wrapW = document.getElementById("compWrap").clientWidth;
    const leftPx = (cx / W) * wrapW;
    tip.style.left = (leftPx > wrapW - 240 ? leftPx - 210 : leftPx + 14) + "px";
    tip.style.top = "12px";
  };
  hit.onmouseleave = () => { cur.setAttribute("visibility", "hidden"); tip.style.display = "none"; };

  const leg = document.getElementById("compLegend");
  leg.innerHTML = "";
  const allItems = [{ id: "portfolio", label: "Portfolio (99%)", color: "#ffffff" }, ...PAYLOAD.etfs.map(e => ({ id: e.id, label: `${e.shortName} (${e.weight === 0 ? '0% info' : e.weight + '%'})`, color: e.color }))];
  allItems.forEach(it => {
    const el = document.createElement("button");
    el.className = "legend-item" + (hiddenSeries.has(it.id) ? " off" : "");
    el.innerHTML = `<span class="swatch" style="background:${it.color}"></span><span>${it.label}</span>`;
    el.onclick = () => {
      if (hiddenSeries.has(it.id)) hiddenSeries.delete(it.id); else hiddenSeries.add(it.id);
      renderComparison(rData);
    };
    leg.appendChild(el);
  });
}

function drawMiniChart(svg, tip, wrap, etf, st, decimalsOverride) {
  const W = 460, H = 155, padL = 52, padR = 12, padT = 10, padB = 22;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const P = st.p || [], T = st.t || [], PCT = st.pct || [];
  const N = P.length;
  if (N < 2) { svg.innerHTML = ""; return; }

  let minV = Math.min(...P), maxV = Math.max(...P);
  if (maxV === minV) { maxV += 1; minV -= 1; }
  const span = maxV - minV; minV -= span * 0.08; maxV += span * 0.08;

  const xAt = i => padL + (i / (N - 1)) * plotW;
  const yAt = v => padT + plotH - ((v - minV) / (maxV - minV)) * plotH;

  let html = "";
  for (let i = 0; i <= 3; i++) {
    const val = minV + (i / 3) * (maxV - minV);
    const y = yAt(val);
    const axisDigits = decimalsOverride != null ? decimalsOverride : (val >= 100 ? 0 : 2);
    html += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="2 2"/>`;
    html += `<text x="${padL - 6}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="10" font-family="monospace">${fmtNum(val, axisDigits)}</text>`;
  }

  const yStart = yAt(P[0]);
  html += `<line x1="${padL}" y1="${yStart}" x2="${W - padR}" y2="${yStart}" stroke="rgba(255,255,255,0.2)" stroke-dasharray="3 3"/>`;

  let d = "";
  P.forEach((v, i) => { d += (i === 0 ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1); });
  const areaD = `${d} L${xAt(N - 1).toFixed(1)},${(padT + plotH).toFixed(1)} L${padL},${(padT + plotH).toFixed(1)} Z`;
  html += `<path d="${areaD}" fill="${etf.color}" fill-opacity="0.12"/>`;
  html += `<path d="${d}" fill="none" stroke="${etf.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;

  html += `<text x="${padL}" y="${H - 4}" text-anchor="start" fill="#64748b" font-size="10" font-family="monospace">${T[0]}</text>`;
  html += `<text x="${W - padR}" y="${H - 4}" text-anchor="end" fill="#64748b" font-size="10" font-family="monospace">${T[N - 1]}</text>`;

  html += `<line class="mini-cur" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="rgba(255,255,255,0.4)" stroke-width="1" visibility="hidden"/>`;
  html += `<rect class="mini-hit" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor:crosshair"/>`;
  svg.innerHTML = html;

  const hit = svg.querySelector(".mini-hit");
  const cur = svg.querySelector(".mini-cur");
  const ttDigits = decimalsOverride != null ? decimalsOverride : 2;
  hit.onmousemove = (ev) => {
    const rect = svg.getBoundingClientRect();
    const relX = ((ev.clientX - rect.left) / rect.width) * W;
    const idx = Math.max(0, Math.min(N - 1, Math.round(((relX - padL) / plotW) * (N - 1))));
    const cx = xAt(idx);
    cur.setAttribute("x1", cx); cur.setAttribute("x2", cx); cur.setAttribute("visibility", "visible");
    tip.innerHTML = `
      <div class="tt-date">${T[idx]}</div>
      <div class="tt-row"><span>Price</span><span>${fmtNum(P[idx], ttDigits)} ${st.currency || "CHF"}</span></div>
      <div class="tt-row"><span>Return</span><span class="${pctClass(PCT[idx])}">${fmtPct(PCT[idx])}</span></div>
    `;
    tip.style.display = "block";
    const wrapW = wrap.clientWidth;
    const leftPx = (cx / W) * wrapW;
    tip.style.left = (leftPx > wrapW - 195 ? leftPx - 180 : leftPx + 10) + "px";
    tip.style.top = "6px";
  };
  hit.onmouseleave = () => { cur.setAttribute("visibility", "hidden"); tip.style.display = "none"; };
}

function renderFxSection(rawRangeData) {
  const fxSec = document.getElementById("fxSection");
  if (!fxSec) return;
  const fxData = rawRangeData.fx;
  const fxList = PAYLOAD.fxPairs || [];
  if (!fxData || fxList.length === 0) { fxSec.style.display = "none"; return; }
  fxSec.style.display = "block";

  document.getElementById("fxBadges").innerHTML = `
    <span class="badge">Window: ${rawRangeData.title}</span>
    <span class="badge badge-accent">USD: 59%</span>
    <span class="badge">CHF: 30%</span>
    <span class="badge">EUR: 8%</span>
    <span class="badge">GBP: 2%</span>
  `;

  const svg = document.getElementById("fxCompSvg");
  const tip = document.getElementById("fxCompTip");
  const W = 1100, H = 260, padL = 56, padR = 20, padT = 16, padB = 30;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const T = fxData.compT || []; const N = T.length;
  if (N >= 2) {
    const activeSeries = [];
    fxList.forEach(fx => {
      if (!hiddenFxSeries.has(fx.id)) {
        activeSeries.push({
          id: fx.id, label: `${fx.pair} (${fx.exposureWeight}%)`, color: fx.color, width: 2.2,
          data: (fxData.compSeries && fxData.compSeries[fx.id]) || [],
        });
      }
    });

    let minV = 0, maxV = 0;
    activeSeries.forEach(s => s.data.forEach(v => {
      if (v != null && Number.isFinite(v)) { if (v < minV) minV = v; if (v > maxV) maxV = v; }
    }));
    if (maxV === minV) { maxV += 1; minV -= 1; }
    const span = maxV - minV; minV -= span * 0.08; maxV += span * 0.08;

    const xAt = i => padL + (i / (N - 1)) * plotW;
    const yAt = v => padT + plotH - ((v - minV) / (maxV - minV)) * plotH;

    let html = "";
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const val = minV + (i / ticks) * (maxV - minV);
      const y = yAt(val);
      html += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(255,255,255,0.07)" stroke-dasharray="3 3"/>`;
      html += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="11" font-family="monospace">${val > 0 ? "+" : ""}${val.toFixed(1)}%</text>`;
    }
    if (minV <= 0 && maxV >= 0) {
      const y0 = yAt(0);
      html += `<line x1="${padL}" y1="${y0}" x2="${W - padR}" y2="${y0}" stroke="rgba(255,255,255,0.28)" stroke-width="1"/>`;
    }

    const xSteps = Math.min(6, N - 1);
    for (let i = 0; i <= xSteps; i++) {
      const idx = Math.round((i / xSteps) * (N - 1));
      const x = xAt(idx);
      html += `<text x="${x}" y="${H - 6}" text-anchor="middle" fill="#64748b" font-size="11" font-family="monospace">${T[idx]}</text>`;
    }

    activeSeries.forEach(s => {
      let d = "";
      s.data.forEach((v, i) => {
        if (v == null || !Number.isFinite(v)) return;
        d += (d === "" ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
      });
      if (d) { html += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width}" stroke-linecap="round" stroke-linejoin="round"/>`; }
    });

    html += `<line id="fxCompCursor" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="rgba(255,255,255,0.35)" stroke-width="1" visibility="hidden"/>`;
    html += `<rect id="fxCompHit" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor:crosshair"/>`;
    svg.innerHTML = html;

    const hit = document.getElementById("fxCompHit");
    const cur = document.getElementById("fxCompCursor");
    hit.onmousemove = (ev) => {
      const rect = svg.getBoundingClientRect();
      const relX = ((ev.clientX - rect.left) / rect.width) * W;
      const idx = Math.max(0, Math.min(N - 1, Math.round(((relX - padL) / plotW) * (N - 1))));
      const cx = xAt(idx);
      cur.setAttribute("x1", cx); cur.setAttribute("x2", cx); cur.setAttribute("visibility", "visible");

      let rowsHtml = `<div class="tt-date">${T[idx]}</div>`;
      activeSeries.forEach(s => {
        const v = s.data[idx];
        rowsHtml += `<div class="tt-row"><span style="color:${s.color}">&#9632; ${s.label}</span><span class="${pctClass(v)}">${fmtPct(v)}</span></div>`;
      });
      tip.innerHTML = rowsHtml; tip.style.display = "block";
      const wrapW = document.getElementById("fxCompWrap").clientWidth;
      const leftPx = (cx / W) * wrapW;
      tip.style.left = (leftPx > wrapW - 240 ? leftPx - 210 : leftPx + 14) + "px";
      tip.style.top = "12px";
    };
    hit.onmouseleave = () => { cur.setAttribute("visibility", "hidden"); tip.style.display = "none"; };
  } else {
    svg.innerHTML = "";
  }

  const leg = document.getElementById("fxCompLegend");
  leg.innerHTML = "";
  fxList.forEach(fx => {
    const el = document.createElement("button");
    el.className = "legend-item" + (hiddenFxSeries.has(fx.id) ? " off" : "");
    el.innerHTML = `<span class="swatch" style="background:${fx.color}"></span><span>${fx.pair} &middot; ${fx.name} (${fx.exposureWeight}%)</span>`;
    el.onclick = () => {
      if (hiddenFxSeries.has(fx.id)) hiddenFxSeries.delete(fx.id); else hiddenFxSeries.add(fx.id);
      renderFxSection(rawRangeData);
    };
    leg.appendChild(el);
  });

  const grid = document.getElementById("fxGrid");
  grid.innerHTML = "";
  fxList.forEach(fx => {
    const st = (fxData.pairs && fxData.pairs[fx.id]) || {
      ticker: fx.ticker, currency: "CHF", field: "Close", startPrice: 0, lastPrice: 0,
      lowPrice: 0, highPrice: 0, changePct: 0, pointCount: 0, startDate: "", endDate: "", t: [], p: [], pct: [],
    };
    const stWithChf = { ...st, currency: "CHF" };
    const card = document.createElement("div");
    card.className = "card"; card.style.padding = "16px"; card.style.background = "var(--card-alt)";
    card.innerHTML = `
      <div class="etf-top">
        <div>
          <div class="etf-short"><span class="swatch" style="background:${fx.color};margin-right:6px"></span>${fx.pair}</div>
          <div class="etf-name">${fx.name} &middot; ${fx.exposureNote}</div>
          <div class="etf-ticker">${st.ticker} &middot; ${st.pointCount || 0} obs</div>
        </div>
        <span class="badge">${fx.exposureWeight}% exp</span>
      </div>
      <div class="etf-metrics">
        <div>
          <div class="price-big">${fmtNum(st.lastPrice, 4)}<span class="ccy">CHF</span></div>
          <div style="font-size:11px;color:var(--muted)">1 ${fx.currency} in CHF</div>
        </div>
        <div style="text-align:right">
          <div class="price-big ${pctClass(st.changePct)}">${fmtPct(st.changePct)}</div>
          <div style="font-size:11px;color:var(--muted)">Change</div>
        </div>
      </div>
      <div class="chart-wrap">
        <svg class="chart-svg" viewBox="0 0 460 155"></svg>
        <div class="tooltip"></div>
      </div>
      <div class="meta-row">
        <span>Start: ${fmtNum(st.startPrice, 4)} CHF</span>
        <span>Low / High: ${fmtNum(st.lowPrice, 4)} &ndash; ${fmtNum(st.highPrice, 4)}</span>
      </div>
    `;
    grid.appendChild(card);
    const wrap = card.querySelector(".chart-wrap");
    drawMiniChart(wrap.querySelector("svg"), wrap.querySelector(".tooltip"), wrap, fx, stWithChf, 4);
  });
}

function renderSleeves(rData) {
  const container = document.getElementById("sleevesContainer");
  container.innerHTML = "";

  PAYLOAD.sleeves.forEach(sleeve => {
    const sec = document.createElement("section");
    sec.className = "sleeve-sec";
    sec.innerHTML = `
      <div class="sleeve-head">
        <div>
          <span class="sleeve-title">${sleeve.label}</span>
          <span class="sleeve-sub">labeled ${sleeve.labeledWeight}% &middot; holdings ${sleeve.holdingsWeight}%</span>
        </div>
        <span style="font-size:13px;color:var(--muted)">${sleeve.note}</span>
      </div>
      <div class="grid" id="grid-${sleeve.id}"></div>
    `;
    container.appendChild(sec);

    const grid = sec.querySelector(`#grid-${sleeve.id}`);
    PAYLOAD.etfs.filter(e => e.sleeve === sleeve.id).forEach(etf => {
      const st = rData.etfs[etf.id];
      const card = document.createElement("div");
      card.className = "card"; card.style.padding = "16px";
      const fbText = st.fallbackUsed ? ` &middot; fallback for ${etf.requestedTicker}` : "";
      const nativeCcy = st.nativeCurrency || st.currency;
      const ccyNote = integrateChf && nativeCcy !== "CHF" ? ` (converted from ${nativeCcy})` : "";
      card.innerHTML = `
        <div class="etf-top">
          <div>
            <div class="etf-short"><span class="swatch" style="background:${etf.color};margin-right:6px"></span>${etf.shortName}</div>
            <div class="etf-name">${etf.name}</div>
            <div class="etf-ticker">${st.ticker}${fbText} &middot; ${st.pointCount || 0} obs</div>
          </div>
          <span class="badge">${etf.weight === 0 ? '0% info' : etf.weight + '%'}</span>
        </div>
        <div class="etf-metrics">
          <div>
            <div class="price-big">${fmtNum(st.lastPrice)}<span class="ccy">${st.currency}</span></div>
            <div style="font-size:11px;color:var(--muted)">Last (${st.field})${ccyNote}</div>
          </div>
          <div style="text-align:right">
            <div class="price-big ${pctClass(st.changePct)}">${fmtPct(st.changePct)}</div>
            <div style="font-size:11px;color:var(--muted)">Return (${st.currency})</div>
          </div>
        </div>
        <div class="chart-wrap">
          <svg class="chart-svg" viewBox="0 0 460 155"></svg>
          <div class="tooltip"></div>
        </div>
        <div class="meta-row">
          <span>Start: ${fmtNum(st.startPrice)} ${st.currency}</span>
          <span>Low / High: ${fmtNum(st.lowPrice)} &ndash; ${fmtNum(st.highPrice)}</span>
        </div>
      `;
      grid.appendChild(card);
      const wrap = card.querySelector(".chart-wrap");
      drawMiniChart(wrap.querySelector("svg"), wrap.querySelector(".tooltip"), wrap, etf, st);
    });
  });
}

// ==================== GLOBAL PORTFOLIO PERFORMANCE & DCA / FUTURE SIMULATOR ====================
const SIM_DEFAULT_START = "2026-10-08";
const SIM_DEFAULT_CAP = 50000;
let simState = {
  initialCapital: SIM_DEFAULT_CAP,
  startDate: SIM_DEFAULT_START,
  endDate: "",
  histBasis: "10y",
  model: "cagr",
  yMaxChf: null,
  yMinChf: null,
  dcaSign: 1,
  dcaAmount: 0,
  dcaFreq: "monthly",
  dcaStartDate: "2026-11-01",
  customCashFlows: [],
};
const hiddenSimSeries = new Set();

function getTodayDataDate() {
  const dts = (PAYLOAD.daily && PAYLOAD.daily.dates) || [];
  return dts.length > 0 ? dts[dts.length - 1] : new Date().toISOString().slice(0, 10);
}

function loadSimState() {
  const todayStr = getTodayDataDate();
  simState.endDate = todayStr;
  try {
    const raw = localStorage.getItem("portefeuille_sim_v1");
    if (raw) {
      const p = JSON.parse(raw);
      if (typeof p.initialCapital === "number" && p.initialCapital > 0) simState.initialCapital = p.initialCapital;
      if (typeof p.startDate === "string" && /^\\d{4}-\\d{2}-\\d{2}$/.test(p.startDate)) simState.startDate = p.startDate;
      if (typeof p.endDate === "string" && /^\\d{4}-\\d{2}-\\d{2}$/.test(p.endDate) && p.endDate >= todayStr) {
        simState.endDate = p.endDate;
      }
      if (p.histBasis) simState.histBasis = p.histBasis;
      if (p.model) simState.model = p.model;
      if (typeof p.yMaxChf === "number" && p.yMaxChf > 0) simState.yMaxChf = p.yMaxChf;
      if (typeof p.yMinChf === "number" && p.yMinChf >= 0) simState.yMinChf = p.yMinChf;
      if (p.dcaSign === 1 || p.dcaSign === -1) simState.dcaSign = p.dcaSign;
      if (typeof p.dcaAmount === "number" && p.dcaAmount >= 0) simState.dcaAmount = p.dcaAmount;
      if (p.dcaFreq) simState.dcaFreq = p.dcaFreq;
      if (p.dcaStartDate) simState.dcaStartDate = p.dcaStartDate;
      if (Array.isArray(p.customCashFlows)) simState.customCashFlows = p.customCashFlows;
    }
  } catch (e) {}
  if (!simState.endDate || simState.endDate < todayStr) {
    simState.endDate = todayStr;
    saveSimState();
  }
}

function saveSimState() {
  try { localStorage.setItem("portefeuille_sim_v1", JSON.stringify(simState)); } catch (e) {}
}

function addMonthsIso(isoStr, months) {
  const [y, m, d] = isoStr.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1 + months, d));
  return dt.toISOString().slice(0, 10);
}

function addDaysIso(isoStr, days) {
  const [y, m, d] = isoStr.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

function generateRecurringDates(firstDateStr, freq, minDateStr, maxDateStr) {
  const out = [];
  if (!firstDateStr || freq === "none" || firstDateStr > maxDateStr) return out;
  let cur = firstDateStr; let step = 0;
  while (cur <= maxDateStr && step < 4000) {
    if (cur > minDateStr && cur <= maxDateStr) { out.push(cur); }
    step += 1;
    if (freq === "once") break;
    else if (freq === "weekly") cur = addDaysIso(firstDateStr, step * 7);
    else if (freq === "biweekly") cur = addDaysIso(firstDateStr, step * 14);
    else if (freq === "monthly") cur = addMonthsIso(firstDateStr, step);
    else if (freq === "quarterly") cur = addMonthsIso(firstDateStr, step * 3);
    else if (freq === "yearly") cur = addMonthsIso(firstDateStr, step * 12);
    else break;
  }
  return out;
}

function computeHistoricalChfDailyReturns() {
  const daily = PAYLOAD.daily;
  if (!daily || !daily.dates || daily.dates.length === 0) return { dates: [], returns: [] };
  
  // Guard against missing pre-inception dates across core portfolio holdings
  const allDates = daily.dates;
  let validStartIdx = 0;
  for (let i = 0; i < allDates.length; i++) {
    let allPresent = true;
    for (const etf of PAYLOAD.etfs) {
      if (etf.weight > 0) {
        const p = daily.prices[etf.id] ? daily.prices[etf.id][i] : null;
        if (p == null || p <= 0) {
          allPresent = false;
          break;
        }
      }
    }
    if (allPresent) {
      validStartIdx = i;
      break;
    }
  }

  const validDates = allDates.slice(validStartIdx);
  const validReturns = (daily.portfolioChfDailyReturns || []).slice(validStartIdx);
  return { dates: validDates, returns: validReturns };
}

function getHistoricalBasisStats(allDates, allReturns, basisId) {
  const N = allDates.length;
  const baselineStats = { meanDaily: 0.00025, stdDaily: 0.0075, cagr: 6.8, annVol: 12.0, sampleReturns: [0.00025], count: 0 };
  if (N < 20) return baselineStats;

  let lookbackDays = N - 1;
  if (basisId === "1y") lookbackDays = Math.min(N - 1, 252);
  else if (basisId === "5y") lookbackDays = Math.min(N - 1, 252 * 5);
  else if (basisId === "10y") lookbackDays = Math.min(N - 1, 252 * 10);
  else if (basisId === "15y") lookbackDays = Math.min(N - 1, 252 * 15);
  else if (basisId === "25y") lookbackDays = Math.min(N - 1, 252 * 25);
  else if (basisId === "max") lookbackDays = N - 1;

  const slice = allReturns.slice(Math.max(1, N - lookbackDays));
  const M = slice.length;
  let sum = 0; let comp = 1.0;
  for (let i = 0; i < M; i++) { sum += slice[i]; comp *= (1.0 + slice[i]); }
  const meanDaily = sum / M;
  let varSum = 0;
  for (let i = 0; i < M; i++) { const d = slice[i] - meanDaily; varSum += d * d; }
  const stdDaily = Math.sqrt(varSum / Math.max(1, M - 1));
  const years = M / 252.0;

  let cagr = years > 0.5 && comp > 0 ? (Math.pow(comp, 1.0 / years) - 1.0) * 100.0 : 6.8;
  if (isNaN(cagr) || cagr < 4.0) cagr = 6.8;

  const annVol = Math.min(18.0, Math.max(8.5, stdDaily * Math.sqrt(252.0) * 100.0));
  return { meanDaily, stdDaily, cagr, annVol, sampleReturns: slice, count: M };
}

function buildGlobalSimulation() {
  const { dates: allHistDates, returns: allHistReturns } = computeHistoricalChfDailyReturns();
  const todayStr = getTodayDataDate();
  const initCap = Math.max(100, Number(simState.initialCapital) || SIM_DEFAULT_CAP);
  let startStr = simState.startDate || SIM_DEFAULT_START;
  let endStr = simState.endDate || todayStr;
  if (endStr <= startStr) { endStr = startStr < todayStr ? todayStr : addMonthsIso(startStr, 3); }

  const basisStats = getHistoricalBasisStats(allHistDates, allHistReturns, simState.histBasis);

  const timeline = [];
  let startIdx = allHistDates.findIndex(d => d >= startStr);
  if (startIdx === -1) startIdx = allHistDates.length - 1;

  if (startStr <= todayStr && startIdx >= 0) {
    for (let i = startIdx; i < allHistDates.length; i++) {
      const d = allHistDates[i];
      if (d > endStr) break;
      timeline.push({ date: d, isFuture: false, ret: i === startIdx ? 0.0 : allHistReturns[i] });
    }
  }

  const lastHistDate = timeline.length > 0 ? timeline[timeline.length - 1].date : startStr;
  if (endStr > lastHistDate) {
    if (timeline.length === 0) { timeline.push({ date: startStr, isFuture: true, ret: 0.0 }); }
    const sample = basisStats.sampleReturns;
    const S = sample.length;
    const cagrDaily = Math.pow(1.0 + basisStats.cagr / 100.0, 1.0 / 252.0) - 1.0;
    const annVolDecimal = basisStats.annVol / 100.0;
    const dailyVol = annVolDecimal / Math.sqrt(252.0);

    let curDate = timeline[timeline.length - 1].date;
    let fStep = 0;
    
    // Seed variation based on chosen basis to ensure every basis produces its distinct path
    const basisSeedOffset = { "1y": 101, "5y": 307, "10y": 509, "15y": 719, "25y": 911, "max": 1337 }[simState.histBasis] || 1337;
    let rngSeed = basisSeedOffset;

    while (curDate < endStr && fStep < 10000) {
      curDate = addDaysIso(curDate, 1);
      if (curDate > endStr) break;
      const [yy, mm, dd] = curDate.split("-").map(Number);
      const dow = new Date(Date.UTC(yy, mm - 1, dd)).getUTCDay();
      if (dow === 0 || dow === 6) continue;

      let r = cagrDaily;
      if (simState.model === "replay" && S > 5) {
        const rawR = sample[(fStep * 19 + basisSeedOffset) % S];
        const shock = (rawR - basisStats.meanDaily);
        // Correct for log-normal variance drag so the compounded expectation matches cagrDaily
        r = Math.exp(Math.log(1.0 + cagrDaily) + shock * 0.45) - 1.0;
      } else if (simState.model === "cholesky") {
        rngSeed = (rngSeed * 9301 + 49297) % 233280;
        const u1 = Math.max(1e-7, rngSeed / 233280.0);
        rngSeed = (rngSeed * 9301 + 49297) % 233280;
        const u2 = rngSeed / 233280.0;
        const z = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
        r = cagrDaily - 0.5 * dailyVol * dailyVol + dailyVol * z;
      }
      timeline.push({ date: curDate, isFuture: true, ret: r, futureStep: fStep + 1 });
      fStep += 1;
    }
  }

  if (timeline.length === 0) { timeline.push({ date: startStr, isFuture: false, ret: 0.0 }); }

  const firstTimelineDate = timeline[0].date;
  const lastTimelineDate = timeline[timeline.length - 1].date;

  const cfEvents = [];
  if (simState.dcaFreq !== "none" && simState.dcaAmount > 0) {
    const signedAmt = simState.dcaSign * Math.abs(simState.dcaAmount);
    const dcaDates = generateRecurringDates(simState.dcaStartDate, simState.dcaFreq, firstTimelineDate, lastTimelineDate);
    dcaDates.forEach(dt => cfEvents.push({ date: dt, amount: signedAmt, label: "Regular DCA" }));
  }

  (simState.customCashFlows || []).forEach(rule => {
    if (!rule || !rule.date || !rule.amount) return;
    const signedAmt = (rule.sign === -1 ? -1 : 1) * Math.abs(Number(rule.amount));
    const freq = rule.freq || "once";
    if (freq === "once") {
      if (rule.date > firstTimelineDate && rule.date <= lastTimelineDate) {
        cfEvents.push({ date: rule.date, amount: signedAmt, label: "Custom" });
      }
    } else {
      const rDates = generateRecurringDates(rule.date, freq, firstTimelineDate, lastTimelineDate);
      rDates.forEach(dt => cfEvents.push({ date: dt, amount: signedAmt, label: "Custom" }));
    }
  });

  const cfByIdx = new Array(timeline.length).fill(0);
  const cfMarkers = [];
  cfEvents.forEach(ev => {
    const idx = timeline.findIndex(pt => pt.date >= ev.date);
    if (idx > 0) { cfByIdx[idx] += ev.amount; }
  });

  const M = timeline.length;
  let portVal = initCap; let netInvested = initCap;
  let totalAdded = 0; let totalRemoved = 0; let todayIdx = 0;

  const seriesDates = new Array(M);
  const seriesPortChf = new Array(M);
  const seriesHistChf = new Array(M).fill(null);
  const seriesSimChf = new Array(M).fill(null);
  const seriesInvestedChf = new Array(M);
  const seriesHighChf = new Array(M).fill(null);
  const seriesLowChf = new Array(M).fill(null);
  const seriesNetGainPct = new Array(M);
  const seriesTotalGrowthPct = new Array(M);
  const seriesCashFlowAtDay = new Array(M).fill(0);

  const cagrDaily = Math.pow(1.0 + basisStats.cagr / 100.0, 1.0 / 252.0) - 1.0;
  let coneCenter = initCap;

  for (let i = 0; i < M; i++) {
    const pt = timeline[i];
    if (i > 0) { portVal = Math.max(0, portVal * (1.0 + pt.ret)); }
    const cf = cfByIdx[i];
    if (cf !== 0) {
      portVal = Math.max(0, portVal + cf);
      netInvested += cf;
      if (cf > 0) totalAdded += cf; else totalRemoved += Math.abs(cf);
      seriesCashFlowAtDay[i] = cf;
      cfMarkers.push({ idx: i, date: pt.date, amount: cf, portChf: portVal, investedChf: netInvested });
    }

    seriesDates[i] = pt.date;
    seriesPortChf[i] = Number(portVal.toFixed(2));
    seriesInvestedChf[i] = Number(netInvested.toFixed(2));

    const denom = netInvested > 0 ? netInvested : initCap;
    seriesNetGainPct[i] = Number((((portVal - netInvested) / denom) * 100.0).toFixed(2));
    seriesTotalGrowthPct[i] = Number((((portVal / initCap) - 1.0) * 100.0).toFixed(2));

    if (!pt.isFuture) {
      seriesHistChf[i] = seriesPortChf[i];
      todayIdx = i;
      coneCenter = portVal;
    } else {
      if (i === todayIdx + 1 && todayIdx >= 0) {
        seriesSimChf[todayIdx] = seriesPortChf[todayIdx];
        seriesHighChf[todayIdx] = seriesPortChf[todayIdx];
        seriesLowChf[todayIdx] = seriesPortChf[todayIdx];
      }
      coneCenter = Math.max(0, coneCenter * (1.0 + cagrDaily) + cf);
      seriesSimChf[i] = seriesPortChf[i];
      const fSteps = pt.futureStep || (i - todayIdx);
      const sigmaT = basisStats.stdDaily * Math.sqrt(fSteps);
      const spread = 1.28 * sigmaT;
      const hi = Math.max(portVal, coneCenter) * Math.exp(spread);
      const lo = Math.min(portVal, coneCenter) * Math.exp(-spread);
      seriesHighChf[i] = Number(hi.toFixed(2));
      seriesLowChf[i] = Number(Math.max(0, lo).toFixed(2));
    }
  }

  const extraSet = new Set([todayIdx]);
  cfMarkers.forEach(m => extraSet.add(m.idx));
  const pickIdxs = sampleIndices(M, 220, extraSet);

  const finalPortChf = seriesPortChf[M - 1];
  const finalInvestedChf = seriesInvestedChf[M - 1];
  const netProfitChf = finalPortChf - finalInvestedChf;
  const netProfitPct = finalInvestedChf > 0 ? (netProfitChf / finalInvestedChf) * 100.0 : 0.0;
  const todayPortChf = seriesPortChf[todayIdx];
  const todayNetPct = seriesNetGainPct[todayIdx];
  const futureDaysCount = M - 1 - todayIdx;

  return {
    initCap, startDate: firstTimelineDate, todayDate: timeline[todayIdx] ? timeline[todayIdx].date : todayStr,
    endDate: lastTimelineDate, hasFuture: futureDaysCount > 0, totalTradingDays: M, futureTradingDays: Math.max(0, futureDaysCount),
    basisStats, todayPortChf, todayNetPct, finalPortChf, finalInvestedChf, totalAdded, totalRemoved,
    netProfitChf, netProfitPct, cfEventsCount: cfEvents.length, pickIdxs,
    dates: pickIdxs.map(i => seriesDates[i]), portChf: pickIdxs.map(i => seriesPortChf[i]),
    histChf: pickIdxs.map(i => seriesHistChf[i]), simChf: pickIdxs.map(i => seriesSimChf[i]),
    investedChf: pickIdxs.map(i => seriesInvestedChf[i]), highChf: pickIdxs.map(i => seriesHighChf[i]),
    lowChf: pickIdxs.map(i => seriesLowChf[i]), netGainPct: pickIdxs.map(i => seriesNetGainPct[i]),
    totalGrowthPct: pickIdxs.map(i => seriesTotalGrowthPct[i]), cfAtDay: pickIdxs.map(i => seriesCashFlowAtDay[i]),
    todaySampleIdx: pickIdxs.indexOf(todayIdx),
  };
}

function fmtChf(v, digits = 0) {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("de-CH", { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " CHF";
}

function renderGlobalSimulator() {
  const sim = buildGlobalSimulation();

  document.getElementById("simInitCapInput").value = simState.initialCapital;
  document.getElementById("simStartInput").value = simState.startDate;
  document.getElementById("simEndInput").value = simState.endDate;
  document.getElementById("simHistBasisSelect").value = simState.histBasis;
  document.getElementById("simModelSelect").value = simState.model;
  const yMaxEl = document.getElementById("simYMaxInput");
  if (document.activeElement !== yMaxEl) {
    yMaxEl.value = (simState.yMaxChf != null && Number.isFinite(simState.yMaxChf) && simState.yMaxChf > 0) ? String(simState.yMaxChf) : "";
  }
  const yMinEl = document.getElementById("simYMinInput");
  if (document.activeElement !== yMinEl) {
    yMinEl.value = (simState.yMinChf != null && Number.isFinite(simState.yMinChf) && simState.yMinChf >= 0) ? String(simState.yMinChf) : "";
  }
  document.querySelectorAll("#simYZoomBtns button").forEach(btn => {
    const ym = btn.getAttribute("data-ymax");
    const isAct = (ym === "auto" && (simState.yMaxChf == null || simState.yMaxChf <= 0)) ||
                  (ym !== "auto" && Number(ym) === simState.yMaxChf);
    btn.style.borderColor = isAct ? "rgba(56,189,248,0.65)" : "";
    btn.style.color = isAct ? "var(--accent)" : "";
  });
  document.getElementById("simDcaSignSelect").value = String(simState.dcaSign);
  document.getElementById("simDcaAmtInput").value = simState.dcaAmount;
  document.getElementById("simDcaFreqSelect").value = simState.dcaFreq;
  document.getElementById("simDcaStartInput").value = simState.dcaStartDate;

  const dcaBadge = document.getElementById("simDcaBadge");
  if (simState.dcaFreq !== "none" && simState.dcaAmount > 0) {
    const sgn = simState.dcaSign > 0 ? "+" : "−";
    dcaBadge.textContent = `${sgn}${fmtNum(simState.dcaAmount, 0)} CHF / ${simState.dcaFreq}`;
    dcaBadge.className = "badge badge-accent";
  } else {
    dcaBadge.textContent = "Off"; dcaBadge.className = "badge";
  }

  const cfListEl = document.getElementById("simCfList");
  cfListEl.innerHTML = "";
  if (!simState.customCashFlows || simState.customCashFlows.length === 0) {
    cfListEl.innerHTML = `<div style="color:var(--dim);font-size:11px;font-family:var(--font-mono)">No custom date rules added yet.</div>`;
  } else {
    simState.customCashFlows.forEach((rule, idx) => {
      const row = document.createElement("div");
      row.className = "sim-cf-item";
      const isAdd = rule.sign !== -1;
      row.innerHTML = `
        <span><strong class="${isAdd ? 'pos' : 'neg'}">${isAdd ? '+' : '&minus;'}${fmtNum(rule.amount, 0)} CHF</strong> on ${rule.date} <span style="color:var(--muted)">(${rule.freq})</span></span>
        <button type="button" class="btn-sm" data-rm-cf="${idx}" style="padding:1px 6px;font-size:10px;">Remove</button>
      `;
      row.querySelector("button").onclick = () => {
        simState.customCashFlows.splice(idx, 1);
        saveSimState(); renderGlobalSimulator();
      };
      cfListEl.appendChild(row);
    });
  }

  const hlVal = document.getElementById("simHeadlineVal");
  hlVal.textContent = `${fmtChf(sim.finalPortChf, 0)} (${fmtPct(sim.netProfitPct)})`;
  hlVal.className = "kpi-val " + pctClass(sim.netProfitChf);
  document.getElementById("simHeadlineSub").textContent = sim.hasFuture
    ? `Projected on ${sim.endDate} · Net P&L: ${sim.netProfitChf >= 0 ? '+' : ''}${fmtChf(sim.netProfitChf, 0)}`
    : `As of Today (${sim.endDate}) · Net P&L: ${sim.netProfitChf >= 0 ? '+' : ''}${fmtChf(sim.netProfitChf, 0)}`;

  const strip = document.getElementById("simKpiStrip");
  strip.innerHTML = `
    <div class="sim-kpi-box">
      <div class="sim-kpi-lbl">Initial Capital (${sim.startDate})</div>
      <div class="sim-kpi-val">${fmtChf(sim.initCap, 0)}</div>
    </div>
    <div class="sim-kpi-box">
      <div class="sim-kpi-lbl">Value Today (${sim.todayDate})</div>
      <div class="sim-kpi-val ${pctClass(sim.todayNetPct)}">${fmtChf(sim.todayPortChf, 0)} <span style="font-size:12px">(${fmtPct(sim.todayNetPct)})</span></div>
    </div>
    <div class="sim-kpi-box">
      <div class="sim-kpi-lbl">Net DCA Added / Removed</div>
      <div class="sim-kpi-val">${sim.totalAdded - sim.totalRemoved >= 0 ? '+' : ''}${fmtChf(sim.totalAdded - sim.totalRemoved, 0)}</div>
    </div>
    <div class="sim-kpi-box">
      <div class="sim-kpi-lbl">Total Net Invested</div>
      <div class="sim-kpi-val">${fmtChf(sim.finalInvestedChf, 0)}</div>
    </div>
    <div class="sim-kpi-box">
      <div class="sim-kpi-lbl">${sim.hasFuture ? 'Simulated Value (' + sim.endDate + ')' : 'Final Value'}</div>
      <div class="sim-kpi-val ${pctClass(sim.netProfitChf)}">${fmtChf(sim.finalPortChf, 0)}</div>
    </div>
    <div class="sim-kpi-box">
      <div class="sim-kpi-lbl">Net Gain / Loss</div>
      <div class="sim-kpi-val ${pctClass(sim.netProfitChf)}">${sim.netProfitChf >= 0 ? '+' : ''}${fmtChf(sim.netProfitChf, 0)} (${fmtPct(sim.netProfitPct)})</div>
    </div>
  `;

  const yZoomText = (simState.yMaxChf != null && simState.yMaxChf > 0) ? `Y-Axis Zoom Active` : "Y-Axis: Auto-fit";
  document.getElementById("simBadges").innerHTML = `
    <span class="badge badge-accent">0% Baseline: ${fmtChf(sim.initCap, 0)} on ${sim.startDate}</span>
    <span class="badge ${sim.hasFuture ? 'badge-accent' : ''}">End Date: ${sim.endDate}${sim.hasFuture ? ' (' + sim.futureTradingDays + ' future trading days)' : ''}</span>
    <span class="badge">Historical Basis (${simState.histBasis.toUpperCase()}): CAGR ${fmtPct(sim.basisStats.cagr)} p.a. &middot; Vol ${sim.basisStats.annVol.toFixed(2)}% p.a.</span>
    <span class="badge">${yZoomText} (Seeded &amp; Stable)</span>
  `;

  const svg = document.getElementById("simSvg");
  const tip = document.getElementById("simTip");
  const W = 1100, H = 350, padL = 96, padR = 76, padT = 18, padB = 32;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const T = sim.dates; const N = T.length;
  if (N < 2) { svg.innerHTML = ""; return; }

  let minChf = sim.initCap, maxChf = sim.initCap;
  const considerVal = v => {
    if (v != null && Number.isFinite(v)) { if (v < minChf) minChf = v; if (v > maxChf) maxChf = v; }
  };
  if (!hiddenSimSeries.has("hist")) sim.histChf.forEach(considerVal);
  if (!hiddenSimSeries.has("sim")) sim.simChf.forEach(considerVal);
  if (!hiddenSimSeries.has("invested")) sim.investedChf.forEach(considerVal);
  if (sim.hasFuture && !hiddenSimSeries.has("cone")) {
    sim.highChf.forEach(considerVal); sim.lowChf.forEach(considerVal);
  }
  if (maxChf === minChf) { maxChf += sim.initCap * 0.02; minChf = Math.max(0, minChf - sim.initCap * 0.02); }
  const span = maxChf - minChf; minChf = Math.max(0, minChf - span * 0.08); maxChf += span * 0.08;

  if (simState.yMinChf != null && Number.isFinite(simState.yMinChf) && simState.yMinChf >= 0) { minChf = simState.yMinChf; }
  if (simState.yMaxChf != null && Number.isFinite(simState.yMaxChf) && simState.yMaxChf > 0) {
    maxChf = simState.yMaxChf;
    if (minChf >= maxChf) { minChf = Math.max(0, maxChf * 0.85); }
  }
  if (maxChf <= minChf) { maxChf = minChf + 1000; }

  const xAt = i => padL + (i / (N - 1)) * plotW;
  const yAt = v => padT + plotH - ((v - minChf) / (maxChf - minChf)) * plotH;

  let html = `<defs><clipPath id="simPlotClip"><rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}"/></clipPath></defs>`;
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const valChf = minChf + (i / ticks) * (maxChf - minChf);
    const valPct = ((valChf / sim.initCap) - 1.0) * 100.0;
    const y = yAt(valChf);
    html += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(255,255,255,0.07)" stroke-dasharray="3 3"/>`;
    html += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" fill="#94a3b8" font-size="11" font-family="monospace">${fmtNum(valChf, 0)} CHF</text>`;
    html += `<text x="${W - padR + 8}" y="${y + 4}" text-anchor="start" fill="#64748b" font-size="11" font-family="monospace">${valPct > 0 ? "+" : ""}${valPct.toFixed(1)}%</text>`;
  }

  if (minChf <= sim.initCap && maxChf >= sim.initCap) {
    const y0 = yAt(sim.initCap);
    html += `<line x1="${padL}" y1="${y0}" x2="${W - padR}" y2="${y0}" stroke="rgba(255,255,255,0.28)" stroke-width="1" stroke-dasharray="4 4"/>`;
  }

  if (sim.hasFuture && sim.todaySampleIdx >= 0 && sim.todaySampleIdx < N - 1) {
    const tx = xAt(sim.todaySampleIdx);
    html += `<line x1="${tx}" y1="${padT}" x2="${tx}" y2="${padT + plotH}" stroke="rgba(56,189,248,0.45)" stroke-width="1.3" stroke-dasharray="4 4"/>`;
    html += `<text x="${tx + 5}" y="${padT + 12}" fill="#38bdf8" font-size="10" font-family="monospace">Today (${sim.todayDate})</text>`;
  }

  const xSteps = Math.min(6, N - 1);
  for (let i = 0; i <= xSteps; i++) {
    const idx = Math.round((i / xSteps) * (N - 1));
    const x = xAt(idx);
    html += `<text x="${x}" y="${H - 7}" text-anchor="middle" fill="#64748b" font-size="11" font-family="monospace">${T[idx]}</text>`;
  }

  html += `<g clip-path="url(#simPlotClip)">`;
  if (sim.hasFuture && !hiddenSimSeries.has("cone")) {
    const conePts = [];
    for (let i = 0; i < N; i++) {
      if (sim.highChf[i] != null && sim.lowChf[i] != null) { conePts.push({ i, hi: sim.highChf[i], lo: sim.lowChf[i] }); }
    }
    if (conePts.length >= 2) {
      let areaD = "";
      conePts.forEach((p, k) => { areaD += (k === 0 ? "M" : "L") + xAt(p.i).toFixed(1) + "," + yAt(p.hi).toFixed(1); });
      for (let k = conePts.length - 1; k >= 0; k--) {
        const p = conePts[k]; areaD += "L" + xAt(p.i).toFixed(1) + "," + yAt(p.lo).toFixed(1);
      }
      areaD += "Z";
      html += `<path d="${areaD}" fill="#38bdf8" fill-opacity="0.11" stroke="rgba(56,189,248,0.25)" stroke-width="1"/>`;
    }
  }

  if (!hiddenSimSeries.has("invested")) {
    let dInv = "";
    sim.investedChf.forEach((v, i) => {
      if (v == null || !Number.isFinite(v)) return;
      dInv += (dInv === "" ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
    });
    if (dInv) { html += `<path d="${dInv}" fill="none" stroke="#94a3b8" stroke-width="1.8" stroke-dasharray="5 4"/>`; }
  }

  if (!hiddenSimSeries.has("hist")) {
    let dHist = "";
    sim.histChf.forEach((v, i) => {
      if (v == null || !Number.isFinite(v)) return;
      dHist += (dHist === "" ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
    });
    if (dHist) { html += `<path d="${dHist}" fill="none" stroke="#34d399" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>`; }
  }

  if (sim.hasFuture && !hiddenSimSeries.has("sim")) {
    let dSim = "";
    sim.simChf.forEach((v, i) => {
      if (v == null || !Number.isFinite(v)) return;
      dSim += (dSim === "" ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
    });
    if (dSim) { html += `<path d="${dSim}" fill="none" stroke="#38bdf8" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>`; }
  }

  sim.cfAtDay.forEach((cf, i) => {
    if (cf !== 0 && sim.portChf[i] != null) {
      const cx = xAt(i).toFixed(1); const cy = yAt(sim.portChf[i]).toFixed(1);
      const col = cf > 0 ? "#34d399" : "#fb7185";
      html += `<circle cx="${cx}" cy="${cy}" r="4" fill="${col}" stroke="#0b0d12" stroke-width="1.5"/>`;
    }
  });
  html += `</g>`;

  html += `<line id="simCursor" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="rgba(255,255,255,0.4)" stroke-width="1" visibility="hidden"/>`;
  html += `<rect id="simHit" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor:crosshair"/>`;
  svg.innerHTML = html;

  const hit = document.getElementById("simHit");
  const cur = document.getElementById("simCursor");
  hit.onmousemove = (ev) => {
    const rect = svg.getBoundingClientRect();
    const relX = ((ev.clientX - rect.left) / rect.width) * W;
    const idx = Math.max(0, Math.min(N - 1, Math.round(((relX - padL) / plotW) * (N - 1))));
    const cx = xAt(idx);
    cur.setAttribute("x1", cx); cur.setAttribute("x2", cx); cur.setAttribute("visibility", "visible");

    const pChf = sim.portChf[idx]; const invChf = sim.investedChf[idx];
    const gainChf = pChf - invChf; const netPct = sim.netGainPct[idx]; const totPct = sim.totalGrowthPct[idx];
    const isFut = sim.simChf[idx] != null && sim.histChf[idx] == null;
    const cfDay = sim.cfAtDay[idx];

    let rows = `<div class="tt-date">${T[idx]} ${isFut ? '(Projected)' : '(Historical)'}</div>`;
    rows += `<div class="tt-row"><span style="color:${isFut ? '#38bdf8' : '#34d399'}">&#9632; Portfolio Value</span><span>${fmtChf(pChf, 2)}</span></div>`;
    rows += `<div class="tt-row"><span style="color:#94a3b8">&#9632; Net Invested</span><span>${fmtChf(invChf, 0)}</span></div>`;
    rows += `<div class="tt-row"><span>Net P&amp;L</span><span class="${pctClass(gainChf)}">${gainChf >= 0 ? '+' : ''}${fmtChf(gainChf, 2)} (${fmtPct(netPct)})</span></div>`;
    rows += `<div class="tt-row"><span>Change vs Initial</span><span class="${pctClass(totPct)}">${fmtPct(totPct)}</span></div>`;
    if (isFut && sim.highChf[idx] != null && sim.lowChf[idx] != null) {
      rows += `<div class="tt-row"><span style="color:#38bdf8">10%–90% Band</span><span>${fmtNum(sim.lowChf[idx], 0)} – ${fmtNum(sim.highChf[idx], 0)} CHF</span></div>`;
    }
    if (cfDay !== 0) {
      rows += `<div class="tt-row"><span class="${cfDay > 0 ? 'pos' : 'neg'}">Cash Flow</span><span class="${cfDay > 0 ? 'pos' : 'neg'}">${cfDay > 0 ? '+' : ''}${fmtChf(cfDay, 0)}</span></div>`;
    }
    tip.innerHTML = rows; tip.style.display = "block";
    const wrapW = document.getElementById("simChartWrap").clientWidth;
    const leftPx = (cx / W) * wrapW;
    tip.style.left = (leftPx > wrapW - 265 ? leftPx - 245 : leftPx + 14) + "px";
    tip.style.top = "12px";
  };
  hit.onmouseleave = () => { cur.setAttribute("visibility", "hidden"); tip.style.display = "none"; };

  const leg = document.getElementById("simLegend");
  leg.innerHTML = "";
  const items = [
    { id: "hist", label: "Historical Portfolio Value", color: "#34d399" },
    { id: "sim", label: "Projected Future Portfolio (Seeded)", color: "#38bdf8" },
    { id: "invested", label: "Net Invested Capital", color: "#94a3b8" },
    { id: "cone", label: "10%–90% Volatility Band", color: "rgba(56,189,248,0.45)" },
  ];
  items.forEach(it => {
    const btn = document.createElement("button");
    btn.className = "legend-item" + (hiddenSimSeries.has(it.id) ? " off" : "");
    btn.innerHTML = `<span class="swatch" style="background:${it.color}"></span><span>${it.label}</span>`;
    btn.onclick = () => {
      if (hiddenSimSeries.has(it.id)) hiddenSimSeries.delete(it.id); else hiddenSimSeries.add(it.id);
      renderGlobalSimulator();
    };
    leg.appendChild(btn);
  });
}

function wireSimulatorEvents() {
  document.getElementById("simInitCapInput").onchange = (e) => {
    simState.initialCapital = Math.max(100, Number(e.target.value) || SIM_DEFAULT_CAP);
    saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simStartInput").onchange = (e) => {
    if (e.target.value) { simState.startDate = e.target.value; saveSimState(); renderGlobalSimulator(); }
  };
  document.getElementById("simEndInput").onchange = (e) => {
    if (e.target.value) { simState.endDate = e.target.value; saveSimState(); renderGlobalSimulator(); }
  };
  document.getElementById("simResetDatesBtn").onclick = () => {
    simState.initialCapital = SIM_DEFAULT_CAP; simState.startDate = SIM_DEFAULT_START;
    simState.endDate = getTodayDataDate(); simState.yMaxChf = null; simState.yMinChf = null;
    saveSimState(); renderGlobalSimulator();
  };
  function parseChfInput(raw) {
    if (raw == null) return null;
    const s = String(raw).trim().replace(/['’_,\\s]/g, "").toLowerCase();
    if (!s) return null;
    let mult = 1; let numStr = s;
    if (s.endsWith("k")) { mult = 1000; numStr = s.slice(0, -1); }
    else if (s.endsWith("m")) { mult = 1000000; numStr = s.slice(0, -1); }
    const val = Number(numStr) * mult;
    return Number.isFinite(val) ? val : null;
  }
  document.getElementById("simYMaxInput").onchange = (e) => {
    const val = parseChfInput(e.target.value);
    simState.yMaxChf = (val != null && val > 0) ? val : null;
    saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simYMinInput").onchange = (e) => {
    const val = parseChfInput(e.target.value);
    simState.yMinChf = (val != null && val >= 0) ? val : null;
    saveSimState(); renderGlobalSimulator();
  };
  document.querySelectorAll("#simYZoomBtns button").forEach(btn => {
    btn.onclick = () => {
      const ym = btn.getAttribute("data-ymax");
      if (ym === "auto") { simState.yMaxChf = null; simState.yMinChf = null; }
      else { simState.yMaxChf = Number(ym) || null; }
      document.getElementById("simYMaxInput").value = simState.yMaxChf != null ? String(simState.yMaxChf) : "";
      if (ym === "auto") document.getElementById("simYMinInput").value = "";
      saveSimState(); renderGlobalSimulator();
    };
  });
  document.querySelectorAll("#simHorizonBtns button").forEach(btn => {
    btn.onclick = () => {
      const h = btn.getAttribute("data-horizon");
      const todayStr = getTodayDataDate();
      if (h === "today") simState.endDate = todayStr;
      else if (h === "3m") simState.endDate = addMonthsIso(todayStr, 3);
      else if (h === "6m") simState.endDate = addMonthsIso(todayStr, 6);
      else if (h === "1y") simState.endDate = addMonthsIso(todayStr, 12);
      else if (h === "3y") simState.endDate = addMonthsIso(todayStr, 36);
      else if (h === "5y") simState.endDate = addMonthsIso(todayStr, 60);
      else if (h === "10y") simState.endDate = addMonthsIso(todayStr, 120);
      else if (h === "15y") simState.endDate = addMonthsIso(todayStr, 180);
      else if (h === "25y") simState.endDate = addMonthsIso(todayStr, 300);
      saveSimState(); renderGlobalSimulator();
    };
  });
  document.getElementById("simHistBasisSelect").onchange = (e) => {
    simState.histBasis = e.target.value; saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simModelSelect").onchange = (e) => {
    simState.model = e.target.value; saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simDcaSignSelect").onchange = (e) => {
    simState.dcaSign = Number(e.target.value) === -1 ? -1 : 1; saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simDcaAmtInput").onchange = (e) => {
    simState.dcaAmount = Math.max(0, Number(e.target.value) || 0); saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simDcaFreqSelect").onchange = (e) => {
    simState.dcaFreq = e.target.value; saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simDcaStartInput").onchange = (e) => {
    if (e.target.value) { simState.dcaStartDate = e.target.value; saveSimState(); renderGlobalSimulator(); }
  };
  document.getElementById("simPresetDca1kBtn").onclick = () => {
    simState.dcaSign = 1; simState.dcaAmount = 1000; simState.dcaFreq = "monthly"; saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simPresetDca2kBtn").onclick = () => {
    simState.dcaSign = 1; simState.dcaAmount = 2000; simState.dcaFreq = "monthly"; saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simPresetWithdrawBtn").onclick = () => {
    simState.dcaSign = -1; simState.dcaAmount = 1000; simState.dcaFreq = "monthly"; saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simPresetDcaOffBtn").onclick = () => {
    simState.dcaAmount = 0; simState.dcaFreq = "none"; saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simAddCfBtn").onclick = () => {
    const dt = document.getElementById("simCfDateInput").value;
    const sign = Number(document.getElementById("simCfSignSelect").value) === -1 ? -1 : 1;
    const amt = Math.max(1, Number(document.getElementById("simCfAmtInput").value) || 0);
    const freq = document.getElementById("simCfFreqSelect").value || "once";
    if (!dt || amt <= 0) return;
    simState.customCashFlows.push({ date: dt, sign, amount: amt, freq });
    saveSimState(); renderGlobalSimulator();
  };
  document.getElementById("simClearCustomCfBtn").onclick = () => {
    simState.customCashFlows = []; saveSimState(); renderGlobalSimulator();
  };
}

function getActiveRangeData() {
  if (currentMode === "custom" && customStartDate) { return buildCustomRangeData(customStartDate); }
  return PAYLOAD.ranges[currentRange] || PAYLOAD.ranges["1y"];
}

function updateStamps() {
  document.getElementById("lastUpdatedLbl").textContent = `Updated ${PAYLOAD.fetchedAt}`;
  const modeDesc = integrateChf ? "Integrated in CHF (FX-converted)" : "Local listing currencies";
  document.getElementById("footerStamp").textContent = `TradingView &amp; Yahoo Finance snapshot fetched ${PAYLOAD.fetchedAt} · ${modeDesc}`;
}

function renderAll() {
  const rawRangeData = getActiveRangeData();
  const effectiveBookData = getEffectiveBookData(rawRangeData);
  updateStamps();
  updateSentimentBar();
  renderGlobalSimulator();
  renderRangeBar(effectiveBookData);
  renderComparison(effectiveBookData);
  renderFxSection(rawRangeData);
  renderSleeves(effectiveBookData);
}

function setIntegrateChf(val) { integrateChf = Boolean(val); saveViewState(); renderAll(); }
document.getElementById("chfNoBtn").onclick = () => setIntegrateChf(false);
document.getElementById("chfYesBtn").onclick = () => setIntegrateChf(true);

function applyCustomStartDate() {
  const val = document.getElementById("startDateInput").value;
  if (!val) return;
  currentMode = "custom"; customStartDate = val; saveViewState(); renderAll();
}
document.getElementById("applyDateBtn").onclick = applyCustomStartDate;
document.getElementById("startDateInput").onchange = applyCustomStartDate;

function updateRefreshUi() {
  const dot = document.getElementById("refreshDot");
  const btn = document.getElementById("toggleRefreshBtn");
  const cd = document.getElementById("countdownLbl");
  dot.className = autoRefreshEnabled ? "dot-live" : "dot-paused";
  btn.textContent = autoRefreshEnabled ? "ON" : "PAUSED";
  const mins = String(Math.floor(secondsLeft / 60)).padStart(2, "0");
  const secs = String(secondsLeft % 60).padStart(2, "0");
  cd.textContent = autoRefreshEnabled ? `Next in ${mins}:${secs}` : "Paused";
}

async function performLiveRefresh(manual = false) {
  const cd = document.getElementById("countdownLbl");
  cd.textContent = "Refreshing...";
  secondsLeft = REFRESH_SECONDS;

  if (window.location.protocol.startsWith("http")) {
    try {
      const endpoint = manual ? `/api/refresh?t=${Date.now()}` : `/api/live-data?t=${Date.now()}`;
      const resp = await fetch(endpoint, { cache: "no-store" });
      if (resp.ok) {
        const nextPayload = await resp.json();
        if (nextPayload && nextPayload.ranges) {
          PAYLOAD = nextPayload;
          const nextToday = getTodayDataDate();
          if (!simState.endDate || simState.endDate < nextToday) { simState.endDate = nextToday; saveSimState(); }
          renderAll(); updateRefreshUi(); return;
        }
      }
    } catch (e) {}
  }

  try {
    await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = `etf_dashboard_data.js?t=${Date.now()}`;
      s.onload = () => resolve(); s.onerror = () => reject(new Error("load failed"));
      document.head.appendChild(s);
      setTimeout(() => { try { document.head.removeChild(s); } catch (e) {} }, 5000);
    });
    if (window.__ETF_LIVE_DATA__ && window.__ETF_LIVE_DATA__.ranges) {
      PAYLOAD = window.__ETF_LIVE_DATA__;
      const nextToday = getTodayDataDate();
      if (!simState.endDate || simState.endDate < nextToday) { simState.endDate = nextToday; saveSimState(); }
      renderAll(); updateRefreshUi(); return;
    }
  } catch (e) {}

  saveViewState();
  window.location.reload();
}

document.getElementById("toggleRefreshBtn").onclick = () => { autoRefreshEnabled = !autoRefreshEnabled; saveViewState(); updateRefreshUi(); };
document.getElementById("refreshNowBtn").onclick = () => performLiveRefresh(true);

setInterval(() => {
  if (!autoRefreshEnabled) return;
  secondsLeft -= 1;
  if (secondsLeft <= 0) { performLiveRefresh(false); } else { updateRefreshUi(); }
}, 1000);

loadViewState();
loadSimState();
wireSimulatorEvents();
initAllocation();
renderAll();
updateRefreshUi();
</script>
</body>
</html>
"""


def generate_dashboard_files(
    out_html_path: Path,
    initial_start_date: str | None = None,
    also_copy_to_public: bool = True,
) -> dict:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching TradingView live quotes + sentiment history (1D/1W/1M/1Y)...")
    tv_quotes = _fetch_tradingview_live_quotes()
    sentiment_history = _fetch_sentiment_history()

    ranges_payload: dict[str, dict] = {}
    print("  -> Fetching max daily history once...")
    _, max_raw, max_fx_raw = build_range_payload("max", RANGES["max"])

    today_ts = pd.Timestamp.now(tz="Europe/Zurich").tz_localize(None).normalize()
    for res in max_raw + max_fx_raw:
        tkr = res["ticker"]
        if tkr in tv_quotes and not res["series"].empty:
            res["series"].loc[today_ts] = tv_quotes[tkr]
            res["series"] = res["series"].sort_index()

    daily_matrix = build_daily_history_matrix(max_raw, max_fx_raw)

    for rid, spec in RANGES.items():
        if rid in DAILY_LOOKBACK_DAYS:
            lookback = DAILY_LOOKBACK_DAYS[rid]
            sub_raw = _slice_results_by_days(max_raw, lookback)
            sub_fx = _slice_results_by_days(max_fx_raw, lookback)
            r_payload, _, _ = build_range_payload(rid, spec, raw_results=sub_raw, fx_results=sub_fx)
        else:
            print(f"  -> Fetching intraday {rid:<3} ({spec['title']})...")
            r_payload, _, _ = build_range_payload(rid, spec)
        ranges_payload[rid] = r_payload

    payload = {
        "fetchedAt": datetime.now(ZoneInfo("Europe/Zurich")).strftime("%Y-%m-%d %H:%M %Z"),
        "refreshIntervalSeconds": REFRESH_INTERVAL_SECONDS,
        "initialStartDate": initial_start_date,
        "rangeOrder": list(RANGES.keys()),
        "sleeves": SLEEVES,
        "fxPairs": FX_PAIRS,
        "sentimentHistory": sentiment_history,
        "etfs": [
            {
                "id": e["id"], "sleeve": e["sleeve"], "weight": e["weight"],
                "name": e["name"], "shortName": e["shortName"], "color": e["color"],
                "requestedTicker": e["candidates"][0]["ticker"],
            }
            for e in ETFS
        ],
        "ranges": ranges_payload,
        "daily": daily_matrix,
    }

    payload_json = json.dumps(payload, separators=(",", ":"))
    html = HTML_TEMPLATE.replace("__PAYLOAD_JSON__", payload_json)
    out_html_path.write_text(html, encoding="utf-8")

    data_js_path = out_html_path.with_name("etf_dashboard_data.js")
    data_js_content = f"window.__ETF_LIVE_DATA__ = {payload_json};\n"
    data_js_path.write_text(data_js_content, encoding="utf-8")

    if also_copy_to_public:
        pub_dir = out_html_path.parent / "public"
        if pub_dir.is_dir():
            (pub_dir / out_html_path.name).write_text(html, encoding="utf-8")
            (pub_dir / "etf_dashboard_data.js").write_text(data_js_content, encoding="utf-8")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Saved {out_html_path.name} + {data_js_path.name}")
    return payload


class DashboardServerState:
    def __init__(self, html_path: Path, initial_start_date: str | None) -> None:
        self.html_path = html_path
        self.initial_start_date = initial_start_date
        self.lock = threading.Lock()
        self.payload: dict = {}

    def refresh(self) -> dict:
        with self.lock:
            self.payload = generate_dashboard_files(self.html_path, self.initial_start_date)
            return self.payload


def run_server(state: DashboardServerState, host: str, port: int, open_browser: bool) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/etf_dashboard.html"):
                body = state.html_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/etf_dashboard_data.js":
                js_path = state.html_path.with_name("etf_dashboard_data.js")
                body = js_path.read_bytes() if js_path.exists() else b""
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/live-data":
                body = json.dumps(state.payload, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/refresh":
                updated = state.refresh()
                body = json.dumps(updated, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt: str, *args) -> None:
            return

    def auto_refresh_worker() -> None:
        while True:
            time.sleep(REFRESH_INTERVAL_SECONDS)
            try:
                state.refresh()
            except Exception as exc:
                print(f"[auto-refresh] Warning: refresh failed: {exc}")

    t = threading.Thread(target=auto_refresh_worker, name="etf-auto-refresh", daemon=True)
    t.start()

    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"Serving live ETF dashboard at {url} (auto-refreshing every 5 minutes). Press Ctrl+C to stop.")
    if open_browser and (os.environ.get("DISPLAY") or os.name == "nt" or os.uname().sysname == "Darwin"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and serve standalone Swiss ETF portfolio HTML dashboard.")
    parser.add_argument(
        "-o", "--output",
        default=str(Path(__file__).resolve().parent / "etf_dashboard.html"),
        help="Output HTML file path (default: ./etf_dashboard.html)",
    )
    parser.add_argument("--start-date", default=None, help="Optional initial custom start date (YYYY-MM-DD)")
    parser.add_argument("--once", action="store_true", help="Generate dashboard files once and exit")
    parser.add_argument("--no-open", action="store_true", help="Do not automatically open browser")
    parser.add_argument("--host", default="127.0.0.1", help="Local HTTP server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=41775, help="Local HTTP server port (default: 41775)")
    args = parser.parse_args()

    out_path = Path(args.output).resolve()
    state = DashboardServerState(out_path, args.start_date)
    state.refresh()

    if args.once:
        if not args.no_open and (os.environ.get("DISPLAY") or os.name == "nt" or os.uname().sysname == "Darwin"):
            try:
                webbrowser.open(out_path.as_uri())
            except Exception:
                pass
        return

    run_server(state, args.host, args.port, not args.no_open)


if __name__ == "__main__":
    main()