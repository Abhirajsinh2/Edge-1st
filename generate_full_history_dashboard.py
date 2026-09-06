"""
Builds full-history.html from full_history_results.json -- same static,
JS-free style as edge_1st_bot.py's write_dashboard() (that file stays
untouched; this is a separate one-off/periodic report, not part of the
scheduled 4-week refresh).

The equity curve is plotted against CALENDAR TIME (first trade -> last trade),
not trade index, and the one-time 2x-stake withdrawal is drawn on the curve
and spelled out on every card.
"""

import json
from datetime import datetime as dt

with open("full_history_results.json", encoding="utf-8") as f:
    results = json.load(f)

COLORS = {"NIFTY": "#58a6ff", "BANKNIFTY": "#d29922"}


def fmt(v):
    return f"{v:+,.0f}"


def _pdate(s):
    """Parse an ISO date/datetime string from the results JSON to a datetime."""
    return dt.fromisoformat(str(s))


def _naive(d):
    """Drop tzinfo so date-only and tz-aware timestamps compare cleanly
    (the whole series is a single IST session run)."""
    return d.replace(tzinfo=None)


def date_range(trades):
    if not trades:
        return None, None
    return _pdate(trades[0]["date"]), _pdate(trades[-1]["date"])


def withdrawal_of(trades, account):
    """(-> amount, date_str, trade_no) for the one-time withdrawal, or None."""
    if not account.get("withdrawal_done"):
        return None
    n = account.get("withdrawal_trade_no")
    hit = None
    for i, t in enumerate(trades, start=1):
        if t.get("withdrawal_here"):
            hit = (i, t)
            break
    if hit is None and n and n <= len(trades):
        hit = (n, trades[n - 1])
    if hit is None:
        return account["withdrawn"], None, n
    i, t = hit
    return account["withdrawn"], _pdate(t["date"]).strftime("%Y-%m-%d"), i


def card_html(label, summary, account, trades):
    col = "#43D9AD" if summary["net"] >= 0 else "#f7768e"
    d0, d1 = date_range(trades)
    span = (f"{d0:%b %Y} &rarr; {d1:%b %Y}" if d0 else "&mdash;")
    wd = withdrawal_of(trades, account)
    if wd:
        amt, wdate, wno = wd
        wtxt = (f"Withdrew Rs {amt:,.0f} once (2&times; stake reached) &middot; "
                f"trade #{wno}, {wdate}")
    else:
        wtxt = "No withdrawal &mdash; equity never reached 2&times; stake"
    return (
        f"<div class=card><h2>{label}</h2>"
        f"<div class=big style='color:{col}'>{account['return_pct']:+.1f}%</div>"
        f"<div class=sub>{span} &middot; {summary['trades']} trades &middot; {summary['win_rate']:.0f}% win</div>"
        f"<div class=sub>Rs {account['initial']:,.0f} start &rarr; "
        f"equity Rs {account['equity']:,.0f} + withdrawn Rs {account['withdrawn']:,.0f} "
        f"= total Rs {account['total_value']:,.0f}</div>"
        f"<div class=sub>{wtxt}</div>"
        f"<div class=sub>gross Rs {fmt(summary['gross'])} &middot; "
        f"charges Rs {summary['charges']:,.0f} &middot; NET Rs {fmt(summary['net'])}</div></div>"
    )


