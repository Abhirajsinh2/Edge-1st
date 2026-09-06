"""
Full-history compounding backtest for Edge 1st driven by a LOCAL 1-minute CSV
file instead of Upstox's chunked historical-candle API.

Same engine as full_history_backtest.py (bot.replay_week -> one CapitalAccount
per instrument, compounding start to finish, full Upstox F&O costs), it just
gets its bars from a CSV you already have on disk.

CSV format (header required):

    timestamp,open,high,low,close,volume
    2022-01-03 09:15:00+05:30,17387.15,17438.4,17387.15,17428.9,0
    ...

Usage
-----
    python local_history_backtest.py                       # both instruments, default paths
    python local_history_backtest.py NIFTY                 # one instrument
    python local_history_backtest.py --from 2024-01-01 --to 2025-12-31
    python local_history_backtest.py --nifty PATH --banknifty PATH

Default CSV paths point at the files in this machine's Downloads folder; pass
--nifty / --banknifty to override.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

import config
import edge_1st_bot as bot
from capital_manager import CapitalAccount

DEFAULTS = {
    "NIFTY": r"C:\Users\yashp\Downloads\spy_qqq_trading_bot\spy_qqq_trading_bot\_archive\generated-artifacts\historical_NIFTY.csv",
    "BANKNIFTY": r"C:\Users\yashp\Downloads\spy_qqq_trading_bot\spy_qqq_trading_bot\_archive\generated-artifacts\historical_BANKNIFTY.csv",
}


def load_1min(path: str, date_from=None, date_to=None) -> pd.DataFrame:
    df = pd.read_csv(path)
    ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(config.MARKET_TZ)
    df = df.drop(columns=["timestamp"]).set_index(ts).sort_index()
    df.index.name = None
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "high", "low", "close"])
    if date_from is not None:
        df = df[df.index.date >= pd.Timestamp(date_from).date()]
    if date_to is not None:
        df = df[df.index.date <= pd.Timestamp(date_to).date()]
    return df


def run(paths: dict, date_from, date_to) -> dict:
    results = {}
    for sym, path in paths.items():
        if not Path(path).exists():
            print(f"  {sym}: CSV not found at {path} -- skipping")
            continue
        print(f"\nloading 1-min {sym} from {path}")
        df = load_1min(path, date_from, date_to)
        if df.empty:
            print(f"  {sym}: no rows in the selected range -- skipping")
            continue
        print(f"  {sym}: {len(df):,} 1-min bars   {df.index.min()}  ->  {df.index.max()}")

        account = CapitalAccount(config.CAPITAL, "Rs ", config.WITHDRAWAL_MULTIPLE)
        print(f"  {sym}: replaying (1-min exit model, compounding) ...")
        t0 = time.time()
        # empty prev-day map -> replay_week derives previous-session OHLC for
        # Camarilla pivots from the minute data itself (same source as the bars)
        trades = bot.replay_week(sym, df, account, {})
        print(f"  {sym}: done in {time.time() - t0:.0f}s, {len(trades)} trades")

        summary = bot.summarize(sym, trades, account)
        bot.write_csv(sym, trades)
        results[sym] = {"summary": summary, "trades": trades, "account": account.snapshot()}
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", choices=["NIFTY", "BANKNIFTY"], help="one instrument only")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--nifty", default=DEFAULTS["NIFTY"])
    ap.add_argument("--banknifty", default=DEFAULTS["BANKNIFTY"])
    args = ap.parse_args()

    paths = {"NIFTY": args.nifty, "BANKNIFTY": args.banknifty}
    if args.symbol:
        paths = {args.symbol: paths[args.symbol]}

    results = run(paths, args.date_from, args.date_to)
    if not results:
        print("\nNothing ran.")
        return

    print("\n" + "=" * 64)
    print("  EDGE 1ST -- LOCAL FULL HISTORY, COMPOUNDING")
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

    subprocess.run([sys.executable, "generate_full_history_dashboard.py"], check=False)


if __name__ == "__main__":
    main()
