# Research notes: forecasting an intraday futures session

These notes cover what held up, what didn't, and the testing habits that kept me from fooling myself. They describe results, not the recipe.

I worked on US equity-index futures with about twelve years of intraday data, using a second index future as an independent check. Everything here is regular-session only.

The goal was to forecast a session in five parts: the bias of the open and the day, where price reacts, when it gets there, how the reaction unfolds, and whether trading it pays. Some of that turned out to be forecastable on data the work never saw: how big and fast the moves are, which nearby prices get reached, and how a reaction candle finishes once it has started. The direction of the day, whether a level holds before price arrives, and the exact minute more than a few minutes out were not. I also could not turn the forecastable parts into a high win-rate entry.

## Principles

Most of what this work is worth comes from the ideas it threw out.

1. Write the test down before running it. The data, the measurement and the pass mark are fixed before I look at the result, and a near miss stays a miss.
2. A result has to show up in an early, a middle and a recent period, and point the same way on the second market.
3. Compare every level or rule against made-up ones placed at the same kind of distance. A "level" that behaves like a random nearby price isn't a level.
4. Score each forecast against what happened after its own timestamp, never against an earlier price.
5. Re-run anything found on one-minute bars on tick data before believing it. Several edges disappeared at this step.
6. Keep the most recent stretch of data sealed until a rule has passed everything else, then open it once.
7. Write down the failures. About nine in ten ideas failed, and they taught me as much as the ones that worked.

## What held up

These held on periods the work was not fit on, and in the same direction on the second market.

- How large and busy the first half hour will be can be ranked well from conditions before the open, clearly better than a plain recent average.
- Which nearby extreme gets visited first is better than a coin flip, and much better when one of them is clearly closer.
- How often a gap closes depends heavily on its size, and that relationship stayed stable for more than ten years.
- Ranking nearby prices by how likely they are to trade soon worked well. Getting reached and holding are separate questions, covered below.
- When price is a couple of minutes away, the arrival minute can be forecast usefully. Further out it can't.
- Once a reaction candle has started, its first part says a lot about how it will finish, and the speed of what follows separates outcomes clearly.

## What didn't hold up

- Direction of the day. I tried dozens of inputs: gaps, profile shape, the calendar, positioning, momentum, order flow, other markets, volatility structure, look-alike days and learned models. Hit rates landed between the high 40s and high 50s in percent, and the better ones flipped between periods or failed on the second market.
- The "real move" after the opening push. About ten framings all came out near 50/50 for continuation versus reversal. The size of the move was forecastable; its direction wasn't.
- Whether a level holds or breaks before price gets there. Approach speed, order-book imbalance, the tape, time of day and nearby structure all sat near 50% at tick resolution. A strong-looking edge on bars turned out to be an artifact, because the bar already contained its own outcome.
- Level types. Nearly every popular family of price levels behaved like random nearby prices. A few broke slightly more often than random, and none held more often.
- The exact minute further out. Nothing I tested narrowed the arrival time beyond a few minutes. In clean trends, straight-line estimates came in about twice too early, because trends slow down.
- High win-rate entries. Every rule built by combining the forecastable parts did about as well on held-out data as chance does at a 3-to-1 reward-to-risk ratio. The best setups I could verify were modest.
- Popular strategies and timing theories. Two dozen well-known daily rules and several time/price geometry theories failed against placebos.

## Pitfalls

Each of these caught me at least once.

- On one-minute bars, the bar that touches a level often already contains the move that settles the question.
- A placebo placed too close to the real level shares its outcome, and one that drops the easy cases is biased. Match the distance.
- Check whether order-flow columns already hold volume before multiplying them by size.
- Scores that were in-sample for part of the history make that part look better than it was.
- A rule that only works in the recent stretch is describing a regime.
- Picking the best of many splits produces winners that vanish. The number of passes has to beat what shuffled data produces.

## If you want to try this yourself

1. Get the finest data you can, plus a second related market to check against.
2. Split by time: an early period to explore, middle and recent periods to confirm, and a final stretch you leave alone.
3. Before running anything, write down the hypothesis, the measurement and the pass mark.
4. Build placebos for every level or rule at matched distances.
5. Re-run anything you found on bars on tick data.
6. Keep count of how many things you tried, and compare your passes with shuffled data.
7. Once something passes, log its calls forward in time with timestamps and grade them automatically against what happened next. Trust the forward record over the backtest.
8. Keep the failures. They will save the next person months.

## Limitations

The high win-rate payoff that started this project was never reached. Everything comes from two related markets and one stretch of market history, so check any of it on new data before relying on it. Some leads are still only being logged and checked forward in time. I didn't have intraday options flow or full order-book depth, and those may carry information the tested inputs didn't.

## Left out on purpose

Inputs, thresholds, timings, model forms, tuning, setup rules, data layout and source code are not in these notes. There's enough here to judge the work and plan something similar, not enough to rebuild it.

*Informational only. Not investment advice.*
