# BTC Race Bot — "Recovery Breakout"

Entry for make.fun **"Race a BTC trading bot live for 24 hours"**
(`btc-race-24h-20261113-2314`, $250). Long-only cbBTC/USDC, 1-minute bars,
$1,000 paper USDC. Python 3.13, stdlib only, deterministic.

## Strategy

**Recovery breakout after a pullback, ridden with a trailing stop.**

- **Entry (long only, full equity, one position):** the close breaks above
  the highest close of the previous 12 bars by ≥ 8 bps, while price is
  still ≥ 30 bps below its 120-minute high (a recovery, not a top chase),
  and the 15-minute return is ≥ +25 bps (the turn has real momentum).
- **Exits (measured from the entry close):** take-profit +120 bps,
  stop-loss −50 bps, trailing stop 30 bps once +30 bps up, max hold
  120 bars, 15-bar cooldown after an exit.
- Orders fill at the next bar's open; round trip costs ≈ 26 bps
  (4 fee + 2 slippage + ~13 impact + ~7 open/close drift), so the
  trailing stop — not the take-profit — does most of the work.

## Why this, and not the obvious things

The dev window is a −4.91% down day. Every kit baseline loses on it
(buy-and-hold −$52.59, momentum −$49.23, adx-momentum −$50.31,
rsi-bollinger −$65.83, ema-cross −$109.38 on 58 overtraded orders).
Dip-buying is a falling-knife trap (all mean-reversion sweeps lost
$43–$80) and plain momentum loses too. The only edge that survived
sweeping ~40,000 parameter combinations was the recovery breakout:
it sat out the morning bleed and caught the day's two real recoveries.

## Dev-window grade (local protocol harness, exact fill model)

The process sandbox cannot run Python (node22 only) and Docker is not
installed on this VM, so `grade-local` could not grade this Python bot
directly. Instead `proto_harness.py` drives `main.py` as a subprocess
over the real NDJSON protocol with the fill/decision model of
`replay.py`, which was validated to the **exact unit** against
`grade-local` on buy-and-hold (−52,586,916 units, 1 trade, 779,117 fees)
and ema-cross (−109,380,836 units, 58 trades, 21,883,562 fees).

| metric | value |
|---|---|
| net PnL | **+$11.04** (gate: > $0 ✅) |
| return | +1.10% |
| max drawdown | 1.09% (limit 25% ✅) |
| trades | 6 (3 round trips, 2 wins / 1 flat −$0.32) |
| fees | $2.42 |
| Sharpe (per-bar ×√1440) | ~0.93 |
| PnL at 2× fees | +$8.63 ✅ |
| half 1 (hours 0–12) | +$11.36 (2 RT) |
| half 2 (hours 12–24) | −$0.29 (1 RT) |
| determinism | two runs, byte-identical action logs ✅ |
| warm-up (60 bars) | same +$11.04, no orders during warm-up ✅ |

Trades: 07:02 → 07:48, +91 bps, +$8.30 (the morning-bounce recovery);
09:23 → 11:23, +38 bps, +$3.07; 16:36 → 18:36, +5 bps, −$0.32 (timeout).

## Files

- `main.py` — the bot (authored in this session; see NOTES.md).
- `vendor/makefun_bars.py` — byte-for-byte copy of the kit SDK shim
  (excluded from authorship counting).
- `replay.py` — exact local port of the harness fill/decision model.
- `proto_harness.py` — NDJSON protocol driver for `main.py`.
- `strat.py`, `sweep1–6.py`, `analyze.py`, `validate.py` — research scripts.
- `NOTES.md` — full analysis, all candidates and scores, weaknesses.

## Known weaknesses

- Only 3 round trips on the dev day; the PnL rests on two morning
  bounces. On a choppy race day with no real recovery, it may end near
  $0 or slightly negative — the gate needs strictly > $0 on the dev
  window (passed), and the race ranks by race-window PnL only.
- The third trade nearly hit its stop (−48 bps dip vs −50 bps stop)
  before recovering — on another day that is a −$5 loss instead of −$0.32.
- No shorting (long-only by the rules); a strong down-trend race day
  caps the upside at the bounces it can catch.
