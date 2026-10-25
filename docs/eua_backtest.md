# EUA auction backtest: does strong auction demand predict later auction prices?

Data: EEX EUA primary auctions on the EU common platform, 1,948 auctions from 2012-11-13 to 2026-10-22 (country `EU`, contract `T3PA` as coded by EEX, cancelled auctions dropped).
Protocol: [`config/backtest.yaml`](../config/backtest.yaml), committed in an earlier commit than the code and not changed since. Results: [`data/processed/eua_backtest.json`](../data/processed/eua_backtest.json), built by `python -m desk.backtest`.

![EUA backtest](eua_backtest.png)

## Result: a null result on the pre-registered rule

**The signal does not pass its own test.** Out of sample (2023-01-09 to 2026-10-20, 547 auction steps), the version with a one-auction delay earns +2.4bp per step after a 10bp cost against +2.2bp for always being long, and the permutation p-value is 0.116, above the 0.05 the protocol required. The one apparent edge needs look-ahead.

**What "return" means here.** The only price in the data is the auction clearing price, so a step return is the change between two consecutive auctions. Neither timing is executable as stated: you cannot sell into a primary auction, and exits and entries at auction prices need secondary or futures prices, which are not free public data. The delayed version is the one without look-ahead; it is a test of information, not a trading record.

| Out of sample, delayed entry (position set by the previous auction's signal) | Total net log return | Annualised Sharpe | Max drawdown |
|---|---|---|---|
| Always long | +0.119 | 0.10 | -47% |
| Signal long/flat, 0bp cost | +0.360 | 0.50 | -29% |
| Signal long/flat, 5bp | +0.246 | 0.34 | -31% |
| **Signal long/flat, 10bp (headline)** | **+0.131** | **0.18** | **-32%** |
| Signal long/flat, 20bp | -0.098 | -0.13 | -35% |
| Signal long/short, 10bp (secondary) | +0.141 | 0.12 | -32% |

Pre-registered decision rule, all three needed: net mean per step above always long (yes, by 0.2bp, which is not a meaningful difference), permutation p below 0.05 (**no**, 0.116), drawdown no worse than always long (yes, but the signal is invested only 45% of the time, so this check is nearly free). Verdict: not useful.

**Inconclusive, not proven absent.** After strong demand the next step averages +14.8bp, otherwise -7.9bp, a spread of 22.7bp. A block-bootstrap 95% interval for that spread is -17bp to +61bp, so the data cannot rule out a real effect of that size either. The signal wins 48% of its invested steps. In sample (2018-2022) it also fails (p = 0.147) and underperforms always-long (+1.25 vs +2.26 total log return at 10bp) in a rising market.

## The look-ahead version

If a position could be opened at the auction price **after** seeing that auction's cover ratio, the same signal looks strong out of sample: +31bp per step after strong demand vs -21bp otherwise, spread 52.6bp (95% interval +8bp to +102bp), p = 0.005, +0.534 total net at 10bp. This is the protocol's "diagnostic" timing, labelled optimistic in advance. Auction bids are placed before the result is known, so the position can only be opened at that price blind. Whether you could buy at about that level on the secondary market right after the result cannot be tested with this data.

## Robustness checks (added after review, post hoc, not part of the decision)

- **Permutation test is not like for like.** Free shuffles produce about 270 trades against 229 for the real signal, which flatters the p-value. Without costs the shuffle p is 0.148 for the delayed version; against circular shifts of the real positions (same trade count and persistence) it is 0.144. The conclusion does not change. The look-ahead version stays significant under both (0.008 and 0.004).
- **August.** Auction volume is cut in August, which mechanically raises the cover ratio: 73% of August signals out of sample are "strong" against 42% in other months. Dropping signals from August leaves the delayed spread at +27bp and the look-ahead spread at +56bp.
- **Largest steps.** Dropping the 3 largest absolute steps leaves the spreads at +19bp (delayed) and +57bp (look-ahead).
- **Persistence.** The rank correlation between relative cover ratio and the next step's return is 0.125 out of sample, and 0.001 for the step after. Whatever the signal carries is gone one auction later.

## Limits

- **Not confirmed:** whether the one-step effect is market information or auction-price timing noise. Both fit the data.
- **Not confirmed:** the 10bp cost, and whether cover ratio or price are ever revised after publication. The result is shown at 0, 5, 10 and 20bp.
- Pre-registration only goes as far as git history: the config was committed before the code and results, but the data already existed, and before fixing the protocol I had looked at yearly average cover ratios and prices (not at returns against the signal). The 2023 cut-off was chosen as the calendar year after a five-year in-sample window; I have not checked whether it matters.
- About 8 variants and 4 p-values are reported (2 timings x 2 positions, 2 periods), with no multiple-testing correction. Only one p-value is on the decision path.
- Steps are irregular (1 to 4 days, and about 3 weeks around Christmas), so "per auction" is not "per day", and the annualised Sharpe is approximate.
- One signal, one lookback (60 auctions), fixed beforehand; no variant was tried and dropped. EU common platform EUAs only; aviation (EAA) and other countries are excluded.
- Next step if pursued: a free secondary-price series to test a real entry and exit. Not started.
