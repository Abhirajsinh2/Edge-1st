"""
Shared compounding / withdrawal / position-sizing logic for every bot and
backtest in this project.

Two pieces:

  CapitalAccount  - a per-(strategy, instrument) working balance that
                    compounds on realised, post-fee P&L and performs a
                    ONE-TIME withdrawal: the first time the balance reaches
                    `withdrawal_multiple` x the original stake (default 2x),
                    the original stake amount is moved into `withdrawn` and
                    set aside forever. Everything left keeps compounding.
                    Withdrawn money is never re-entered into `equity` and is
                    never used for sizing.

  size_by_risk()  - "risk 1% of CURRENT equity" position sizing, capped by
                    buying power. If 1% risk rounds down to zero units, take
                    a single minimum unit as long as its worst-case loss is
                    within `max_risk_pct` of equity and buying power allows
                    it; otherwise return 0 (skip the trade).

Both the live paper engines and the historical backtests thread the SAME
CapitalAccount object through the run, so a paper-trading projection and a
backtest of the same strategy compound identically.
"""

from __future__ import annotations


class CapitalAccount:
    def __init__(self, initial: float, currency: str = "$", withdrawal_multiple: float = 2.0):
        self.initial = float(initial)
        self.currency = currency
        self.withdrawal_multiple = float(withdrawal_multiple)

        self.equity = float(initial)      # working balance - compounds, used for sizing
        self.peak = float(initial)        # high-water mark of the working balance
        self.withdrawn = 0.0              # set aside on the one-time withdrawal, NEVER reused
        self.withdrawal_done = False      # the withdrawal is one-time only
        self.withdrawal_trade_no = None   # 1-based index of the trade that triggered it

        self.realized_net = 0.0           # cumulative post-fee P&L booked to this account
        self.fees_paid = 0.0              # cumulative charges
        self.n_trades = 0

    # ------------------------------------------------------------------ #

    def book_trade(self, net_pnl: float, fees: float = 0.0) -> bool:
        """Apply one closed trade's NET (post-fee) P&L to the balance.
        Returns True iff this trade triggered the one-time withdrawal."""
        self.equity += net_pnl
        self.realized_net += net_pnl
        self.fees_paid += fees
        self.n_trades += 1
        if self.equity > self.peak:
            self.peak = self.equity

        triggered = False
        if (not self.withdrawal_done
                and self.equity >= self.withdrawal_multiple * self.initial):
            self.withdrawn = self.initial
            self.equity -= self.initial
            self.withdrawal_done = True
            self.withdrawal_trade_no = self.n_trades
            triggered = True
        return triggered

    # ------------------------------------------------------------------ #

    @property
    def reinvested_profit(self) -> float:
        """Profit currently at work in the market. Before the withdrawal this
        is just the running gain; after it, the whole working balance is
        profit (the original stake was pulled out)."""
        return self.equity - (self.initial - self.withdrawn)

    @property
    def total_value(self) -> float:
        """Working balance + money set aside. What the account is really worth."""
        return self.equity + self.withdrawn

    @property
    def return_pct(self) -> float:
        if self.initial <= 0:
            return 0.0
        return (self.total_value - self.initial) / self.initial * 100.0

    def snapshot(self) -> dict:
        return {
            "currency": self.currency,
            "initial": round(self.initial, 2),
            "equity": round(self.equity, 2),
            "peak": round(self.peak, 2),
            "withdrawn": round(self.withdrawn, 2),
            "reinvested_profit": round(self.reinvested_profit, 2),
            "total_value": round(self.total_value, 2),
            "fees_paid": round(self.fees_paid, 2),
            "realized_net": round(self.realized_net, 2),
            "withdrawal_done": self.withdrawal_done,
            "withdrawal_trade_no": self.withdrawal_trade_no,
            "return_pct": round(self.return_pct, 2),
            "n_trades": self.n_trades,
        }


# ---------------------------------------------------------------------- #


def size_by_risk(equity: float, entry_price: float, stop: float, risk_pct: float,
                 leverage: float, lot_size: int = 1, max_risk_pct: float = 0.03) -> int:
    """
    Position size in TOTAL UNITS (shares, or lots x lot_size for F&O).

      target   = risk_pct of `equity` lost if the stop is hit
      cap      = what `equity` x `leverage` of buying power can actually hold
      floor    = if the target rounds to 0 units, take ONE minimum unit
                 (1 share, or 1 lot) provided its worst-case loss is within
                 `max_risk_pct` of equity AND buying power can hold it

    `equity` here is always the CURRENT working balance of a CapitalAccount -
    never the original stake, never anything including withdrawn money.
    """
    stop_distance = abs(entry_price - stop)
    if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
        return 0

    risk_amount = equity * risk_pct
    buying_power = equity * max(leverage, 0.0)
    lot_size = max(int(lot_size), 1)

    if lot_size > 1:
        lots_by_risk = int(risk_amount / (stop_distance * lot_size))
        lots_by_bp = int(buying_power / (entry_price * lot_size))
        lots = min(lots_by_risk, lots_by_bp)
        if lots < 1:
            one_lot_loss = stop_distance * lot_size
            if lots_by_bp >= 1 and one_lot_loss <= equity * max_risk_pct:
                return lot_size
            return 0
        return lots * lot_size

    units_by_risk = int(risk_amount / stop_distance)
    units_by_bp = int(buying_power / entry_price)
    units = min(units_by_risk, units_by_bp)
    if units < 1:
        if units_by_bp >= 1 and stop_distance <= equity * max_risk_pct:
            return 1
        return 0
    return units


if __name__ == "__main__":
    acc = CapitalAccount(1_000, "$")
    for i in range(6):
        trig = acc.book_trade(300, 2)
        print(f"trade {i+1}: equity={acc.equity:.2f} withdrawn={acc.withdrawn:.2f} "
              f"{'<< WITHDRAWAL' if trig else ''}")
    print(acc.snapshot())
    print("size:", size_by_risk(1_000, 650, 647, 0.01, 4.0))
    print("size (india lot):", size_by_risk(100_000, 24_800, 24_750, 0.01, 10.0, lot_size=25))
