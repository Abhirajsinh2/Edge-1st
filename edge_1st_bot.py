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

Full Upstox index-futures charges (brokerage + STT + exchange txn + SEBI +
stamp duty + GST) are applied to every trade via upstox_fno_costs.py.
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
from datetime import datetime as _dt, timedelta as _td

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pandas as pd

import config
import strategy as strat
from capital_manager import CapitalAccount, size_by_risk
from upstox_fno_costs import calculate_futures_costs

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
    out = pd.concat([
        df["open"].resample(rule).first(), df["high"].resample(rule).max(),
        df["low"].resample(rule).min(), df["close"].resample(rule).last(),
        df["volume"].resample(rule).sum(),
    ], axis=1)
    out.columns = ["open", "high", "low", "close", "volume"]
    return out.dropna()


_CAS_FREEZE_MIN = int(config.CAS_FREEZE_TIME[:2]) * 60 + int(config.CAS_FREEZE_TIME[3:])


def _entries_allowed(ts) -> bool:
    minute_of_day = ts.hour * 60 + ts.minute
    if minute_of_day < _OPEN_MIN + config.NO_TRADE_MINUTES_AFTER_OPEN:
        return False
    if minute_of_day >= _CAS_FREEZE_MIN - config.NO_TRADE_MINUTES_BEFORE_CAS_FREEZE:
        return False
    return True


def _must_force_flatten(ts) -> bool:
    """The last bar before the CAS freeze -- still real, continuous price
    data, so a genuine fill is possible here. One minute later, continuous
    trading has already halted (verified in our own 1-min data: the bar at
    CAS_FREEZE_TIME is already frozen O=H=L=C)."""
    return (ts.hour * 60 + ts.minute) == _CAS_FREEZE_MIN - 1


def _slippage(symbol: str, bar) -> float:
    """Points of adverse slippage for a market-speed fill on this bar --
    a per-instrument floor plus a fraction of the bar's own high-low range,
    so a fast/violent bar costs more than a quiet one."""
    floor = config.ASSUMED_SLIPPAGE_POINTS.get(symbol, 1.0)
    bar_range = float(bar["high"]) - float(bar["low"])
    return max(floor, bar_range * config.SLIPPAGE_RANGE_FRACTION)


def _costs(direction: str, entry: float, exit_: float, qty: int) -> dict:
    if direction == "LONG":
        return calculate_futures_costs(buy_price=entry, sell_price=exit_, qty=qty)
    return calculate_futures_costs(buy_price=exit_, sell_price=entry, qty=qty)


# ── core: 1-minute-exit replay over one instrument's week ──────────────────

