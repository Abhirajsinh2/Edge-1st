"""
Builds full-history.html from full_history_results.json -- same static,
JS-free style as edge_1st_bot.py's write_dashboard() (that file stays
untouched; this is a separate one-off/periodic report, not part of the
scheduled 4-week refresh).
"""

import json
from datetime import datetime as dt

with open("full_history_results.json", encoding="utf-8") as f:
    results = json.load(f)

COLORS = {"NIFTY": "#58a6ff", "BANKNIFTY": "#d29922"}


def fmt(v):
    return f"{v:+,.0f}"


def card_html(label, summary, account):
    col = "#43D9AD" if summary["net"] >= 0 else "#f7768e"
    return (
        f"<div class=card><h2>{label}</h2>"
        f"<div class=big style='color:{col}'>{account['return_pct']:+.1f}%</div>"
        f"<div class=sub>Rs {account['initial']:,.0f} &rarr; Rs {account['equity']:,.0f} "
        f"&middot; {summary['trades']} trades &middot; {summary['win_rate']:.0f}% win</div>"
        f"<div class=sub>gross Rs {fmt(summary['gross'])} &middot; "
        f"charges Rs {summary['charges']:,.0f} &middot; NET Rs {fmt(summary['net'])}</div></div>"
    )


def equity_curve_svg(all_trades_by_sym):
    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 900, 300, 70, 20, 16, 30
    series = {}
    max_len = 0
    for sym, trades in all_trades_by_sym.items():
        pts = [(0, trades[0]["equity_before"])] if trades else [(0, 0)]
        for i, t in enumerate(trades, start=1):
            pts.append((i, t["equity_after"]))
        series[sym] = pts
        max_len = max(max_len, len(pts))

    all_vals = [v for pts in series.values() for _, v in pts]
    min_v, max_v = min(all_vals), max(all_vals)
    span_v = (max_v - min_v) or 1

    def x(i):
        return PAD_L + (i / max(1, max_len - 1)) * (W - PAD_L - PAD_R)

    def y(v):
        return PAD_T + (1 - (v - min_v) / span_v) * (H - PAD_T - PAD_B)

    grid, labels = "", ""
    for g in range(5):
        gy = PAD_T + (g / 4) * (H - PAD_T - PAD_B)
        v = max_v - (g / 4) * span_v
        grid += f"<line x1='{PAD_L}' y1='{gy}' x2='{W-PAD_R}' y2='{gy}' stroke='#21262d' stroke-width='1'/>"
        labels += f"<text x='4' y='{gy+4}' font-size='11' fill='#8b949e'>{v:,.0f}</text>"

    paths, legend = "", ""
    for sym, pts in series.items():
        color = COLORS.get(sym, "#c9d1d9")
        d = "M " + " L ".join(f"{x(i):.1f} {y(v):.1f}" for i, v in pts)
        paths += f"<path d='{d}' fill='none' stroke='{color}' stroke-width='2'/>"
        legend += f"<span style='color:{color}'>&#9632;</span> {sym}&nbsp;&nbsp;&nbsp;"

    svg = f"<svg viewBox='0 0 {W} {H}' style='width:100%;height:auto'>{grid}{paths}</svg>"
    return f"<div class='sub' style='margin-bottom:8px'>{legend}</div>{svg}"


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
        rows += (
            f"<tr><td>{t['date']}</td><td>{t['direction']}</td>"
            f"<td>{t['entry_price']}</td><td>{t['exit_price']}</td>"
            f"<td>{t['exit_reason']}</td><td>{t['quantity']}</td>"
            f"<td>{t['equity_before']:,.0f}</td>"
            f"<td style='color:{col}'>{t['net_pnl_inr']:+,.0f}</td>"
            f"<td>{t['equity_after']:,.0f}</td></tr>"
        )
    return rows


total_cards = "".join(
    card_html(sym, r["summary"], r["account"]) for sym, r in results.items()
)

combined_initial = sum(r["account"]["initial"] for r in results.values())
combined_final = sum(r["account"]["equity"] for r in results.values())
combined_net = sum(r["summary"]["net"] for r in results.values())
combined_trades = sum(r["summary"]["trades"] for r in results.values())
combined_return = (combined_final - combined_initial) / combined_initial * 100 if combined_initial else 0

combined_card = (
    f"<div class=card style='border-color:#58a6ff'><h2>Combined</h2>"
    f"<div class=big style='color:{'#43D9AD' if combined_net >= 0 else '#f7768e'}'>{combined_return:+.1f}%</div>"
    f"<div class=sub>Rs {combined_initial:,.0f} &rarr; Rs {combined_final:,.0f} &middot; "
    f"{combined_trades} trades &middot; NET Rs {fmt(combined_net)}</div></div>"
)

all_trades_by_sym = {sym: r["trades"] for sym, r in results.items()}
chart_html = equity_curve_svg(all_trades_by_sym)

sections = ""
for sym, r in results.items():
    sections += (
        f"<h2 style='color:#8b949e;font-size:1rem;margin-top:32px'>{sym} &mdash; all trades</h2>"
        f"{lot_growth_note(sym, r['trades'])}"
        f"<table><tr><th>Date</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Reason</th>"
        f"<th>Qty</th><th>Equity Before</th><th>Net Rs</th><th>Equity After</th></tr>"
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
<p><a href="index.html">&larr; back to last-4-weeks dashboard</a></p>
<h1>Edge 1st &mdash; full history, compounding</h1>
<div class=meta>Every trade sizes off the CURRENT account balance (1% risk + margin cap) &middot;
no capital added or removed mid-run, one CapitalAccount per instrument compounds start to finish &middot;
full Upstox F&amp;O costs &middot; generated {dt.now():%Y-%m-%d %H:%M} &middot; paper only, no real orders</div>
<div class=cards>{total_cards}{combined_card}</div>
<div class=chart-card>
<h2 style='color:#8b949e;font-size:1rem;margin-top:0'>Equity curve (per trade, compounding)</h2>
{chart_html}
</div>
{sections}
</body></html>"""

with open("full-history.html", "w", encoding="utf-8") as f:
    f.write(html)

print("wrote full-history.html")
