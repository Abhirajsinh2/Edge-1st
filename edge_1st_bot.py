"""
Edge 1st bot — runs the FIRST Edge strategy (strategy.py, formerly the
archived "Strategy 5" strategy_edge.py) over the LAST WEEK of real 1-minute
NIFTY / BANKNIFTY market data.

"Keep everything the same" = this uses the SAME 1-minute exit-management model
as the backtest that produced the winning numbers:

    * open positions are checked every 1-minute bar
    * stop / target fill at the exact SL / TP price if that 1-min bar's
      range touches it
    * otherwise should_exit_early() (thesis broken) exits at that 1-min
      bar's close, BEFORE price has to travel all the way to the stop
    * new entries are only evaluated on 5-minute bar boundaries

Full Zerodha index-futures charges (brokerage + STT + exchange txn + SEBI +
stamp duty + GST) are applied to every trade via zerodha_fno_costs.py.
Capital compounds at 1% risk/trade with the one-time 2x withdrawal
(capital_manager.py). NO REAL ORDERS ARE EVER PLACED.

Usage
-----
    python edge_1st_bot.py            # replay the last 7 days, write report + dashboard
    python edge_1st_bot.py 5          # NIFTY only? no - day count; use one instrument arg for that
    python edge_1st_bot.py NIFTY      # just one instrument
    python edge_1st_bot.py live       # forward paper loop, same 1-min exit logic
"""

import csv
import os
import sys
import time as _time
import webbrowser
from dataclasses import dataclass
from datetime import datetime as _dt

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pandas as pd

import config
import strategy as strat
from capital_manager import CapitalAccount, size_by_risk
from zerodha_fno_costs import calculate_futures_costs

# --- pick the market-data source: upstox (default) or yahoo ---------------
_SRC = os.getenv("EDGE1ST_DATA", getattr(config, "DATA_SOURCE", "upstox")).lower()
if _SRC == "upstox":
    try:
        import data_upstox as market
        _probe = market.get_week_1min(config.INSTRUMENTS[0], 3)
        if _probe.empty:
            raise RuntimeError("Upstox returned no candles")
        _tok = " + token (live intraday)" if market.has_token() else " (no token — run login.py for freshest bars)"
        print(f"data source: Upstox v3 historical-candle{_tok}")
    except Exception as e:
        print(f"data source: Upstox unavailable ({e})\n             -> falling back to Yahoo Finance")
        import data as market
        _SRC = "yahoo"
else:
    import data as market
    print("data source: Yahoo Finance")

LEVERAGE = getattr(config, "ASSUMED_MARGIN_LEVERAGE", 10.0)
RISK_PCT = getattr(config, "RISK_PCT_PER_TRADE", 0.01)
MAX_RISK_PCT = getattr(config, "MAX_RISK_PCT", 0.03)
ENTRY_LOOKBACK_BARS = 3000
REF_LOOKBACK_BARS = 1500
_OPEN_MIN = int(config.MARKET_OPEN[:2]) * 60 + int(config.MARKET_OPEN[3:])

CSV_FIELDS = ["date", "instrument", "direction", "entry_time", "entry_price",
              "exit_time", "exit_price", "exit_reason", "quantity", "points",
              "pnl_inr", "brokerage", "stt", "exchange_txn_charges", "sebi_charges",
              "stamp_duty", "gst", "total_charges", "net_pnl_inr",
              "equity_before", "equity_after", "withdrawn_to_date", "withdrawal_here"]


@dataclass
class Position:
    direction: str
    entry_price: float
    entry_time: pd.Timestamp
    stop_loss: float
    take_profit: float
    quantity: int