def replay_week(symbol: str, df_1m: pd.DataFrame, account: CapitalAccount,
                prev_ohlc_map: dict, signals: list = None) -> list:
    """`signals`, if given, is appended to with every LONG/SHORT the
    strategy actually generated -- including ones that did not turn into a
    trade (invalid stop, or position sizing rounded to 0 lots). This is
    evaluated only once the daily-trade-cap / daily-target / no-trade-window
    gates already pass, same as a real entry would be -- it does not
    capture a signal the strategy would have fired had those gates not
    blocked evaluation in the first place."""
    lot_size = config.LOT_SIZES.get(symbol, 1)
    df_1m = df_1m.sort_index()
    df_entry_full = _resample(df_1m, config.ENTRY_TIMEFRAME_MIN)
    df_ref_full = _resample(df_1m, config.REFERENCE_TIMEFRAME_MIN)
    daily = df_1m.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    entry_min = config.ENTRY_TIMEFRAME_MIN
    session_dates = sorted(set(df_1m.index.date))

    trades: list = []
    for day_idx, day in enumerate(session_dates):
        prev = prev_ohlc_map.get(day)
        if prev is None:
            drows = daily[daily.index.date < day]
            if drows.empty:
                continue
            pr = drows.iloc[-1]
            prev = {"high": float(pr["high"]), "low": float(pr["low"]), "close": float(pr["close"])}

        day_bars = df_1m[df_1m.index.date == day]
        if day_bars.empty:
            continue

        open_pos = None
        trades_today = 0
        points_today = 0.0

        for ts in day_bars.index:
            bar = df_1m.loc[ts]

            # ---- manage an open position against THIS 1-minute bar ----
            if open_pos is not None:
                reason = None
                if open_pos.direction == "LONG":
                    if bar["low"] <= open_pos.stop_loss:
                        reason = "SL"
                    elif bar["high"] >= open_pos.take_profit:
                        reason = "TP"
                else:
                    if bar["high"] >= open_pos.stop_loss:
                        reason = "SL"
                    elif bar["low"] <= open_pos.take_profit:
                        reason = "TP"

                if reason is None:
                    pos_e = df_entry_full.index.searchsorted(ts, side="right")
                    de = df_entry_full.iloc[max(0, pos_e - ENTRY_LOOKBACK_BARS):pos_e]
                    if len(de) and strat.should_exit_early(de, open_pos.direction):
                        reason = "EARLY_EXIT"

                if reason is None and _must_force_flatten(ts):
                    reason = "PRE_CLOSE_FLATTEN"

                if reason is not None:
                    if reason == "TP":
                        exit_price = open_pos.take_profit
                    else:
                        raw_exit = open_pos.stop_loss if reason == "SL" else float(bar["close"])
                        slip = _slippage(symbol, bar)
                        exit_price = (raw_exit - slip if open_pos.direction == "LONG"
                                      else raw_exit + slip)
                    pts = ((exit_price - open_pos.entry_price) if open_pos.direction == "LONG"
                           else (open_pos.entry_price - exit_price))
                    qty = open_pos.quantity
                    gross = pts * qty
                    c = _costs(open_pos.direction, open_pos.entry_price, exit_price, qty)
                    net = gross - c["total_charges"]
                    points_today += pts
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

            direction = "LONG" if signal == strat.Signal.LONG else "SHORT"
            sig_record = {"date": day, "instrument": symbol, "time": ts, "direction": direction}
            slip = _slippage(symbol, bar)
            ep = float(bar["close"]) + (slip if direction == "LONG" else -slip)
            sl, tp = strat.compute_stop_and_target(de, ep, direction)
            if (direction == "LONG" and sl >= ep) or (direction == "SHORT" and sl <= ep):
                sig_record["entered"] = False
                sig_record["skip_reason"] = "invalid_stop"
                if signals is not None:
                    signals.append(sig_record)
                continue
            qty = size_by_risk(account.equity, ep, sl, RISK_PCT, LEVERAGE,
                               lot_size=lot_size, max_risk_pct=MAX_RISK_PCT)
            if qty < 1:
                sig_record["entered"] = False
                sig_record["skip_reason"] = "position_size_zero"
                if signals is not None:
                    signals.append(sig_record)
                continue
            open_pos = Position(direction, ep, ts, sl, tp, qty)
            trades_today += 1
            sig_record["entered"] = True
            sig_record["skip_reason"] = None
            if signals is not None:
                signals.append(sig_record)

    return trades


# ── reporting ────────────────────────────────────────────────────────────

def _agg(trades: list) -> dict:
    """P&L aggregation from a plain trade list -- no account/equity involved,
    so it works the same whether given a whole window or one week's slice."""
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "gross": 0.0, "charges": 0.0, "net": 0.0}
    wins = sum(1 for t in trades if t["net_pnl_inr"] > 0)
    return {
        "trades": n,
        "win_rate": wins / n * 100,
        "gross": sum(t["pnl_inr"] for t in trades),
        "charges": sum(t["total_charges"] for t in trades),
        "net": sum(t["net_pnl_inr"] for t in trades),
    }


def _week_start(d):
    """Monday of the ISO week containing date d (d is a date, not datetime)."""
    return d - _td(days=d.weekday())


