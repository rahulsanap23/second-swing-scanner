# second-swing-scanner

Daily NSE swing-trade screen for **stage-2 momentum leaders**, built from published research
(NSE momentum index methodology, Minervini trend template + VCP, Qullamaggie breakouts, Indian backtests).
Research notes and sources: the "Indian Swing Trading Filters" doc.

## How it runs

| When (IST) | What |
|---|---|
| 6:47 PM Mon–Fri | GitHub Action: bring NSE data up to today's close, scan every NSE stock, commit `results/` |
| 9:37 PM, 6:07 AM | Retries if NSE's files weren't available (the 6:07 AM run fails the job, and GitHub emails you, if data is still stale) |
| 7:45 AM Mon–Fri | Claude scheduled task reads `results/`, checks each shortlisted stock with the india skills (time machine, news, insider/bulk deals, results), sends the report, and republishes the Swing Pick Tracker dashboard from `results/tracker.html` |
| 9:07 AM Saturday | GitHub Action `weekly-history-test`: replays the scanner on 5 years of NSE data and commits `results/backtest.json` / `backtest.md`, which the daily run uses to rank picks |

Data: [EOD2](https://github.com/BennyThadikaran/eod2_data) (split/bonus-adjusted, updated ~weekly) plus NSE's
official daily files for the missing days (`sec_bhavdata_full`, `ind_close_all`), with Yahoo Finance as fallback.
Days appended from the daily files are not split-adjusted until EOD2 catches up; the scan flags any 15% gap.

## Files

- `fetch_data.py` – appends missing trading days to the EOD2 dataset; writes source/error details
- `screener.py` – the filters (thresholds and their sources are in the `P` dict)
- `backtest.py` – the history test: every setup the scanner would have found on each trading day of the last 5 years, traded exactly as the report says (buy-stop, stop, breakeven at +1R, Target 1, sell at the close of day 5, Zerodha charges); results by setup and market mood, by target and by holding period, in `results/backtest.json` and `results/backtest.md`
- `tracker.py` – follows every stock in each day's ranked top 10 (so every stock the morning report can pick is covered): triggered or not, then Target 1, Stop, Breakeven or Sold day 5, with scores by setup and market mood; writes `results/tracker.json` and the dashboard page `results/tracker.html` (from `tracker_template.html`). The dashboard shows the morning report's final picks by default (the morning task saves them into the dashboard as `reported.json`; days without that list fall back to GitHub's own best picks) and has an "All top 10" switch
- `run_daily.py` – pipeline entry point; skips if results are already fresh (`FORCE=1` to re-run)
- `results/status.json` – `fresh: true/false`, data date, sources used, errors
- `results/latest.json`, `results/latest.md` – today's scan (`best5`, `reserve`, `shortlist` = top 10 for the morning checks, `evidence`); `results/history/` – one file per data date
- `results/plans/` – one plan file per shortlisted stock in the india-timemachine skill's format (entry, stop, target, 5-session time stop)

## Filters (summary)

0. Regime: Nifty 500 above its 200-day SMA and 10-day EMA above 20-day EMA → GREEN; one → YELLOW; none → RED (no new longs)
1. Tradable: NSE EQ series, ≥1 year history, price ≥ ₹20, 20-day median turnover ≥ ₹2.5 cr (50× a ₹5 lakh account), circuit hits < 20% of days
2. Minervini trend template (price/50/150/200-day SMA order, 200-day rising, ≥30% above 52-week low, within 25% of high)
3. RS rating ≥ 70 (40% 3-month + 20% each 6/9/12-month return, percentile)
4. Setup: BREAKOUT (close > 21-day high on ≥1.5× volume), READY (tight base within 3% of pivot), PULLBACK (to rising 20 EMA); skip if > 10% above 21 EMA
5. Ranking by evidence: setups are ordered by their average one-week result in today's market mood (from the history test). A setup that lost money on average is dropped that day; if all did (RED markets so far), only the least-bad is kept, with a warning in the report. Then profit growth, then RS rating. Skip if results fall within 7 sessions
6. Picks: 5 in GREEN, 5 at half size in YELLOW (RS 85+), 3 at half size in RED
7. One-week plan: buy-stop +0.2% above pivot/high (skip if it opens > 3% above); stop ≤ min(1 ADR, 7%); risk 0.5% of ₹5 lakh (₹2,500) before the size cut; Target 1 = +2.5 ADR; stop to the buy price after +1R; sell at the close of day 5

## What the history test found (Oct 2021 – Oct 2026)

Full tables: `results/backtest.md`. Average one-week result per trade after charges, selling on day 5:

- PULLBACK: +0.08% (GREEN), +0.05% (YELLOW), −0.31% (RED). BREAKOUT and READY lost money in every market mood.
- Closer targets (1–1.5 ADR, 2R) won more often but made less overall; 2.5 ADR is kept.
- Buying at the open instead of waiting for "Buy above" was clearly worse (stop hit 40% vs 31%).
- Holding longer did better: all setups −0.07% at 5 sessions, +0.04% at 10, +0.14% at 20; pullbacks in RED −0.31% at 5 sessions but +0.47% at 20 (section 3 of backtest.md). `hold_sessions` in `screener.py` changes it.
- "RED leaders" (RS 90+, up while the index falls) did not do better than other RED picks, so no such filter is used.

The edge is small. The point of the plan is to keep losses small and let the occasional +5% winner pay for them.

Rules-based screen, not investment advice.