def _resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    out = pd.concat                    points_today += pts
                    eq_before = account.equity
                    withdrew = account.book_trade(net, c["total_charges"])
                    trades.append({
                        "date": day, "instrument": symbol, "direction": open_pos.direction,
                        "entry_time": open_pos.entry_time, "entry_price": round(open_pos.entry_price, 2),
                        "exit_time": ts, "exit_price": round(exit_price, 2), "exit_reason": reason,
                        "quantity": qty, "points": round(pts, 2), "pnl_inr": round(gross, 2),
                        "brokerage": c["brokerage"], "stt": c["stt"],
                        "exchange_txn_charges": c["exchange_txn_charges"], "sebi_charges": c["sebi_charges"],
                        "stamp_duty": c["stamp_duty"], "gst": c["gst"], "total_charges": c["total_charges"],
                        "net_pnl_inr": round(net, 2), "equity_before": round(eq_before, 2),
                        "equity_after": round(account.equity, 2),
                        "withdrawn_to_date": round(account.withdrawn, 2), "withdrawal_here": withdrew,
                    })
                    open_pos = None
                continue

            # ---- flat: look for an entry, only on 5-min boundaries ----
            if trades_today >= config.MAX_TRADES_PER_DAY:
                continue
            if points_today >= config.DAILY_TARGET_POINTS:
                continue
            if not _entries_allowed(ts):
                continue
            if (ts.hour * 60 + ts.minute) % entry_min != 0:
                continue

            pos_e = df_entry_full.index.searchsorted(ts, side="right")
            pos_r = df_ref_full.index.searchsorted(ts, side="right")
            de = df_entry_full.iloc[max(0, pos_e - ENTRY_LOOKBACK_BARS):pos_e]
            dr = df_ref_full.iloc[max(0, pos_r - REF_LOOKBACK_BARS):pos_r]
            if len(de) < 5:
                continue

            try:
                signal, _ = strat.generate_signal(de, dr, prev, instrument=symbol)
            except Exception:
                continue
            if signal not in (strat.Signal.LONG, strat.Signal.SHORT):
                continue

            ep = float(bar["close"])
            direction = "LONG" if signal == strat.Signal.LONG else "SHORT"
            sl, tp = strat.compute_stop_and_target(de, ep, direction)
            if (direction == "LONG" and sl >= ep) or (direction == "SHORT" and sl <= ep):
                continue
            qty = size_by_risk(account.equity, ep, sl, RISK_PCT, LEVERAGE,
                               lot_size=lot_size, max_risk_pct=MAX_RISK_PCT)
            if qty < 1:
                continue
            open_pos = Position(direction, ep, ts, sl, tp, qty)
            trades_today += 1

    return trades


# ── reporting ────────────────────────────────────────────────────────────

def summarize(symbol: str, trades: list, account: CapitalAccount) -> dict:
    n = len(trades)
    if n == 0:
        print(f"\n  {symbol}: no trades over this window.")
        return {"instrument": symbol, "trades": 0, "win_rate": 0.0, "gross": 0.0,
                "charges": 0.0, "net": 0.0, "days": 0}
    wins = sum(1 for t in trades if t["net_pnl_inr"] > 0)
    gross = sum(t["pnl_inr"] for t in trades)
    charges = sum(t["total_charges"] for t in trades)
    net = sum(t["net_pnl_inr"] for t in trades)
    days = len({t["date"] for t in trades})
    reasons = {}
    for t in trades<table><tr><th>Sym</th><th>Dir</th><th>Entry time</th><th>Entry</th><th>Exit time</th>
