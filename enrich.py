#!/usr/bin/env python3
"""Add results dates and profit growth to the shortlist, then pick the best 5.

Results calendar: NSE event calendar (board meetings for financial results).
Profit growth:    Screener.in quarterly table (latest quarter vs same quarter a year earlier).
Ranking rules (from the research doc):
  - only BREAKOUT / READY / PULLBACK setups (Qullamaggie, Minervini, stock-signals)
  - skip if results are due within the next 10 trading days (exit/avoid before results)
  - skip if the 90-day 15% gap flag is set (possible split or news, Clenow)
  - order: setup (BREAKOUT > READY > PULLBACK), then positive profit growth first
    (earnings momentum, AAMJAF 2015), then RS rating
  - total amount capped at capital (quantities scaled down if needed)
"""
import datetime as dt
import io
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


def enrich_and_pick(out, hol, capital, n_pick=5):
    asof = dt.date.fromisoformat(out["asof"])
    cands = out["candidates"]
    setups = [c for c in cands if c["setup"] in SETUP_RANK][:15]
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
        time.sleep(1.5)

    picks, skipped = [], []
    for c in setups:
        why = None
        if c.get("results_in_sessions") is not None and c["results_in_sessions"] <= 10:
            why = f"results on {c['next_results']}"
        elif c.get("note") and "gap" in c["note"]:
            why = "15% gap in last 90 days"
        elif out["regime"] == "YELLOW" and c["rs_rating"] < 85:
            why = "YELLOW market needs RS 85+"
        (skipped if why else picks).append(dict(c, skip_reason=why) if why else c)
    growth_ok = lambda c: 0 if (c.get("profit_yoy_pct") or -1) > 0 else 1
    picks.sort(key=lambda c: (SETUP_RANK[c["setup"]], growth_ok(c), -c["rs_rating"]))
    best, reserve = picks[:n_pick], picks[n_pick:n_pick + 3]

    if out["regime"] == "YELLOW":                      # research: half size when only one trend condition holds
        for c in best + reserve:
            c["qty"] = math.floor(c["qty"] / 2)
            c["amount"] = round(c["qty"] * c["entry"], 2)
    total = sum(c["amount"] for c in best)
    if total > capital:                                # never commit more than the account
        f = capital / total
        for c in best:
            c["qty"] = math.floor(c["qty"] * f)
            c["amount"] = round(c["qty"] * c["entry"], 2)

    out["best5"] = best
    out["reserve"] = reserve
    out["skipped"] = [dict(symbol=c["symbol"], reason=c["skip_reason"]) for c in skipped]
    out["watch_trend"] = trend
    out["enrich_notes"] = dict(results_calendar=cal_err or f"NSE event calendar: {len(cal)} companies",
                               latest_quarter_expected=lq_label)
    return out
