# History test: 2021-10-23 to 2026-10-23

Every setup the scanner would have found on each trading day, traded the way the report says: buy-stop next session (skipped if it opens > 3% above), stop as planned, stop moved to the buy price after +1R, sell at the close of day 5 if neither stop nor target was hit. Returns are after about 0.25% Zerodha charges. 2205 stocks. Generated 2026-11-04T13:14+05:30.

## 1. Setup × market mood (live plan: target 2.5 ADR, sell on day 5)

This is the table the daily ranking uses.

| Setup · market | Trades | Win % | Target hit % | Stop hit % | Avg return | Avg win | Avg loss |
|---|---|---|---|---|---|---|---|
| BREAKOUT · GREEN | 1532 | 28.9 | 11.1 | 50.1 | -0.72% | +4.87% | -3.00% |
| BREAKOUT · YELLOW | 476 | 34.5 | 14.1 | 44.1 | -0.15% | +5.35% | -3.04% |
| BREAKOUT · RED | 183 | 18.0 | 6.6 | 53.6 | -1.60% | +5.09% | -3.07% |
| READY · GREEN | 480 | 20.8 | 11.7 | 59.2 | -0.85% | +4.05% | -2.14% |
| READY · YELLOW | 83 | 24.1 | 13.3 | 53.0 | -0.74% | +3.69% | -2.15% |
| READY · RED | 25 (few) | 20.0 | 0.0 | 60.0 | -1.25% | +2.01% | -2.06% |
| PULLBACK · GREEN | 11363 | 41.1 | 14.0 | 27.6 | +0.08% | +5.37% | -3.62% |
| PULLBACK · YELLOW | 5028 | 42.4 | 11.9 | 27.0 | +0.05% | +5.25% | -3.79% |
| PULLBACK · RED | 2319 | 39.6 | 11.9 | 32.4 | -0.31% | +5.55% | -4.15% |

## 2. Which target works best when selling on day 5

| Target | Trades | Win % | Target hit % | Stop hit % | Avg return | Avg win | Avg loss |
|---|---|---|---|---|---|---|---|
| 2.5 ADR (live) | 21489 | 39.5 | 12.9 | 31.0 | -0.07% | +5.31% | -3.59% |
| 4 ADR | 21489 | 38.7 | 3.7 | 31.0 | -0.05% | +5.48% | -3.55% |
| 1.5 ADR | 21489 | 43.9 | 28.9 | 30.9 | -0.17% | +4.48% | -3.82% |
| 1 ADR | 21489 | 51.3 | 44.3 | 30.0 | -0.25% | +3.44% | -4.15% |
| 2R | 21489 | 39.7 | 12.3 | 31.0 | -0.08% | +5.28% | -3.60% |
| nearer of 1.5 ADR / 2R | 21489 | 43.9 | 28.9 | 30.9 | -0.17% | +4.48% | -3.82% |
| no target (sell on the last day) | 21489 | 38.6 | 0.0 | 31.0 | -0.05% | +5.51% | -3.54% |
| bought at open | 37138 | 32.3 | 8.8 | 40.2 | -0.18% | +5.52% | -2.90% |

"Bought at open" = buying at the next open without waiting for the Buy-above price.

## 3. How long to hold (target 2.5 ADR)

| Hold | Trades | Win % | Target hit % | Stop hit % | Avg return | Avg win | Avg loss |
|---|---|---|---|---|---|---|---|
| 5 sessions | 21489 | 39.5 | 12.9 | 31.0 | -0.07% | +5.31% | -3.59% |
| 10 sessions | 21343 | 34.3 | 22.1 | 41.8 | +0.04% | +7.59% | -3.90% |
| 20 sessions | 21113 | 31.0 | 28.2 | 46.8 | +0.14% | +9.33% | -3.98% |

Pullbacks only, by hold:

| Hold | GREEN | YELLOW | RED |
|---|---|---|---|
| 5 sessions | +0.08% | +0.05% | -0.31% |
| 10 sessions | +0.19% | +0.10% | +0.01% |
| 20 sessions | +0.26% | +0.17% | +0.47% |

## 4. RED market: all setups vs leaders only

Leaders = RS 90+, up over 10 sessions while Nifty 500 is down.

| Group | Trades | Win % | Target hit % | Stop hit % | Avg return | Avg win | Avg loss |
|---|---|---|---|---|---|---|---|
| All RED setups | 2527 | 37.8 | 11.4 | 34.2 | -0.41% | +5.52% | -4.02% |
| RED leaders | 779 | 37.2 | 10.4 | 35.4 | -0.51% | +5.73% | -4.21% |

## 5. By year (live plan)

| Year | Trades | Win % | Target hit % | Stop hit % | Avg return | Avg win | Avg loss |
|---|---|---|---|---|---|---|---|
| 2021 | 606 | 43.4 | 12.5 | 29.5 | +0.25% | +6.20% | -4.32% |
| 2022 | 2661 | 36.4 | 11.8 | 33.2 | -0.34% | +5.61% | -3.75% |
| 2023 | 5155 | 42.6 | 14.8 | 25.4 | +0.35% | +5.09% | -3.16% |
| 2024 | 6028 | 42.1 | 13.8 | 30.7 | +0.21% | +5.84% | -3.88% |
| 2025 | 3106 | 35.3 | 10.7 | 35.0 | -0.69% | +4.17% | -3.34% |
| 2026 | 3933 | 36.4 | 11.8 | 34.5 | -0.44% | +5.21% | -3.68% |

Limits: delisted companies are missing from the data (flatters results a little); results dates can't be replayed; overlapping signals on the same stock are counted separately. Past results do not promise future ones.
