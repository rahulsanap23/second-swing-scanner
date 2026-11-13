# second-swing-scanner

Daily NSE swing-trade screen for **stage-2 momentum leaders**, built from published research
(NSE momentum index methodology, Minervini trend template + VCP, Qullamaggie breakouts, Indian backtests).
Research notes and sources: the "Indian Swing Trading Filters" doc.

## How it runs

| When (IST) | What |
|---|---|
| 6:47 PM Mon–Fri | GitHub Action: bring NSE data up to today's close, scan every NSE stock, commit `results/` |
| 9:37 PM, 6:07 AM | Retries if NSE's files weren't available (the 6:07 AM run fails the job, and GitHub emails you, if data is still stale) |
| 7:45 AM Mon–Fri | Claude scheduled task reads `results/`, checks each shortlisted stock with the india skills (news, insider/bulk deals, results), sends the report, and republishes the Swing Pick Tracker dashboard from `results/tracker.html` |

Data: [EOD2](https://github.com/BennyThadikaran/eod2_data) (split/bonus-adjusted, updated ~weekly) plus NSE's
official daily files for the missing days (`sec_bhavdata_full`, `ind_close_all`), with Yahoo Finance as fallback.
Days appended from the daily files are not split-adjusted until EOD2 catches up; the scan flags any 15% gap.

## Files

- `fetch_data.py` – appends missing trading days to the EOD2 dataset; writes source/error details
- `screener.py` – the filters (thresholds and their sources are in the `P` dict)
- `tracker.py` – follows every stock in each day's ranked top 10 (so every stock the morning report can pick is covered): bought or not, then (DIP) close above the 10 EMA, disaster stop or Sold day 5, or (older picks) Target 1, Stop, Breakeven or Sold day 5, with scores by setup and market mood; writes `results/tracker.json` and the dashboard page `results/tracker.html` (from `tracker_template.html`). The dashboard shows the morning report's final picks by default (the morning task saves them into the dashboard as `reported.json`; days without that list fall back to GitHub's own best picks) and has an "All top 10" switch
- Tracking starts from `track_since` in `screener.py` (12 Nov 2026 scan, the first with the DIP rules); older scans stay in `results/history/` but aren't tracked
- `run_daily.py` – pipeline entry point; skips if results are already fresh (`FORCE=1` to re-run)
- `results/status.json` – `fresh: true/false`, data date, sources used, errors
- `results/latest.json`, `results/latest.md` – today's scan (`best5`, `reserve`, `shortlist` = top 10 for the morning checks, `mood_line`); `results/history/` – one file per data date
- `results/plans/` – one plan file per shortlisted stock in the india-timemachine skill's format (entry, stop, target = the 10 EMA at the pick for DIP, 5-session time stop)

## Filters (summary)

0. Regime: Nifty 500 above its 200-day SMA and 10-day EMA above 20-day EMA → GREEN; one → YELLOW; none → RED (fewer, smaller picks)
1. Tradable: NSE EQ series, ≥1 year history, price ≥ ₹20, 20-day median turnover ≥ ₹2.5 cr (50× a ₹5 lakh account), circuit hits < 20% of days
2. Minervini trend template (price/50/150/200-day SMA order, 200-day rising, ≥30% above 52-week low, within 25% of high)
3. RS rating ≥ 70 (40% 3-month + 20% each 6/9/12-month return, percentile)
4. Setups: **DIP** (RSI(2) < 10 at the close: a sharp 1–3 day dip in a trend-template leader), BREAKOUT (close > 21-day high on ≥1.5× volume), READY (tight base within 3% of pivot), PULLBACK (up-day bounce off a rising 20 EMA)
5. Setup types: only DIP setups are picked since 13 Nov 2026 (`setups_allowed` in `screener.py`; `None` = DIP, BREAKOUT, READY and PULLBACK, in that order). DIP stocks are ranked deepest dip first (lowest RSI(2)). Skip if results fall within 7 sessions or the stock gapped 15% in 90 days (split check)
6. Picks: 5 in GREEN, 5 at half size in YELLOW, 3 at half size in RED
7. DIP plan: next day's LIMIT buy 2% below the signal close (3% in RED), valid that day only; 12% disaster stop (`dip_stop_pct`; also sets the size: 0.5% of ₹5 lakh = ₹2,500 risk ÷ 12% ≈ ₹21k a position before the market-mood size cut); sell when the stock CLOSES above its 10-day EMA (at that close or the next open), or at the close of day 5

## Why DIP (13 Nov 2026 backtest, NSE daily data 2018 – Nov 2026)

The screener's rules were re-run on every trading day since 2018 (the rebuilt screen matches the live 12 Nov scan exactly) and every
trade was followed with the tracker's rules, after about 0.25% Zerodha charges a round trip:

| Rules | Trades | Win rate | Avg per trade | Years positive |
|---|---|---|---|---|
| Old: PULLBACK, buy-stop above the high, top 5 by RS, Target 1 / 7% stop / day 5 | 5,747 | 39% | −0.30% | 2 of 9 |
| New: DIP, limit 2% below close (3% in RED), sell on close > 10 EMA or day 5 | 2,528 | 58.5% | +0.84% | 9 of 9 |

New rules by market mood: GREEN +0.95%, YELLOW +0.89%, RED +0.21% a trade. Train 2018–22 and test 2023–26 were both positive.
About 36% of the limit orders fill (22% in RED). With the 12% disaster stop the average drops to about +0.5% a trade (a stop
costs this setup some edge, but caps the rare large loss: 1 trade in 20 lost about 10% or more without it).
Why the old rules lost: over a 1-week hold, Indian stocks that just ran up (high RSI(2), volume spikes, RS 94–99) tend to give
some of it back, while sharp dips in leaders tend to bounce. Caveats: the price data may lack delisted stocks; a limit is counted
as filled when the day's low touches it.

Rules-based screen, not investment advice.
