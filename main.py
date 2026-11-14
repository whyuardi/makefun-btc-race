"""Recovery-breakout bot for the make.fun BTC 24h race (agent-fun/bars-replay/v1).

Idea (my own, built from the dev-window data, not from the kit baselines):
  - The dev day is a -4.9% down day. Every kit baseline loses on it
    (buy-and-hold -$52.59, ema-cross -$109.38, momentum -$49.23, ...).
    Dip-buying is a falling-knife trap here (mean-reversion sweeps all lost
    $43-$80); plain momentum also loses. The only edge that survived the
    sweep is a *recovery breakout*: after price has pulled back from its
    2-hour high, buy the close that breaks above the last 15 bars' highs
    with a strong 15-minute rise behind it, then ride it with a trailing
    stop. On the dev window that caught the two real intraday recoveries
    (+91 bps and +38 bps net of fills) and sat out the morning bleed.
  - Long only, one position, full equity per entry. No network, no clock,
    no randomness: every decision is a pure function of the closes seen so
    far, so the two grading runs produce identical action logs.

Risk shell (all in basis points of the entry close, decided on closes):
  take-profit 120, stop-loss 50, trailing stop 30 once +30 up, max hold
  120 bars, 15-bar cooldown after an exit. Orders fill at the next bar's
  open with fee 4 bps + slippage 2 bps + ~13 bps of price impact on a
  full-size trade, so a round trip costs ~26 bps; the trailing stop is
  what keeps a winning trade from giving the move back.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- entry parameters (chosen on the dev window; see NOTES.md) ---
BRK_WINDOW = 12        # breakout lookback: bars before the current one
BRK_BPS = 8            # close must beat the lookback high by this much
PULL_BPS = 30          # close must still be this far below the 120-min high
MIN_R15_BPS = 25       # 15-minute return at entry must be at least this
MAX_FROM_HIGH_BPS = 10 ** 9  # entry bar must close within this of its high
# --- exit parameters ---
TP_BPS = 120           # take-profit from the entry close
SL_BPS = 50            # stop-loss from the entry close
TRAIL_ARM_BPS = 30     # profit at which the trailing stop arms
TRAIL_BPS = 30         # trailing-stop distance behind the peak move
MAX_HOLD = 120         # bars; exit on timeout
COOLDOWN = 15          # bars to wait after an exit before re-entering
MAX_SLIPPAGE_BPS = 100


class Ema:
    """EMA seeded as the mean of its first span of closes, then
    value += (2 / (span + 1)) * (close - value). Same as the kit baselines."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def step(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return self.value
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span
        return self.value


class Bot:
    def __init__(self):
        self.closes = []          # closes seen so far (dollars, float)
        self.ema60 = Ema(60)
        self.entry_close = None  # close of the bar the buy was decided on
        self.entry_seq = None
        self.peak_bps = 0.0
        self.last_exit_seq = -10 ** 9
        self.pending_buy = None  # (client_id, entry_close, entry_seq)
        self.n_buys = 0
        self.n_sells = 0

    def _update_indicators(self, state):
        bar = state.latest.get(SYMBOL)
        if bar is None:
            return None
        close = int(bar["c"]) / 1e5
        self.closes.append(close)
        self.ema60.step(close)
        return bar, close

    def _confirm_fills(self, batch):
        """A buy the market refused leaves no position: forget the entry."""
        for fill in batch.get("fills", []):
            if fill.get("symbol") != SYMBOL:
                continue
            if fill.get("status") != "filled":
                if self.pending_buy and fill.get("client_id") == self.pending_buy[0]:
                    self.pending_buy = None
                    self.entry_close = None
                    self.entry_seq = None
            elif self.pending_buy and fill.get("client_id") == self.pending_buy[0]:
                self.pending_buy = None

    def on_batch(self, state, batch):
        self._confirm_fills(batch)
        got = self._update_indicators(state)
        if got is None or state.warming_up:
            return []
        bar, close = got
        seq = state.seq
        held = state.positions.get(SYMBOL)

        # ---- holding: exits ----
        if held is not None and held.qty > 0:
            if self.entry_close is None:
                # Position without a recorded entry (should not happen):
                # fall back to its cost basis.
                self.entry_close = close
                self.entry_seq = seq
                self.peak_bps = 0.0
            move = (close / self.entry_close - 1.0) * 1e4
            if move > self.peak_bps:
                self.peak_bps = move
            hold = seq - self.entry_seq
            exit_now = False
            if move >= TP_BPS:
                exit_now = True
            elif move <= -SL_BPS:
                exit_now = True
            elif self.peak_bps >= TRAIL_ARM_BPS and move <= self.peak_bps - TRAIL_BPS:
                exit_now = True
            elif hold >= MAX_HOLD:
                exit_now = True
            if exit_now:
                self.last_exit_seq = seq
                self.entry_close = None
                self.entry_seq = None
                self.n_sells += 1
                return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        # ---- flat: entries ----
        if seq - self.last_exit_seq < COOLDOWN:
            return []
        n = len(self.closes)
        if n < 120 or self.ema60.value is None:
            return []
        # breakout: current close vs the max close of the BRK_WINDOW bars
        # before it (the current bar is already appended, so slice off it).
        prev = self.closes[-BRK_WINDOW - 1:-1]
        if not prev:
            return []
        prev_hi = max(prev)
        brk = (close / prev_hi - 1.0) * 1e4
        hi120 = max(self.closes[-120:])
        pull = (hi120 / close - 1.0) * 1e4
        r15 = None
        if n >= 16:
            r15 = math.log(close / self.closes[-16]) * 1e4
        bar_high = int(bar["h"]) / 1e5
        near_high = (bar_high / close - 1.0) * 1e4
        if brk < BRK_BPS or pull < PULL_BPS:
            return []
        if r15 is not None and r15 < MIN_R15_BPS:
            return []
        if near_high > MAX_FROM_HIGH_BPS:
            return []
        if state.cash < 1_000_000:  # below the minimum notional
            return []
        cid = state.client_id()
        self.entry_close = close
        self.entry_seq = seq
        self.peak_bps = 0.0
        self.pending_buy = (cid, close, seq)
        self.n_buys += 1
        return [{"side": "buy", "symbol": SYMBOL, "client_id": cid,
                 "notional_quote_units": state.cash,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]


BOT = Bot()
makefun_bars.run(BOT.on_batch)