def classify_weekly(all_trades: dict, syms: list, max_weeks: int = 4) -> list:
    """Bucket every symbol's trades into ISO calendar weeks (Mon-Sun), most
    recent week first, capped at max_weeks. Each bucket carries a combined
    total plus a per-symbol breakdown, using the same _agg() as the overall
    window summary so the numbers are directly comparable."""
    flat = [t for trs in all_trades.values() for t in trs]
    by_week: dict = {}
    for t in flat:
        by_week.setdefault(_week_start(t["date"]), []).append(t)

    weeks = []
    for ws in sorted(by_week.keys(), reverse=True)[:max_weeks]:
        wk_trades = by_week[ws]
        weeks.append({
            "week_start": ws,
            "week_end": ws + _td(days=6),
            "total": _agg(wk_trades),
            "per_symbol": {sym: _agg([t for t in wk_trades if t["instrument"] == sym]) for sym in syms},
            "trades": sorted(wk_trades, key=lambda t: t["exit_time"]),
        })
    return weeks


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
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    s = account.snapshot()
    cost_note = f"   ({charges / gross:.2f}x gross)" if gross else ""
    wd_note = f", withdrew Rs {s['withdrawn']:,.0f}" if s["withdrawal_done"] else ""
    print(f"\n  -- {symbol} --------------------------------------------------")
    print(f"  trades         : {n}  over {days} trading day(s)")
    print(f"  win rate       : {wins / n * 100:.1f}%   ({wins}W / {n - wins}L)")
    print(f"  exits          : {reasons}")
    print(f"  gross P&L      : Rs {gross:+,.0f}")
    print(f"  total charges  : Rs {charges:,.0f}{cost_note}")
    print(f"  NET P&L        : Rs {net:+,.0f}")
    print(f"  ending equity  : Rs {s['equity']:,.0f}   (start Rs {s['initial']:,.0f}{wd_note})")
    print(f"  return on stake: {s['return_pct']:+.1f}%")
    return {"instrument": symbol, "trades": n, "win_rate": wins / n * 100,
            "gross": gross, "charges": charges, "net": net, "days": days}


def write_csv(symbol: str, trades: list):
    path = f"edge_1st_trades_{symbol}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for t in trades:
            w.writerow(t)
    return path


def _fmt(v) -> str:
    return f"{v:+,.0f}"


def _card_html(label: str, agg: dict) -> str:
    col = "#43D9AD" if agg["net"] >= 0 else "#f7768e"
    return (f"<div class=card><h2>{label}</h2>"
            f"<div class=big style='color:{col}'>Rs {_fmt(agg['net'])}</div>"
            f"<div class=sub>{agg['trades']} trades · {agg['win_rate']:.0f}% win · "
            f"gross Rs {_fmt(agg['gross'])} · charges Rs {agg['charges']:,.0f}</div></div>")


def _time_fmt(ts, compact: bool) -> str:
    """compact=True (single-day pages): just HH:MM:SS. Otherwise (tables
    spanning multiple days): date + time, dropping the always-:00 seconds
    and always-+05:30 offset that clutter the multi-day view."""
    return f"{ts:%H:%M:%S}" if compact else f"{ts:%Y-%m-%d %H:%M}"


def _trade_rows_html(trades: list, compact: bool = False) -> str:
    rows = ""
    for t in trades:
        col = "#43D9AD" if t["net_pnl_inr"] >= 0 else "#f7768e"
        rows += (f"<tr><td>{t['instrument']}</td><td>{t['direction']}</td>"
                 f"<td>{_time_fmt(t['entry_time'], compact)}</td><td>{t['entry_price']}</td>"
                 f"<td>{_time_fmt(t['exit_time'], compact)}</td><td>{t['exit_price']}</td>"
                 f"<td>{t['exit_reason']}</td><td>{t['quantity']}</td>"
                 f"<td style='color:{col}'>{t['net_pnl_inr']:+,.0f}</td></tr>")
    return rows


def _trade_table_html(trades: list, compact: bool = False, empty_msg: str = "No trades this week.") -> str:
    if not trades:
        return f"<p style='color:#8b949e;font-size:.85rem'>{empty_msg}</p>"
    return (f"<table><tr><th>Sym</th><th>Dir</th><th>Entry time</th><th>Entry</th>"
            f"<th>Exit time</th><th>Exit</th><th>Reason</th><th>Qty</th><th>Net Rs</th></tr>"
            f"{_trade_rows_html(trades, compact)}</table>")