def equity_curve_svg(results):
    """Total account value (equity + withdrawn-to-date) vs calendar time.
    Plotting total value keeps the line continuous through the one-time
    withdrawal; the withdrawal instant is still marked with a dot + label."""
    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 900, 320, 78, 20, 16, 46

    series, marks = {}, {}
    for sym, r in results.items():
        trades = r["trades"]
        if not trades:
            continue
        pts = [(_naive(_pdate(trades[0]["entry_time"])), trades[0]["equity_before"])]
        for t in trades:
            tv = t["equity_after"] + t.get("withdrawn_to_date", 0.0)
            et = _naive(_pdate(t["exit_time"]))
            pts.append((et, tv))
            if t.get("withdrawal_here"):
                marks[sym] = (et, tv, t.get("withdrawn_to_date", 0.0))
        series[sym] = pts

    if not series:
        return "<p class=sub>No trades to plot.</p>"

    all_t = [t for pts in series.values() for t, _ in pts]
    all_v = [v for pts in series.values() for _, v in pts]
    t_min, t_max = min(all_t), max(all_t)
    span_t = (t_max - t_min).total_seconds() or 1.0
    min_v, max_v = min(all_v), max(all_v)
    pad_v = (max_v - min_v) * 0.06 or 1.0
    min_v -= pad_v
    max_v += pad_v
    span_v = (max_v - min_v) or 1.0

    def x(t):
        return PAD_L + ((t - t_min).total_seconds() / span_t) * (W - PAD_L - PAD_R)

    def y(v):
        return PAD_T + (1 - (v - min_v) / span_v) * (H - PAD_T - PAD_B)

    # horizontal value grid
    grid = ""
    for g in range(5):
        gy = PAD_T + (g / 4) * (H - PAD_T - PAD_B)
        v = max_v - (g / 4) * span_v
        grid += f"<line x1='{PAD_L}' y1='{gy:.1f}' x2='{W-PAD_R}' y2='{gy:.1f}' stroke='#21262d'/>"
        grid += f"<text x='6' y='{gy+4:.1f}' font-size='11' fill='#8b949e'>Rs {v:,.0f}</text>"

    # vertical date grid
    for g in range(6):
        frac = g / 5
        gx = PAD_L + frac * (W - PAD_L - PAD_R)
        tt = t_min + (t_max - t_min) * frac
        grid += f"<line x1='{gx:.1f}' y1='{PAD_T}' x2='{gx:.1f}' y2='{H-PAD_B}' stroke='#21262d'/>"
        anchor = "start" if g == 0 else ("end" if g == 5 else "middle")
        grid += (f"<text x='{gx:.1f}' y='{H-PAD_B+18:.1f}' font-size='11' fill='#8b949e' "
                 f"text-anchor='{anchor}'>{tt:%b %Y}</text>")

    paths, dots, legend = "", "", ""
    for sym, pts in series.items():
        color = COLORS.get(sym, "#c9d1d9")
        d = "M " + " L ".join(f"{x(t):.1f} {y(v):.1f}" for t, v in pts)
        paths += f"<path d='{d}' fill='none' stroke='{color}' stroke-width='2'/>"
        d0, d1 = pts[0][0], pts[-1][0]
        legend += (f"<span style='color:{color}'>&#9632;</span> {sym} "
                   f"({d0:%d %b %Y} &rarr; {d1:%d %b %Y})&nbsp;&nbsp;&nbsp;")
        m = marks.get(sym)
        if m:
            mt, mv, mamt = m
            mx, my = x(mt), y(mv)
            dots += (f"<line x1='{mx:.1f}' y1='{PAD_T}' x2='{mx:.1f}' y2='{H-PAD_B}' "
                     f"stroke='{color}' stroke-dasharray='3 3' opacity='0.5'/>")
            dots += f"<circle cx='{mx:.1f}' cy='{my:.1f}' r='4' fill='{color}'/>"
            lx = min(mx + 8, W - PAD_R - 150)
            dots += (f"<text x='{lx:.1f}' y='{my-8:.1f}' font-size='10.5' fill='{color}'>"
                     f"&minus;Rs {mamt:,.0f} withdrawn ({mt:%d %b %Y})</text>")

    svg = (f"<svg viewBox='0 0 {W} {H}' style='width:100%;height:auto'>"
           f"{grid}{paths}{dots}</svg>")
    note = ("Line = equity + withdrawn-to-date (total account value), so it stays "
            "continuous through the one-time withdrawal. Dot / dashed line = the "
            "moment the original stake was pulled out.")
    return (f"<div class='sub' style='margin-bottom:6px'>{legend}</div>"
            f"{svg}"
            f"<div class='sub' style='margin-top:6px'>{note}</div>")


def lot_growth_note(sym, trades):
    if not trades:
        return ""
    first, last = trades[0], trades[-1]
    lot_sizes_seen = sorted({t["quantity"] for t in trades})
    if len(lot_sizes_seen) > 1:
        detail = (f"position size actually stepped up during this run, from "
                  f"{lot_sizes_seen[0]} to {lot_sizes_seen[-1]} qty, as equity grew.")
    else:
        detail = (f"position size stayed at {lot_sizes_seen[0]} qty (1 lot) the whole run -- "
                  f"1%-risk-per-trade sizing needs roughly double the equity to justify a 2nd "
                  f"lot given this strategy's typical stop distances, and this window's "
                  f"+{(last['equity_after']/first['equity_before']-1)*100:.0f}% growth didn't cross that line. "
                  f"Sizing does compound (risk amount and buying-power cap both scale with equity every "
                  f"trade) -- it just hadn't grown enough yet to round up to a 2nd lot.")
    return (
        f"<div class=sub>First trade: {first['quantity']} qty at equity Rs {first['equity_before']:,.0f} "
        f"&rarr; Last trade: {last['quantity']} qty at equity Rs {last['equity_before']:,.0f}. {detail}</div>"
    )


