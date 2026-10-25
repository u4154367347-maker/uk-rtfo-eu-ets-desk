# EUA auction backtest: does strong auction demand predict later auction prices?

Data: EEX EUA primary auctions on the EU common platform, 1,948 auctions from 2012-11-13 to 2026-10-22 (country `EU`, contract `T3PA` as coded by EEX, cancelled auctions dropped).
Protocol: [`config/backtest.yaml`](../config/backtest.yaml), committed before any result was computed (see git history). Results: [`data/processed/eua_backtest.json`](../data/processed/eua_backtest.json), built by `python -m desk.backtest`.

![EUA backtest](eua_backtest.png)

## Result: a null result

**The signal does not pass its own test.** Out of sample (2023-01-09 to 2026-10-20, 547 auction steps), the tradable version earns +2.4bp per auction after a 10bp cost against +2.2bp for always being long, and the permutation p-value is 0.116, above the 0.05 the protocol required. The one apparent edge only exists in a version nobody can trade with this data.

| Out of sample, tradable (enter at the next auction) | Total net log return | Annualised Sharpe | Max drawdown |
|---|---|---|---|
| Always long | +0.119 | 0.10 | -47% |
| Signal long/flat, 0bp cost | +0.360 | 0.50 | -29% |
| Signal long/flat, 5bp | +0.246 | 0.34 | -31% |
| **Signal long/flat, 10bp (headline)** | **+0.131** | **0.18** | **-32%** |
| Signal long/flat, 20bp | -0.098 | -0.13 | -35% |
| Signal long/short, 10bp (secondary) | +0.141 | 0.12 | -32% |

Pre-registered decision rule, all three needed: net mean per auction above always long (yes, by 0.2bp, which is not a difference), permutation p below 0.05 (**no**, 0.116), drawdown no worse than always long (yes, but the signal is invested only 45% of the time). Verdict: not useful.

Gross, the signal is invested 45% of the time and wins 48% of the invested steps. It earns +14.8bp per step after strong demand and -7.9bp otherwise, a spread of 22.7bp that is not statistically distinguishable from luck (p = 0.116). In sample (2018-2022) the picture is the same: p = 0.147, and the signal underperforms always-long (+1.25 vs +2.26 total log return at 10bp) in a rising market.

## The version that looks good but cannot be traded

If you could buy at the auction price **after** seeing that auction's cover ratio, the same signal looks strong out of sample: +31.3bp per step after strong demand vs -21.3bp otherwise, p = 0.005, +0.534 total net at 10bp. This is the protocol's "diagnostic" timing and it was labelled optimistic in advance. The auction price is set before the result is known, so you can only enter it blind. The only price in the data is the auction clearing price, so I cannot test whether you could buy at about that level on the secondary market right after the result. That needs secondary or futures prices, which are not free public data.

Post-hoc and exploratory (not part of the decision): the rank correlation between relative cover ratio and the next step's return is 0.125 out of sample, but 0.001 for the step after. Whatever the signal carries is gone one auction later.

## What I would check next, and what I have not

- **Not confirmed:** whether the one-step effect is real market information or an artefact of auction-price timing noise. Both fit the data.
- **Not confirmed:** the 10bp cost. It is an assumption; the real secondary spread is not in the data. The result is shown at 0, 5, 10 and 20bp.
- Returns are changes between consecutive auction clearing prices, not secondary-market returns, and the auction is not an instrument you can trade at will.
- One signal, one lookback (60 auctions), one cut-off date, all fixed beforehand. No variant was tried and dropped. The long/short and diagnostic results are reported, not selected.
- The data is limited to the EU common platform. Aviation (EAA) auctions and other countries are excluded.
- Next step if this is pursued: free secondary prices (for example a public settlement series) to test the real entry. Not started.