def _signal_table_html(signals: list) -> str:
    if not signals:
        return "<p style='color:#8b949e;font-size:.85rem'>No signals generated today.</p>"
    rows = ""
    for s in sorted(signals, key=lambda x: x["time"]):
        if s["entered"]:
            status, col = "entered", "#43D9AD"
        else:
            status, col = s["skip_reason"], "#8b949e"
        rows += (f"<tr><td>{s['time']:%H:%M:%S}</td><td>{s['instrument']}</td>"
                 f"<td>{s['direction']}</td><td style='color:{col}'>{status}</td></tr>")
    return (f"<table><tr><th>Time</th><th>Sym</th><th>Dir</th><th>Outcome</th></tr>"
            f"{rows}</table>")


def _ohlc_card_html(symbol: str, o: dict) -> str:
    if o is None:
        return f"<div class=card><h2>{symbol}</h2><div class=sub>no data today</div></div>"
    chg = o["last"] - o["prev_close"]
    pct = (chg / o["prev_close"] * 100) if o["prev_close"] else 0.0
    col = "#43D9AD" if chg >= 0 else "#f7768e"
    return (f"<div class=card><h2>{symbol}</h2>"
            f"<div class=big style='color:{col}'>{o['last']:,.2f}</div>"
            f"<div class=sub style='color:{col}'>{chg:+,.2f} ({pct:+.2f}%) vs prev close</div>"
            f"<div class=sub>open {o['open']:,.2f} &middot; high {o['high']:,.2f} &middot; "
            f"low {o['low']:,.2f}</div></div>")


def write_dashboard(rows: list, all_trades: dict, window_desc: str, weekly: list = None):
    weekly = weekly or []
    total_cards = "".join(_card_html(r["instrument"], r) for r in rows)

    week_sections = ""
    for wk in weekly:
        heading = f"Week of {wk['week_start']:%b %d} &ndash; {wk['week_end']:%b %d}"
        wk_cards = "".join(_card_html(sym, agg) for sym, agg in wk["per_symbol"].items())
        wk_cards += _card_html("Combined", wk["total"])
        week_sections += (
            f"<h2 style='color:#8b949e;font-size:1rem;margin-top:32px'>{heading}</h2>"
            f"<div class=cards>{wk_cards}</div>"
            f"{_trade_table_html(wk['trades'])}"
        )

    html = f"""<!doctype html><html><head><meta charset=UTF-8>
<title>Edge 1st — last 4 weeks</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}}
h1{{margin:0 0 4px}} .meta{{color:#8b949e;margin-bottom:20px;font-size:.9rem}}
.cards{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px;min-width:220px}}
.card h2{{margin:0 0 8px;font-size:1rem;color:#8b949e}}
.big{{font-size:1.8rem;font-weight:700}} .sub{{color:#8b949e;font-size:.8rem;margin-top:6px}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;margin-bottom:8px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #21262d}} th{{color:#8b949e}}
a{{color:#58a6ff}}
</style></head><body>
<h1>Edge 1st &mdash; last 4 weeks</h1>
<div class=meta>First Edge strategy (archived Strategy 5) &middot; 1-minute exit model &middot;
full Upstox F&amp;O costs &middot; {window_desc} &middot; generated {_dt.now():%Y-%m-%d %H:%M} &middot;
paper only, no real orders &middot; <a href="today.html">today &rarr;</a> &middot;
<a href="full-history.html">full-history compounding backtest &rarr;</a></div>
<h2 style='color:#8b949e;font-size:1rem'>Last 4 weeks &mdash; total</h2>
<div class=cards>{total_cards}</div>
{week_sections}
</body></html>"""
    path = "edge_1st_dashboard.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def write_today_page(today_date, ohlc: dict, today_trades: dict, today_signals: dict):
    """The single most-recent trading day covered by this run: how
    NIFTY/BANKNIFTY moved, every signal the strategy generated (entered or
    not, see replay_week's docstring for exactly what that does and doesn't
    capture), and every trade actually taken -- with plain HH:MM:SS times
    so it's clear when each one was placed. Regenerated on the same
    schedule as the main dashboard (see .github/workflows), so this always
    reflects the most recent run, not necessarily today's calendar date if
    markets were closed since."""
    ohlc_cards = "".join(_ohlc_card_html(sym, ohlc.get(sym)) for sym in config.INSTRUMENTS)

    sections = ""
    for sym in config.INSTRUMENTS:
        sections += (
            f"<h2 style='color:#8b949e;font-size:1rem;margin-top:32px'>{sym}</h2>"
            f"<h3 style='color:#8b949e;font-size:.85rem;margin:16px 0 4px'>Signals generated</h3>"
            f"{_signal_table_html(today_signals.get(sym, []))}"
            f"<h3 style='color:#8b949e;font-size:.85rem;margin:16px 0 4px'>Trades taken</h3>"
            f"{_trade_table_html(today_trades.get(sym, []), compact=True, empty_msg='No trades today.')}"
        )

    html = f"""<!doctype html><html><head><meta charset=UTF-8>
<title>Edge 1st — today</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}}
h1{{margin:0 0 4px}} .meta{{color:#8b949e;margin-bottom:20px;font-size:.9rem}}
.cards{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px;min-width:220px}}
.card h2{{margin:0 0 8px;font-size:1rem;color:#8b949e}}
.big{{font-size:1.8rem;font-weight:700}} .sub{{color:#8b949e;font-size:.8rem;margin-top:6px}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;margin-bottom:8px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #21262d}} th{{color:#8b949e}}
a{{color:#58a6ff}}
</style></head><body>
<h1>Edge 1st &mdash; today</h1>
<div class=meta>{today_date:%A, %Y-%m-%d}, IST &middot; generated {_dt.now():%Y-%m-%d %H:%M} &middot;
refreshed on the same schedule as the main dashboard &middot; paper only, no real orders &middot;
<a href="edge_1st_dashboard.html">last 4 weeks &rarr;</a> &middot;
<a href="full-history.html">full-history compounding backtest &rarr;</a></div>
<div class=cards>{ohlc_cards}</div>
{sections}
</body></html>"""
    path = "today.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ── entrypoints ──────────────────────────────────────────────────────────

