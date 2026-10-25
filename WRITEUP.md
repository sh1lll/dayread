# Can an intraday futures session be forecast? A project write-up

*Day Read research, September to October 2026. This covers methods and results. Inputs, thresholds and model settings are left out on purpose.*

## Summary

The project began with the claim that trading is just gambling. The goal set against it was ambitious: forecast a Nasdaq-100 futures session well enough to say before the open something like "at 09:41 ±2 minutes price tags 25,592, pokes about 8 points through, holds, and runs 60+", and trade those calls at about 65% wins with a 3-to-1 reward-to-risk ratio.

Over several weeks and roughly 470 logged decisions and experiments, some parts of that turned out to be forecastable and others didn't. The forecastable parts are how large and busy the open will be, which nearby prices get reached and roughly when, and how a reaction candle finishes once it has started. With the inputs tested, nothing forecast which way the day goes, whether a level holds before price arrives, or the exact minute more than a few minutes ahead. The best verified setup wins about 35% of the time at 3-to-1, about +0.3R per trade. That is slightly profitable on paper, well short of the 65% target, and not proven with real money.

## 1. The question, broken into parts

The forecast was split into five parts and worked on in this order:

| Part | Question |
|---|---|
| Bias | How will the open and the day play out: size, volume, the first push and its fake-out, then the real move? |
| Where | Which price will trade, and does anything happen there? |
| When | At what minute? A fixed time of day doesn't count. |
| How | What does the reaction candle do: speed, wick, close, hold or break? |
| Payoff | Can it be traded at a high win rate, with a setup most days? |

## 2. Data

| Data | Coverage |
|---|---|
| Nasdaq-100 futures (NQ), every trade with aggressor side and top-of-book | Aug 2020 to Oct 2026, about 1,500 sessions |
| NQ one-minute bars | 2014 to 2020, about 1,500 more sessions |
| S&P 500 futures (ES), every trade | about 240 sessions, used only as an independent check |
| Daily context | index volatility (VXN, VIX), dealer-gamma and dark-pool indices, an economic-release calendar |

Distances are measured in units of the average daily range (ATR), which makes a 2014 day comparable with a 2026 day.

## 3. How ideas were tested

A Python research harness turned each idea into a rule it could check on every day in the data. For each rule it found every case where the rule applied, scored each case the same way, and repeated the test on controls. The ideas came from level theories, order flow, market-profile teaching, timing theories and published strategies. About 120 trading PDFs and course videos alone supplied around 1,500 claims to check.

These checks decided what survived:

- **Pre-registration.** The data, the measurement and the pass mark were written down and timestamped before each test ran. A result that fell just short of the mark counted as a failure.
- **Base rates.** With a target three times the stop, random entries win about 25%, so 25%, not 50%, is the bar to beat.
- **Placebo controls.** Every "important" price was compared with made-up prices at a similar distance. Circular time shifts, which break any real relationship, gave a pure-luck benchmark, and the same tests also ran on simulated random-walk prices.
- **Three periods and a second market.** A result had to appear in 2014-19, 2020-23 and 2024 onward, and point the same way on ES.
- **Cross-validation and a sealed holdout.** Ideas were built with leave-one-year-out cross-validation on 2020-23. Data from 2024 onward stayed locked until a rule had passed everything else, and was then opened once.
- **Multiple-testing control.** Benjamini-Hochberg false-discovery control, a deflated Sharpe ratio, and shuffle tests in which a sweep's pass count had to beat the 95th percentile of 200 shuffled runs.
- **Uncertainty.** Every payoff figure carries a bootstrap confidence interval from 2,000 resamples.
- **Tick re-checks.** Anything found on one-minute bars was re-run on trade-by-trade data before it was believed.
- **Forward grading.** Rules that passed went into a live daily brief, which writes its calls down in advance and grades them automatically after the close.

Models used along the way:
- Monte Carlo simulation of a two-barrier race (which price is reached first).
- First-passage, gambler's-ruin and reflection-principle probabilities as random-walk baselines.
- GARCH and FIGARCH volatility models.
- Gradient-boosted trees, logistic and ridge regression.
- Quantile regression with conformal bands.
- A discrete-time hazard model for arrival time.
- Nearest-neighbour "analog days" and neural networks, both tested and rejected.

## 4. What held up

These figures come from data the rule was not fit on.

