#!/usr/bin/env python3
"""Add results dates and profit growth to the shortlist, then pick the best 5.

Results calendar: NSE event calendar (board meetings for financial results).
Profit growth:    Screener.in quarterly table (latest quarter vs same quarter a year earlier).
Ranking rules:
  - only BREAKOUT / READY / PULLBACK setups (Qullamaggie, Minervini, stock-signals)
  - order by EVIDENCE: the average one-week result of that setup in today's market mood over the last
    5 years (results/backtest.json, written weekly by backtest.py). A setup that lost money on average
    (with enough history) is dropped that day; if every setup lost money in this market mood, only the
    least-bad one is kept and the report says so plainly.
    Without backtest.json the old fixed order is used (BREAKOUT > READY > PULLBACK).
  - then positive profit growth first (earnings momentum, AAMJAF 2015), then RS rating
  - skip if results are due within the hold period + 2 sessions; skip if the 90-day 15% gap flag is set
  - picks per market mood and size multipliers come from screener.P (picks_*/size_*)
  - total amount capped at capital (quantities scaled down if needed)
  - the top P["shortlist_n"] ranked stocks go to "shortlist" for the morning Claude checks
"""
import datetime as dt
import io
import json
import os
import math
import re
import time

import pandas as pd
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
SETUP_RANK = {"BREAKOUT": 0, "READY": 1, "PULLBACK": 2}
BACKTEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "backtest.json")


def evidence(regime):
    """Per setup: the history test's one-week stats in this market mood, and which setups to keep."""
    try:
        bt = json.load(open(BACKTEST))
    except (OSError, ValueError):
        return dict(available=False, keep=set(SETUP_RANK), stats={},
                    line="No history test yet (results/backtest.json missing): using the fixed setup order.")
    stats = {s: bt.get("by_setup_regime", {}).get(s, {}).get(regime, {}) for s in SETUP_RANK}
    known = {s: v for s, v in stats.items() if v.get("enough")}
    pos = {s for s, v in known.items() if v["avg_ret"] > 0}
    unknown = {s for s in SETUP_RANK if s not in known}
    if pos:
        keep, line = pos | unknown, None
    elif known:
        best = max(known, key=lambda s: known[s]["avg_ret"])
        keep = {best} | unknown
        v = known[best]
        line = (f"**History warning:** in a {regime} market no setup made money over one week in the last 5 years. "
                f"The least-bad, {best}, averaged {v['avg_ret']:+.2f}% a trade after charges "
                f"({v['win_pct']:.0f}% winners, {v['trades']} past trades). Picks are cut and sized down; "
                f"waiting is the safer choice.")
    else:
        keep, line = set(SETUP_RANK), None
    dropped = [s for s in known if s not in keep]
    if line is None:
        parts = [f"{s} {stats[s]['avg_ret']:+.2f}%" for s in SETUP_RANK if stats[s].get("avg_ret") is not None]
        line = (f"History ({bt.get('start')} to {bt.get('end')}), average one-week result per trade in a {regime} "
                f"market after charges: " + ", ".join(parts) + ".")
        if dropped:
            line += " Dropped today because they lost money on average: " + ", ".join(dropped) + "."
    return dict(available=True, keep=keep, stats=stats, line=line, dropped=dropped,
                period=f"{bt.get('start')} to {bt.get('end')}", generated=bt.get("generated"))
NAMES = {}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], 1)}


def results_calendar():
    """{SYMBOL: date} of upcoming board meetings that consider financial results (NSE)."""
    s = requests.Session()
    s.headers.update(HEADERS | {"Referer": "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"})
    try:
        s.get("https://www.nseindia.com", timeout=20)
        r = s.get("https://www.nseindia.com/api/event-calendar", timeout=30)
        rows = r.json()
    except Exception as e:  # noqa: BLE001
        return {}, f"NSE event calendar unavailable ({type(e).__name__})"
    out = {}
    for x in rows if isinstance(rows, list) else []:
        purpose = f"{x.get('purpose', '')} {x.get('bm_desc', '')}".lower()
        if "result" not in purpose:
            continue
        try:
            d = dt.datetime.strptime(str(x.get("date")), "%d-%b-%Y").date()
        except ValueError:
            continue
        sym = str(x.get("symbol", "")).upper()
        if sym and (sym not in out or d < out[sym]):
            out[sym] = d
            NAMES[sym] = str(x.get("company", "")).strip()
    return out, None


