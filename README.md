# second-swing-scanner

Daily NSE swing-trade screen for **stage-2 momentum leaders**, built from published research
(NSE momentum index methodology, Minervini trend template + VCP, Qullamaggie breakouts, Indian backtests).
Research notes and sources: the "Indian Swing Trading Filters" doc.

## How it runs

| When (IST) | What |
|---|---|
| 6:47 PM Mon–Fri | GitHub Action: bring NSE data up to today's close, scan every NSE stock, commit `results/` |
| 9:37 PM, 6:07 AM | Retries if NSE's files weren't available (the 6:07 AM run fails the job, and GitHub emails you, if data is still stale) |
| 7:45 AM Mon–Fri | Claude scheduled task reads `results/`, checks results dates on Screener.in, sends the report |

Data: [EOD2](https://github.com/BennyThadikaran/eod2_data) (split/bonus-adjusted, updated ~weekly) plus NSE's
official daily files for the missing days (`sec_bhavdata_full`, `ind_close_all`), with Yahoo Finance as fallback.
Days appended from the daily files are not split-adjusted until EOD2 catches up; the scan flags any 15% gap.

## Files

- `fetch_data.py` – appends missing trading days to the EOD2 dataset; writes source/error details
- `screener.py` – the filters (thresholds and their sources are in the `P` dict)
- `run_daily.py` – pipeline entry point; skips if results are already fresh (`FORCE=1` to re-run)
- `results/status.json` – `fresh: true/false`, data date, sources used, errors
- `results/latest.json`, `results/latest.md` – today's scan; `results/history/` – one file per data date

## Filters (summary)

0. Regime: Nifty 500 above its 200-day SMA and 10-day EMA above 20-day EMA → GREEN; one → YELLOW; none → RED (no new longs)
1. Tradable: NSE EQ series, ≥1 year history, price ≥ ₹20, 20-day median turnover ≥ ₹2.5 cr (50× a ₹5 lakh account), circuit hits < 20% of days
2. Minervini trend template (price/50/150/200-day SMA order, 200-day rising, ≥30% above 52-week low, within 25% of high)
3. RS rating ≥ 70 (40% 3-month + 20% each 6/9/12-month return, percentile)
4. Setup: BREAKOUT (close > 21-day high on ≥1.5× volume), READY (tight base within 3% of pivot), PULLBACK (to rising 20 EMA); skip if > 10% above 21 EMA
5. Plan: buy-stop +0.2% above pivot/high; stop ≤ min(1 ADR, 7%); risk 0.5% of ₹5 lakh (₹2,500); position ≤ 25%; sell ⅓–½ at +2.5 ADR, trail 10/20 EMA

Rules-based screen, not investment advice.