| Finding | Result |
|---|---|
| Size of the first 30 minutes, forecast before the open | rank correlation ≈ 0.55 (2024 onward), ≈ 0.54 on 2014-20 |
| Volume of the first 30 minutes | ≈ 0.59, vs ≈ 0.32 for a plain recent average |
| Which overnight extreme trades first | ≈ 65% overall; 88-90% when one is clearly nearer |
| Whether the opening dip extends before the push | "likely" ≈ 69-79% vs "done" ≈ 34-36%; same on ES |
| Same-day gap fill, by gap size | 53-61% small, 26-40% medium, 11-17% large; stable for 12 years |
| Ranking nearby prices by chance of trading by 10:30 | AUC ≈ 0.85 |
| "This price trades within 60 seconds" | right ≈ 71%, 2-3 calls a day |
| Arrival minute, within ±2 minutes | ≈ 73% when 0-2 minutes out; 23-39% at 3-5 minutes; under 20% beyond |
| How a touch candle closes, read part-way through | ≈ 79% right 10 seconds in, ≈ 91% at 20 seconds (9,232 touches, tick data) |
| Speed after the candle closes | the fast tier reaches its target within 3 minutes ≈ 60% of the time, the slow tier ≈ 9%; confirmed on ES |
| Yesterday's low | holds 42-48% vs ≈ 50% at nearby prices, so it tends to break; true in every period and on ES |

## 5. What didn't

| Idea | Result |
|---|---|
| Direction of the day (dozens of inputs: gap, profile shape, calendar, positioning, momentum, order flow, other markets, volatility structure, analog days, learned models) | 47-58% hit rates; the better ones flipped between periods or failed on ES |
| The "real move" after the opening push (about ten framings) | ≈ 50% continuation vs reversal; its size is forecastable, its direction is not |
| Hold or break, judged before price arrives (approach speed, order book, tape, time, structure) | 47-52% across ≈ 35,000 touches |
| Popular level families (Fibonacci, round numbers, profile levels, naked points of control, unfinished auctions, ledges, weekly and monthly levels) | behave like random nearby prices; range extremes break slightly more often, and none hold more often |
| Exact minute more than ~5 minutes ahead | 5-19% within ±2 minutes; clean trends slow down, so straight-line estimates arrive about twice too early |
| Entries combining the forecastable parts, at 3-to-1 | 23-29% wins on held-out data, the rate chance gives at that ratio |
| About two dozen published daily strategies; Gann, Goldbach and other time/price geometry | failed against placebos |

## 6. Payoff

| Setup | Record |
|---|---|
| Breaks of major prior extremes, taken on a late-confirmed read | 419 trades 2020-26, 35.1% wins at 3-to-1, +0.29R per trade (95% bootstrap range +0.13 to +0.47R); about flat in 2025 |
| Fixed-time closing bracket (a backup, not a forecast) | 46-55% wins at 3-to-1 on the sealed 2024-26 data, depending on day type |

The 65% target was not reached. At 3-to-1, a 65% win rate needs about +1.6R per trade, around five times the best edge found.

## 7. Mistakes caught along the way

1. **Short samples.** Several early setups that won 60% or more on one year of data dropped to chance on the six-year backfill, and were downgraded in the log.
2. **A measurement artifact.** A "70% break" result came from one-minute bars: the bar that touches a level often already contains the move that settles the question. At tick resolution the edge shrank to about 2-5 points.
3. **A volume bug.** Order-flow columns that already held volume were multiplied by trade size again. The fix weakened one lead and left two other conclusions unchanged.
4. **A biased placebo.** A "+20-point magnet" effect came from a control that silently dropped its easy cases. A distance-matched control returned +1.6 and −0.6 points.
5. **Live-feed gaps.** For two days of live use, the data connection dropped every 10 to 17 minutes. The grader now flags days with missing data and keeps them out of the running record rather than scoring calls it couldn't see.

## 8. What was built

- **The research harness:** data loaders, case-finding and scoring, controls, period splits, and a decision log holding every pre-registration and result.
- **The live brief:** runs each morning, prints a five-line read, writes every call with a timestamp, and grades itself after the close.
- **Day Read (this repository):** the local app that shows the read, keeps the record, runs the end-of-day report, and manages practice or webhook accounts with hard risk limits. The engine that produces the read stays private.

## 9. Limitations and next steps

- Everything comes from two related markets and one stretch of history, so it should be re-checked on new data.
- The forward record is short, and live-feed gaps interrupted it.
- Intraday options flow and full order-book depth were not tested. They may hold information the tested inputs lack.

*Informational only. Not investment advice.*