def run_week(only_symbol: str = None, days: int = None):
    # The dashboard is intentionally a rolling recent-weeks view. Keep an
    # accidental larger CLI value from pulling/displaying older history.
    days = config.INTRADAY_LOOKBACK_DAYS if days is None else min(max(int(days), 1), config.INTRADAY_LOOKBACK_DAYS)
    syms = [only_symbol] if only_symbol else list(config.INSTRUMENTS)
    rows, all_trades = [], {}
    all_signals: dict = {}
    today_ohlc: dict = {}
    win_lo = win_hi = None
    latest_date = None
    for sym in syms:
        print(f"\nfetching last {days} days of 1-min {sym} ...")
        df = market.get_week_1min(sym, days)
        if df.empty:
            print(f"  {sym}: no data returned (throttled, holiday, or feed unavailable).")
            # Replace any prior artifact so the published view cannot show an
            # older window's trades when this run's feed is empty.
            write_csv(sym, [])
            all_trades[sym] = []
            all_signals[sym] = []
            continue
        w0, w1 = df.index.min(), df.index.max()
        win_lo = w0 if win_lo is None else min(win_lo, w0)
        win_hi = w1 if win_hi is None else max(win_hi, w1)
        print(f"  {len(df):,} 1-min bars   {w0}  ->  {w1}")
        account = CapitalAccount(config.CAPITAL, "Rs ", config.WITHDRAWAL_MULTIPLE)
        prev_map = market.prev_day_ohlc_map(sym)
        sig_list: list = []
        trades = replay_week(sym, df, account, prev_map, signals=sig_list)
        rows.append(summarize(sym, trades, account))
        all_trades[sym] = trades
        all_signals[sym] = sig_list
        write_csv(sym, trades)

        sym_last_date = df.index.max().date()
        latest_date = sym_last_date if latest_date is None else max(latest_date, sym_last_date)
        day_bars = df[df.index.date == sym_last_date]
        if not day_bars.empty:
            prev_close = prev_map.get(sym_last_date, {}).get("close")
            today_ohlc[sym] = {
                "open": float(day_bars.iloc[0]["open"]), "high": float(day_bars["high"].max()),
                "low": float(day_bars["low"].min()), "last": float(day_bars.iloc[-1]["close"]),
                "prev_close": prev_close if prev_close is not None else float(day_bars.iloc[0]["open"]),
            }

    if not rows:
        print("\nNothing to report.")
        dash = write_dashboard([], all_trades, f"last {days} calendar days (no trades)")
        print(f"\nwrote {dash} (no trades in the recent window)")
        return

    tot_g = sum(r["gross"] for r in rows)
    tot_c = sum(r["charges"] for r in rows)
    tot_n = sum(r["net"] for r in rows)
    tot_t = sum(r["trades"] for r in rows)
    print("\n" + "=" * 64)
    print(f"  EDGE 1ST — LAST {days} DAYS TOTAL ({tot_t} trades)")
    print(f"  gross Rs {tot_g:+,.0f}   charges Rs {tot_c:,.0f}   NET Rs {tot_n:+,.0f}")
    print("=" * 64)

    weekly = classify_weekly(all_trades, syms, max_weeks=4)
    for wk in weekly:
        t = wk["total"]
        print(f"  week {wk['week_start']:%Y-%m-%d} - {wk['week_end']:%Y-%m-%d}: "
              f"{t['trades']} trades, NET Rs {t['net']:+,.0f}")

    desc = f"{win_lo:%Y-%m-%d} to {win_hi:%Y-%m-%d}" if win_lo is not None else f"last {days} calendar days"
    dash = write_dashboard(rows, all_trades, desc, weekly=weekly)
    print(f"\nwrote {dash}  +  edge_1st_trades_<SYM>.csv")

    if latest_date is not None:
        today_trades = {sym: [t for t in all_trades.get(sym, []) if t["date"] == latest_date] for sym in syms}
        today_signals = {sym: [s for s in all_signals.get(sym, []) if s["date"] == latest_date] for sym in syms}
        today_page = write_today_page(latest_date, today_ohlc, today_trades, today_signals)
        print(f"wrote {today_page}")
    if os.getenv("EDGE1ST_NO_BROWSER") != "1":
        try:
            webbrowser.open(os.path.abspath(dash))
        except Exception:
            pass