<th>Exit</th><th>Reason</th><th>Qty</th><th>Net Rs</th></tr>{trows}</table>
</body></html>"""
    path = "edge_1st_dashboard.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ── entrypoints ──────────────────────────────────────────────────────────

def run_week(only_symbol: str = None, days: int = None):
    # The dashboard is intentionally a rolling recent-week view.  Keep an
    # accidental larger CLI value from pulling/displaying older history.
    days = config.INTRADAY_LOOKBACK_DAYS if days is None else min(max(int(days), 1), config.INTRADAY_LOOKBACK_DAYS)
    syms = [only_symbol] if only_symbol else list(config.INSTRUMENTS)
    rows, all_trades = [], {}
    win_lo = win_hi = None
    for sym in syms:
        print(f"\nfetching last {days or config.INTRADAY_LOOKBACK_DAYS} days of 1-min {sym} ...")
        df = market.get_week_1min(sym, days)
        if df.empty:
            print(f"  {sym}: no data returned from Yahoo (throttled or market holiday week).")
            # Replace any prior artifact so the published view cannot show an
            # older week's trades when this week's feed is empty.
            write_csv(sym, [])
            all_trades[sym] = []
            continue
        w0, w1 = df.index.min(), df.index.max()
        win_lo = w0 if win_lo is None else min(win_lo, w0)
        win_hi = w1 if win_hi is None else max(win_hi, w1)
        print(f"  {len(df):,} 1-min bars   {w0}  ->  {w1}")
        account = CapitalAccount(config.CAPITAL, "Rs ", config.WITHDRAWAL_MULTIPLE)
        trades = replay_week(sym, df, account, market.prev_day_ohlc_map(sym))
        rows.append(summarize(sym, trades, account))
        all_trades[sym] = trades
        write_csv(sym, trades)

    if not rows:
        print("\nNothing to report.")
        dash = write_dashboard([], all_trades, f"last {days} calendar days (no trades)")
        print(f"\nwrote {dash} (no trades in the recent-week window)")
        return

    tot_g = sum(r["gross"] for r in rows)
    tot_c = sum(r["charges"] for r in rows)
    tot_n = sum(r["net"] for r in rows)
    tot_t = sum(r["trades"] for r in rows)
    print("\n" + "=" * 64)
    print(f"  EDGE 1ST — WEEK TOTAL ({tot_t} trades)")
    print(f"  gross Rs {tot_g:+,.0f}   charges Rs {tot_c:,.0f}   NET Rs {tot_n:+,.0f}")
    print("=" * 64)

    desc = f"{win_lo:%Y-%m-%d} to {win_hi:%Y-%m-%d}" if win_lo is not None else "last week"
    dash = write_dashboard(rows, all_trades, desc)
    print(f"\nwrote {dash}  +  edge_1st_trades_<SYM>.csv")
    if os.getenv("EDGE1ST_NO_BROWSER") != "1":
        try:
            webbrowser.open(os.path.abspath(dash))
        except Exception:
            pass


def run_live():
    """Forward paper loop — same 1-minute exit model, re-pulling fresh data each poll."""
    print("Edge 1st LIVE paper loop — Ctrl+C to stop. No real orders.\n")
    accounts = {s: CapitalAccount(config.CAPITAL, "Rs ", config.WITHDRAWAL_MULTIPLE)
                for s in config.INSTRUMENTS}
    seen = {s: set() for s in config.INSTRUMENTS}
    poll = 0
    while True:
        poll += 1
        for sym in config.INSTRUMENTS:
            try:
                df = market.get_week_1min(sym, days=2)
                if df.empty:
                    print(f"[poll {poll}] {sym}: no data"); continue
                trades = replay_week(sym, df, accounts[sym], market.prev_day_ohlc_map(sym))
                fresh = [t for t in trades if (t["entry_time"], t["exit_time"]) not in seen[sym]]
                for t in fresh:
                    seen[sym].add((t["entry_time"], t["exit_time"]))
                    print(f"[poll {poll}] {sym} {t['direction']} {t['exit_reason']} "
                          f"net Rs {t['net_pnl_inr']:+,.0f}  eq Rs {accounts[sym].equity:,.0f}")
                if not fresh:
                    last = df.index[-1]
                    print(f"[poll {poll}] {sym}: flat, last bar {last:%H:%M}")
            except Exception as e:
                print(f"[poll {poll}] {sym} ERROR: {e}")
        _time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if args and args[0].lower() == "live":
        run_live()
    else:
        sym = next((a.upper() for a in args if not a.isdigit()), None)
        dcount = next((int(a) for a in args if a.isdigit()), None)
        run_week(only_symbol=sym, days=dcount)