def nse_names():
    """{SYMBOL: company name} from NSE's listed-equity master file."""
    try:
        r = requests.get("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
                         headers=HEADERS | {"Referer": "https://www.nseindia.com/"}, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.BytesIO(r.content))
        df.columns = [c.strip() for c in df.columns]
        return dict(zip(df["SYMBOL"].astype(str).str.strip(), df["NAME OF COMPANY"].astype(str).str.strip()))
    except Exception:  # noqa: BLE001
        return {}


def screener_info(sym):
    """Latest quarter, its net-profit YoY change, and any board-meeting date mentioned on Screener.in."""
    url = f"https://www.screener.in/company/{requests.utils.quote(sym, safe='')}/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return dict(screener_error=type(e).__name__)
    html = r.text
    info = {}
    m = re.search(r"<title>\s*([^<|]+?)\s+share price", html, re.I)   # "<Company> share price | ... - Screener"
    if m:
        info["company"] = re.sub(r"\s+", " ", m.group(1)).strip()
    try:
        for t in pd.read_html(io.StringIO(html)):
            first = t.iloc[:, 0].astype(str).str.replace(r"[^A-Za-z ]", "", regex=True).str.strip()
            cols = [c for c in t.columns[1:] if re.match(r"^[A-Z][a-z]{2} \d{4}$", str(c))]
            if "Net Profit" in first.values and len(cols) >= 5:
                row = t[first == "Net Profit"].iloc[0]
                num = lambda c: float(str(row[c]).replace(",", ""))
                latest, year_ago = cols[-1], cols[-5]
                a, b = num(latest), num(year_ago)
                info["latest_quarter"] = latest
                info["profit_latest_cr"], info["profit_year_ago_cr"] = a, b
                info["profit_yoy_pct"] = round((a - b) / abs(b) * 100, 1) if b else None
                break
    except Exception:  # noqa: BLE001
        pass
    # announcement such as "... Board Meeting ... to be held on 30th October, 2026 ... financial results"
    m = re.search(r"[Bb]oard [Mm]eeting[^<]{0,200}?(\d{1,2})(?:st|nd|rd|th)?[\s-]+([A-Za-z]{3,9})[,\s-]+(\d{4})", html)
    if m:
        mon = MONTHS.get(m.group(2)[:3].lower())
        if mon:
            try:
                info["board_meeting_mentioned"] = dt.date(int(m.group(3)), mon, int(m.group(1))).isoformat()
            except ValueError:
                pass
    return info


def trading_days_between(a, b, hol):
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d not in hol:
            n += 1
    return n


def last_quarter_end(asof):
    q = (asof.month - 1) // 3
    return dt.date(asof.year, 3 * q, 1) if q else dt.date(asof.year - 1, 12, 1)


