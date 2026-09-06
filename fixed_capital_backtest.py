"""
Full-history FIXED-STAKE backtest for Edge 1st -- the "do NOT reinvest" run.

Same strategy, same 1-minute exit model, same full Upstox F&O costs, same local
1-minute CSVs as local_history_backtest.py -- the ONLY difference is the money
model:

    local_history_backtest.py  -> CapitalAccount(..., compound=True)
        every trade sizes off the CURRENT (growing) balance; profit rides.

    this file                  -> CapitalAccount(..., compound=False)
        every trade sizes off the flat original Rs 2,00,000 stake, forever.
        Realised P&L still accrues so the equity curve moves, but it is never
        fed back into position sizing and there is no 2x-stake withdrawal.

Nothing about the strategy or the engine is touched -- edge_1st_bot.replay_week
just reads account.sizing_equity for sizing, which is the flat stake here.

Writes fixed_capital_results.json and then renders fixed-capital.html via
generate_fixed_capital_dashboard.py.

Usage
-----
    python fixed_capital_backtest.py                       # both instruments, default CSV paths
    python fixed_capital_backtest.py NIFTY                 # one instrument
    python fixed_capital_backtest.py --from 2024-01-01 --to 2025-12-31
    python fixed_capital_backtest.py --nifty PATH --banknifty PATH
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import config
import edge_1st_bot as bot
from capital_manager import CapitalAccount
from local_history_backtest import DEFAULTS, load_1min

# Keep the live-run artifacts (edge_1st_trades_<SYM>.csv, committed by the
# GitHub Action) untouched -- write this run's per-trade breakdown separately.
FIXED_CSV_FIELDS = bot.CSV_FIELDS + ["realized_net_to_date"]


def write_fixed_csv(symbol: str, trades: list) -> str:
    path = f"edge_1st_trades_fixed_{symbol}.csv"
    running = 0.0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIXED_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            running += t["net_pnl_inr"]
            w.writerow({**t, "realized_net_to_date": round(running, 2)})
    return path


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

        # compound=False -> fixed Rs 2,00,000 stake for every trade, no withdrawal
        account = CapitalAccount(config.CAPITAL, "Rs ", config.WITHDRAWAL_MULTIPLE, compound=False)
        print(f"  {sym}: replaying (1-min exit model, FIXED stake, no reinvest) ...")
        t0 = time.time()
        trades = bot.replay_week(sym, df, account, {})
        print(f"  {sym}: done in {time.time() - t0:.0f}s, {len(trades)} trades")

        summary = bot.summarize(sym, trades, account)
        write_fixed_csv(sym, trades)
        results[sym] = {"summary": summary, "trades": trades, "account": account.snapshot()}
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", choices=["NIFTY", "BANKNIFTY"], help="one instrument only")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--nifty", default=DEFAULTS["NIFTY"])
    ap.add_argument("--banknifty", default=DEFAULTS["BANKNIFTY"])
    ap.add_argument("--no-dashboard", action="store_true", help="skip rendering fixed-capital.html")
    args = ap.parse_args()

    paths = {"NIFTY": args.nifty, "BANKNIFTY": args.banknifty}
    if args.symbol:
        paths = {args.symbol: paths[args.symbol]}

    results = run(paths, args.date_from, args.date_to)
    if not results:
        print("\nNothing ran.")
        return

    print("\n" + "=" * 64)
    print("  EDGE 1ST -- FULL HISTORY, FIXED Rs 2,00,000 STAKE (NO REINVEST)")
    for sym, r in results.items():
        s, a = r["summary"], r["account"]
        print(f"  {sym}: {s['trades']} trades, NET Rs {s['net']:+,.0f}, "
              f"total value Rs {a['total_value']:,.0f} (from Rs {a['initial']:,.0f}), "
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
    with open("fixed_capital_results.json", "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, default=str)
    print("\nwrote fixed_capital_results.json")

    if not args.no_dashboard:
        subprocess.run([sys.executable, "generate_fixed_capital_dashboard.py"], check=False)


if __name__ == "__main__":
    main()