def trade_rows_html(sym, trades):
    rows = ""
    for t in trades:
        col = "#43D9AD" if t["net_pnl_inr"] >= 0 else "#f7768e"
        wd = " &bull;" if t.get("withdrawal_here") else ""
        rows += (
            f"<tr><td>{t['date']}{wd}</td><td>{t['direction']}</td>"
            f"<td>{t['entry_price']}</td><td>{t['exit_price']}</td>"
            f"<td>{t['exit_reason']}</td><td>{t['quantity']}</td>"
            f"<td>{t['equity_before']:,.0f}</td>"
            f"<td style='color:{col}'>{t['net_pnl_inr']:+,.0f}</td>"
            f"<td>{t['equity_after']:,.0f}</td>"
            f"<td>{t.get('withdrawn_to_date', 0):,.0f}</td></tr>"
        )
    return rows


total_cards = "".join(
    card_html(sym, r["summary"], r["account"], r["trades"]) for sym, r in results.items()
)

combined_initial = sum(r["account"]["initial"] for r in results.values())
combined_equity = sum(r["account"]["equity"] for r in results.values())
combined_withdrawn = sum(r["account"]["withdrawn"] for r in results.values())
combined_total = combined_equity + combined_withdrawn
combined_net = sum(r["summary"]["net"] for r in results.values())
combined_trades = sum(r["summary"]["trades"] for r in results.values())
combined_return = (combined_total - combined_initial) / combined_initial * 100 if combined_initial else 0

_alltrades = [t for r in results.values() for t in r["trades"]]
_d0 = min((_pdate(t["date"]) for t in _alltrades), default=None)
_d1 = max((_pdate(t["date"]) for t in _alltrades), default=None)
_span = f"{_d0:%d %b %Y} &rarr; {_d1:%d %b %Y}" if _d0 else "&mdash;"

combined_card = (
    f"<div class=card style='border-color:#58a6ff'><h2>Combined</h2>"
    f"<div class=big style='color:{'#43D9AD' if combined_net >= 0 else '#f7768e'}'>{combined_return:+.1f}%</div>"
    f"<div class=sub>{_span} &middot; {combined_trades} trades</div>"
    f"<div class=sub>Rs {combined_initial:,.0f} start &rarr; equity Rs {combined_equity:,.0f} "
    f"+ withdrawn Rs {combined_withdrawn:,.0f} = total Rs {combined_total:,.0f}</div>"
    f"<div class=sub>NET Rs {fmt(combined_net)}</div></div>"
)

chart_html = equity_curve_svg(results)

sections = ""
for sym, r in results.items():
    sections += (
        f"<h2 style='color:#8b949e;font-size:1rem;margin-top:32px'>{sym} &mdash; all trades</h2>"
        f"{lot_growth_note(sym, r['trades'])}"
        f"<table><tr><th>Date</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Reason</th>"
        f"<th>Qty</th><th>Equity Before</th><th>Net Rs</th><th>Equity After</th><th>Withdrawn</th></tr>"
        f"{trade_rows_html(sym, r['trades'])}</table>"
    )

html = f"""<!doctype html><html><head><meta charset=UTF-8>
<title>Edge 1st — full history</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}}
h1{{margin:0 0 4px}} .meta{{color:#8b949e;margin-bottom:20px;font-size:.9rem}}
a{{color:#58a6ff}}
.cards{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px;min-width:240px}}
.card h2{{margin:0 0 8px;font-size:1rem;color:#8b949e}}
.big{{font-size:1.8rem;font-weight:700}} .sub{{color:#8b949e;font-size:.8rem;margin-top:6px}}
.chart-card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px;margin-bottom:20px}}
table{{width:100%;border-collapse:collapse;font-size:.8rem;margin-bottom:8px}}
th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid #21262d}} th{{color:#8b949e}}
</style></head><body>
<p><a href="index.html">&larr; back to last-4-weeks dashboard</a> &middot; <a href="today.html">today &rarr;</a></p>
<h1>Edge 1st &mdash; full history, compounding</h1>
<div class=meta>Every trade sizes off the CURRENT account balance (1% risk + margin cap) &middot;
one CapitalAccount per instrument compounds start to finish, with the single one-time
2&times;-stake withdrawal &middot; full Upstox F&amp;O costs &middot; window {_span} &middot;
generated {dt.now():%Y-%m-%d %H:%M} &middot; paper only, no real orders</div>
<div class=cards>{total_cards}{combined_card}</div>
<div class=chart-card>
<h2 style='color:#8b949e;font-size:1rem;margin-top:0'>Total account value over time (compounding)</h2>
{chart_html}
</div>
{sections}
</body></html>"""

with open("full-history.html", "w", encoding="utf-8") as f:
    f.write(html)

print("wrote full-history.html")