def enrich_and_pick(out, hol, capital, n_pick=None):
    import screener                                     # settings live in screener.P
    P = screener.P
    regime = out["regime"]
    n_pick = n_pick or P.get(f"picks_{regime.lower()}", 5)
    size = P.get(f"size_{regime.lower()}", 1.0)
    ev = evidence(regime)
    allowed = P.get("setups_allowed")
    if allowed:                                         # user's fixed choice overrides the history test
        allowed = [s for s in allowed if s in SETUP_RANK]
        ev["keep"] = set(allowed)
        parts = []
        for s in allowed:
            st = ev["stats"].get(s) or {}
            if st.get("avg_ret") is not None:
                parts.append(f"{s} {st['avg_ret']:+.2f}% a trade after charges ({st['win_pct']:.0f}% winners, "
                             f"{st['trades']} past trades)")
        if ev["available"] and parts:
            ev["line"] = (f"History ({ev.get('period')}), one-week result in a {regime} market: " + "; ".join(parts)
                          + ". Only " + ", ".join(s.lower() + "s" for s in allowed) + " are picked (your setting).")
            bad = [s for s in allowed if (ev["stats"].get(s) or {}).get("enough") and ev["stats"][s]["avg_ret"] <= 0]
            if bad:
                ev["line"] = "**History warning:** " + ev["line"] + (" In this market they lost money on average; "
                             "picks are cut and sized down, and waiting is the safer choice.")
        ev["dropped"] = [s for s in SETUP_RANK if s not in ev["keep"]]
    exp = lambda c: (ev["stats"].get(c["setup"], {}).get("avg_ret") if ev["available"] else None)
    rank = lambda c: (-(exp(c) if exp(c) is not None else -99), SETUP_RANK[c["setup"]], -c["rs_rating"])

    asof = dt.date.fromisoformat(out["asof"])
    cands = out["candidates"]
    allsetups = [c for c in cands if c["setup"] in SETUP_RANK]
    dropped_by_history = [c for c in allsetups if c["setup"] not in ev["keep"]]
    setups = sorted([c for c in allsetups if c["setup"] in ev["keep"]], key=rank)[:20]
    trend = [c for c in cands if c["setup"] == "TREND"][:5]
    cal, cal_err = results_calendar()
    master = nse_names()
    lq = last_quarter_end(asof)
    lq_label = lq.strftime("%b %Y")
    for c in setups + trend:
        info = screener_info(c["symbol"])
        c.update(info)
        c["company"] = master.get(c["symbol"]) or c.get("company") or NAMES.get(c["symbol"])
        nxt = cal.get(c["symbol"])
        if not nxt and info.get("board_meeting_mentioned"):
            d = dt.date.fromisoformat(info["board_meeting_mentioned"])
            if d > asof:
                nxt = d
        c["next_results"] = nxt.isoformat() if nxt else None
        c["results_in_sessions"] = trading_days_between(asof, nxt, hol) if nxt else None
        c["reported_latest_quarter"] = info.get("latest_quarter") == lq_label
        st = ev["stats"].get(c["setup"]) or {}
        c["history"] = {k: st.get(k) for k in ("avg_ret", "win_pct", "target_pct", "stop_pct", "trades")} if st else None
        time.sleep(1.5)

    results_window = P.get("hold_sessions", 5) + 2
    picks, skipped = [], []
    for c in setups:
        why = None
        if c.get("results_in_sessions") is not None and c["results_in_sessions"] <= results_window:
            why = f"results on {c['next_results']}"
        elif c.get("note") and "gap" in c["note"]:
            why = "15% gap in last 90 days"
        elif regime == "YELLOW" and c["rs_rating"] < 85:
            why = "YELLOW market needs RS 85+"
        (skipped if why else picks).append(dict(c, skip_reason=why) if why else c)
    growth_ok = lambda c: 0 if (c.get("profit_yoy_pct") or -1) > 0 else 1
    picks.sort(key=lambda c: (rank(c)[0], growth_ok(c), rank(c)[1], rank(c)[2]))
    shortlist = picks[:P.get("shortlist_n", 10)]
    best, reserve = shortlist[:n_pick], shortlist[n_pick:]

    if size < 1:                                       # smaller positions when the market is not GREEN
        for c in shortlist:
            c["qty"] = math.floor(c["qty"] * size)
            c["amount"] = round(c["qty"] * c["entry"], 2)
    total = sum(c["amount"] for c in best)
    if total > capital:                                # never commit more than the account
        f = capital / total
        for c in best:
            c["qty"] = math.floor(c["qty"] * f)
            c["amount"] = round(c["qty"] * c["entry"], 2)

    out["best5"] = best
    out["reserve"] = reserve
    out["shortlist"] = shortlist
    out["picks_today"] = n_pick
    out["size_multiplier"] = size
    out["evidence"] = dict(line=ev["line"], available=ev["available"], keep=sorted(ev["keep"]),
                           dropped=ev.get("dropped", []), period=ev.get("period"),
                           stats={k: v for k, v in ev["stats"].items() if v})
    out["skipped"] = ([dict(symbol=c["symbol"], reason=c["skip_reason"]) for c in skipped] +
                      [dict(symbol=c["symbol"], reason=(f"{c['setup']} not picked (setting: pullbacks only)" if allowed
                                                        else f"{c['setup']} lost money on average in {regime} markets"))
                       for c in dropped_by_history][:15])
    out["watch_trend"] = trend
    out["enrich_notes"] = dict(results_calendar=cal_err or f"NSE event calendar: {len(cal)} companies",
                               latest_quarter_expected=lq_label)
    return out
