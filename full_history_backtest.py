"""
Full-history compounding backtest for Edge 1st -- separate from the live
4-week scheduled dashboard (edge_1st_bot.py run_week()), which stays as-is.

Upstox's v3 historical-candle endpoint caps 1-minute granularity at ~31 days
per request but the underlying archive goes back years, so this chains
consecutive ~29-day chunks to assemble a much longer continuous series, then
replays it through ONE CapitalAccount per instrument from start to finish --
exactly the "reinvest everything, next trade sizes off the new balance"
compounding edge_1st_bot.py's size_by_risk() already does per-trade, just
run across the whole history instead of one rolling 4-week window.

Usage
-----
    python full_history_backtest.py            # last 12 months, both instruments
    python full_history_backtest.py 6           # last 6 months
    python full_history_backtest.py 12 NIFTY    # last 12 months, one instrument
"""

import json
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import config
import data_upstox as dux
import edge_1st_bot as bot
from capital_manager import CapitalAccount

TZ = ZoneInfo(config.MARKET_TZ)
CHUNK_DAYS = 29  # stay under Upstox's ~31-day cap per request


def fetch_long_1min(symbol: str, months_back: int) -> pd.DataFrame:
    inst = config.UPSTOX_INSTRUMENT_KEYS[symbol]
    now = datetime.now(TZ)
    earliest = now - timedelta(days=30 * months_back)

    chunks = []
    cursor = now
    while cursor > earliest:
        from_d = max(cursor - timedelta(days=CHUNK_DAYS), earliest)
        df = dux._hist_1min(inst, from_d, cursor)
        if df.empty:
            time.sleep(1.5)
            df = dux._hist_1min(inst, from_d, cursor)  # one retry for throttling blips
        if not df.empty:
            chunks.append(df)
            print(f"  {symbol}: {from_d.date()} -> {cursor.date()}  {len(df):,} bars")
        else:
            print(f"  {symbol}: {from_d.date()} -> {cursor.date()}  no data (holiday range or gap)")
        cursor = from_d - timedelta(days=1)

    intr = dux._intraday_1min(inst)
    if not intr.empty:
        chunks.append(intr)

    if not chunks:
        return pd.DataFrame()
    combined = pd.concat(chunks)
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    return combined


def fetch_long_prev_day_map(symbol: str, months_back: int) -> dict:
    inst = config.UPSTOX_INSTRUMENT_KEYS[symbol]
    now = datetime.now(TZ)
    daily = dux._hist_daily(inst, now - timedelta(days=30 * months_back + 10), now)
    out = {}
    if daily.empty or len(daily) < 2:
        return out
    dates = list(daily.index.date)
    for i in range(1, len(dates)):
        pr = daily.iloc[i - 1]
        out[dates[i]] = {"high": float(pr["high"]), "low": float(pr["low"]), "close": float(pr["close"])}
    return out


def run(months_back: int, syms: list):
    all_results = {}
    for sym in syms:
        print(f"\nfetching {months_back} months of 1-min {sym} (chunked, Upstox caps ~31d/request) ...")
        df = fetch_long_1min(sym, months_back)
        if df.empty:
            print(f"  {sym}: no data at all -- skipping")
            continue
        print(f"  {sym}: {len(df):,} total 1-min bars, {df.index.min()} -> {df.index.max()}")

        prev_map = fetch_long_prev_day_map(sym, months_back)
        account = CapitalAccount(config.CAPITAL, "Rs ", config.WITHDRAWAL_MULTIPLE)

        print(f"  {sym}: replaying ...")
        trades = bot.replay_week(sym, df, account, prev_map)
        summary = bot.summarize(sym, trades, account)
        all_results[sym] = {"summary": summary, "trades": trades, "account": account.snapshot()}

    return all_results


if __name__ == "__main__":
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    only = sys.argv[2] if len(sys.argv) > 2 else None
    syms = [only] if only else list(config.INSTRUMENTS)

    results = run(months, syms)

    print("\n" + "=" * 64)
    print(f"  EDGE 1ST -- FULL HISTORY ({months} months), COMPOUNDING")
    for sym, r in results.items():
        s, a = r["summary"], r["account"]
        print(f"  {sym}: {s['trades']} trades, NET Rs {s['net']:+,.0f}, "
              f"final equity Rs {a['equity']:,.0f} (from Rs {a['initial']:,.0f}), "
              f"return {a['return_pct']:+.1f}%")
    print("=" * 64)

    serializable = {
        sym: {
            "summary": r["summary"],
            "account": r["account"],
            "trades": [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in t.items()}
                for t in r["trades"]
            ],
        }
        for sym, r in results.items()
    }
    with open("full_history_results.json", "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, default=str)
    print("\nwrote full_history_results.json")