def run_live():
    """Forward paper loop — same 1-minute exit model. When the Upstox
    WebSocket feed (upstox_stream.py) is available, the current 1-min bar is
    updated live by the feed and this loop just re-evaluates the strategy
    against it every POLL_INTERVAL_SECONDS -- no network call in that path.
    Falls back to plain REST re-polling (the old behavior) if the feed
    can't connect (no token, Yahoo data source, etc)."""
    print("Edge 1st LIVE paper loop — Ctrl+C to stop. No real orders.\n")
    accounts = {s: CapitalAccount(config.CAPITAL, "Rs ", config.WITHDRAWAL_MULTIPLE)
                for s in config.INSTRUMENTS}
    seen = {s: set() for s in config.INSTRUMENTS}

    use_stream = _SRC == "upstox"
    if use_stream:
        try:
            import upstox_stream
            upstox_stream.start()
            if upstox_stream.wait_connected(timeout=10):
                print("live feed: connected -- reacting to bars as they stream in.\n")
            else:
                print("live feed: did not connect within 10s -- "
                      "continuing on REST polling only.\n")
        except Exception as e:
            print(f"live feed unavailable ({e}) -- continuing on REST polling only.\n")
            use_stream = False

    seed_cache: dict = {}
    prevday_cache: dict = {}
    cache_refreshed_at = 0.0
    SEED_REFRESH_SECONDS = 300  # history/prev-day OHLC barely changes intraday

    poll = 0
    while True:
        poll += 1
        now = _time.time()
        if now - cache_refreshed_at > SEED_REFRESH_SECONDS:
            for sym in config.INSTRUMENTS:
                try:
                    seed_cache[sym] = market.get_week_1min(sym, days=2)
                    prevday_cache[sym] = market.prev_day_ohlc_map(sym)
                except Exception as e:
                    print(f"[poll {poll}] {sym} seed refresh ERROR: {e}")
            cache_refreshed_at = now

        for sym in config.INSTRUMENTS:
            try:
                if use_stream:
                    df = upstox_stream.get_live_df(sym, seed=seed_cache.get(sym))
                else:
                    df = market.get_week_1min(sym, days=2)
                if df is None or df.empty:
                    print(f"[poll {poll}] {sym}: no data"); continue
                trades = replay_week(sym, df, accounts[sym], prevday_cache.get(sym, {}))
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
