#!/usr/bin/env python3
"""Follow every past shortlisted pick: did the buy-stop trigger, then did it hit Target 1 or the Stop, and when.

Which stocks: the whole ranked shortlist (top 10) of each day, so every stock the morning report can pick
is tracked. "best_on" lists the days a stock was in GitHub's own best picks (before the morning checks);
the dashboard shows the morning report's final picks (reported.json, saved into the dashboard by the
morning task) and falls back to best_on for days without that file.

DIP picks (since 13 Nov 2026):
  - The limit buy is valid on the next trading day only: FILLED if that day's low reaches the limit,
    fill = min(open, limit); otherwise NOT TRIGGERED (shown as "Not bought").
  - From the fill day on: low <= disaster stop -> STOP HIT (at min(open, stop)); else a CLOSE above the
    10-day EMA -> EXIT 10EMA at that close; else no exit by the close of day 5 -> SOLD DAY 5 at that close.

Other setups (BREAKOUT / READY / PULLBACK, the rules before 13 Nov 2026):
  - A pick's buy-stop is valid on the next trading day only.
  - If that day opens more than 3% above "Buy above", the trade is SKIPPED (report rule).
  - Otherwise it TRIGGERS when the day's high reaches "Buy above"; fill = max(open, Buy above).
  - After entry, each day: low <= Stop -> STOP HIT; high >= Target 1 -> TARGET HIT.
    Both on the same day counts as STOP HIT (we can't know which came first), unless the day
    opened beyond one of them. A gap through a level fills at the open.
  - Once a day's high reaches entry + 1R, the stop moves to the buy price from the next day;
    a later exit there is BREAKEVEN.
  - Day 1 = the entry day. No hit by the close of day 5 (screener.P["hold_sessions"]) -> SOLD DAY 5
    at that close (the one-week time stop).
  - Repeats: a stock picked again while its trade is open is the same trade (suggested count +1).
    A stock picked again on the day its buy-stop missed carries on as the same idea with the new levels.

Usage: python3 tracker.py <eod2_data_dir>   (writes results/tracker.json and results/tracker.html)
"""
import datetime as dt
import glob
import json
import os
import sys

import pandas as pd

import screener

ROOT = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(ROOT, "results")
SKIP_GAP_PCT = screener.P.get("gap_skip_pct", 3.0)
MAX_DAYS = screener.P.get("hold_sessions", 5)
BE_AT_R = screener.P.get("breakeven_at_r", 1.0)
CLOSED = ("TARGET HIT", "EXIT 10EMA", "STOP HIT", "BREAKEVEN", "SOLD DAY %d" % MAX_DAYS)
EXIT_EMA = screener.P.get("dip_exit_ema", 10)
TRACK_SINCE = screener.P.get("track_since") or "0000-00-00"


def load_picks():
    picks = {}
    for f in sorted(glob.glob(os.path.join(RES, "history", "*.json"))):
        d = json.load(open(f))
        if d.get("asof", "") < TRACK_SINCE:              # fresh start: older scans are kept but not tracked
            continue
        best = {c["symbol"] for c in d.get("best5") or []}
        for c in d.get("shortlist") or d.get("best5") or []:
            if c.get("entry") is None or c.get("stop") is None or c.get("t1") is None:
                continue
            c = dict(c, asof=d["asof"], regime=d.get("regime"), in_best=c["symbol"] in best)
            picks.setdefault(c["symbol"], []).append(c)
    return picks


def prices(daily, symbol, since):
    f = os.path.join(daily, symbol.lower() + ".csv")
    if not os.path.exists(f):
        return None
    d = pd.read_csv(f, usecols=["Date", "Open", "High", "Low", "Close"])
    d["Date"] = d["Date"].astype(str).str[:10]
    d = d.drop_duplicates("Date", keep="last").sort_values("Date")
    d["Ema"] = d["Close"].ewm(span=EXIT_EMA, adjust=False).mean()   # same EMA as the screener's ema10
    d = d[d["Date"] >= since]
    return [r for r in d.itertuples(index=False)]


def new_trade(p):
    return dict(symbol=p["symbol"], company=p.get("company"), setup=p["setup"], regime=p.get("regime"),
                suggested=[p["asof"]], best_on=[p["asof"]] if p.get("in_best") else [], close_at_pick=p["close"], entry=p["entry"], stop=p["stop"], t1=p["t1"],
                status="WAITING", entry_date=None, fill=None, exit_date=None, exit_price=None,
                days=None, last_close=None, result_pct=None, r_mult=None, flag="")


def add_day(t, p, date):
    t["suggested"].append(date)
    if p.get("in_best"):
        t["best_on"].append(date)


def finish(t, status, date, price):
    t.update(status=status, exit_date=date, exit_price=round(price, 2))
    t["result_pct"] = round((price / t["fill"] - 1) * 100, 2)
    t["r_mult"] = round((price - t["fill"]) / (t["fill"] - t["stop"]), 2) if t["fill"] > t["stop"] else None


def check_exit_dip(t, r):
    o, l, c = r.Open, r.Low, r.Close
    if l <= t["stop"]:
        return finish(t, "STOP HIT", r.Date, min(o, t["stop"]))
    if c > r.Ema:
        return finish(t, "EXIT 10EMA", r.Date, c)
    if t["days"] >= MAX_DAYS:
        return finish(t, "SOLD DAY %d" % MAX_DAYS, r.Date, c)


def check_exit(t, r):
    if t.get("setup") == "DIP":
        return check_exit_dip(t, r)
    o, h, l, c = r.Open, r.High, r.Low, r.Close
    stop = t.get("active_stop") or t["stop"]
    stop_name = "BREAKEVEN" if stop >= t["fill"] else "STOP HIT"
    hit_stop, hit_t1 = l <= stop, h >= t["t1"]
    if hit_stop and hit_t1:
        if o >= t["t1"]:
            return finish(t, "TARGET HIT", r.Date, o)
        t["flag"] = "stop and target both touched that day; counted as stop"
        return finish(t, stop_name, r.Date, min(o, stop))
    if hit_stop:
        return finish(t, stop_name, r.Date, min(o, stop))
    if hit_t1:
        return finish(t, "TARGET HIT", r.Date, max(o, t["t1"]))
    if t["days"] >= MAX_DAYS:
        return finish(t, "SOLD DAY %d" % MAX_DAYS, r.Date, c)
    if h >= t["fill"] + BE_AT_R * (t["fill"] - t["stop"]):     # up 1R: stop to the buy price from tomorrow
        t["active_stop"] = max(stop, t["fill"])


def track_symbol(picks, rows):
    by_date = {p["asof"]: p for p in picks}
    trades, cur, prev_close = [], None, None
    for r in rows:
        if prev_close and r.Open < prev_close * 0.6:      # likely split/bonus: levels no longer comparable
            if cur and cur["status"] in ("WAITING", "OPEN"):
                cur.update(status="DATA ISSUE", result_pct=None,
                           flag=f"price fell >40% overnight on {r.Date} (split/bonus?)")
        prev_close = r.Close
        # 1) evaluate today's bar
        if cur and cur["status"] == "WAITING" and r.Date > cur["suggested"][-1]:
            cur["eval_date"] = r.Date
            if cur["setup"] == "DIP":                          # limit buy below the signal close
                if r.Low <= cur["entry"]:
                    cur.update(status="OPEN", entry_date=r.Date, fill=round(min(r.Open, cur["entry"]), 2), days=1)
                    check_exit(cur, r)
                else:
                    cur["status"] = "NOT TRIGGERED"
            elif r.Open > cur["entry"] * (1 + SKIP_GAP_PCT / 100):
                cur.update(status="SKIPPED", flag=f"opened {(r.Open / cur['entry'] - 1) * 100:.1f}% above Buy above")
            elif r.High >= cur["entry"]:
                cur.update(status="OPEN", entry_date=r.Date, fill=round(max(r.Open, cur["entry"]), 2), days=1)
                check_exit(cur, r)
            else:
                cur["status"] = "NOT TRIGGERED"
        elif cur and cur["status"] == "OPEN":
            cur["days"] += 1
            check_exit(cur, r)
        if cur and cur["status"] == "OPEN":
            cur["last_close"] = r.Close
            cur["ema_now"] = round(r.Ema, 2)
            cur["result_pct"] = round((r.Close / cur["fill"] - 1) * 100, 2)
        # 2) a new pick published after today's close
        p = by_date.get(r.Date)
        if not p:
            continue
        if cur and cur["status"] == "OPEN":
            add_day(cur, p, r.Date)
        elif cur and cur["status"] in ("NOT TRIGGERED", "SKIPPED") and cur.get("eval_date") == r.Date:
            add_day(cur, p, r.Date)
            cur.update(status="WAITING", entry=p["entry"], stop=p["stop"], t1=p["t1"], setup=p["setup"], flag="")
        else:
            if cur:
                trades.append(cur)
            cur = new_trade(p)
    for p in picks:                                      # picks newer than the last price row
        if not rows or p["asof"] > rows[-1].Date:
            if cur and cur["status"] in ("WAITING", "OPEN") and p["asof"] not in cur["suggested"]:
                add_day(cur, p, p["asof"])
            elif not cur or p["asof"] not in cur["suggested"]:
                if cur:
                    trades.append(cur)
                cur = new_trade(p)
    if cur:
        trades.append(cur)
    for t in trades:
        t.pop("eval_date", None)
        a = t.pop("active_stop", None)
        if t["status"] != "OPEN":
            t.pop("ema_now", None)
        if t["status"] == "OPEN":                             # for the morning to-do list
            t["stop_now"] = round(a or t["stop"], 2)
            t["sell_day_is_next"] = t["days"] + 1 >= MAX_DAYS
        t["cmp"] = rows[-1].Close if rows else None          # latest NSE close for the dashboard
        t["cmp_date"] = rows[-1].Date if rows else None
        t["times_suggested"] = len(t["suggested"])
        t["t1_pct"] = round((t["t1"] / t["entry"] - 1) * 100, 1)
        t["stop_pct"] = round((t["stop"] / t["entry"] - 1) * 100, 1)
    return trades


def summarize(trades):
    done = [t for t in trades if t["status"] in CLOSED]
    tgt = [t for t in done if t["status"] == "TARGET HIT"]
    stp = [t for t in done if t["status"] == "STOP HIT"]
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None

    def group(key):
        out = {}
        for t in trades:
            k = key(t)
            g = out.setdefault(k, dict(picks=0, triggered=0, target=0, stop=0, breakeven=0, sold_day=0, open=0,
                                       closed=0, wins=0, _res=[]))
            g["picks"] += 1
            g["triggered"] += t["entry_date"] is not None
            g["target"] += t["status"] == "TARGET HIT"
            g["exit_ema"] = g.get("exit_ema", 0) + (t["status"] == "EXIT 10EMA")
            g["stop"] += t["status"] == "STOP HIT"
            g["breakeven"] += t["status"] == "BREAKEVEN"
            g["sold_day"] += t["status"].startswith("SOLD DAY")
            g["open"] += t["status"] == "OPEN"
            if t["status"] in CLOSED:
                g["closed"] += 1
                g["wins"] += (t["result_pct"] or 0) > 0
                g["_res"].append(t["result_pct"] or 0)
        for g in out.values():
            res = g.pop("_res")
            g["win_rate"] = round(g["wins"] / g["closed"] * 100) if g["closed"] else None
            g["avg_result_pct"] = round(sum(res) / len(res), 2) if res else None
        return out

    count = lambda s: sum(t["status"] == s for t in trades)
    return dict(
        stock_ideas=len(trades), triggered=sum(t["entry_date"] is not None for t in trades),
        target_hit=len(tgt), exit_10ema=count("EXIT 10EMA"), stop_hit=len(stp), open=count("OPEN"),
        waiting=count("WAITING"), not_triggered=count("NOT TRIGGERED"), skipped=count("SKIPPED"),
        data_issue=count("DATA ISSUE"),
        breakeven=count("BREAKEVEN"), sold_day=count("SOLD DAY %d" % MAX_DAYS), closed=len(done),
        win_rate=round(sum((t["result_pct"] or 0) > 0 for t in done) / len(done) * 100) if done else None,
        avg_days_to_target=avg([t["days"] for t in tgt]), avg_days_to_stop=avg([t["days"] for t in stp]),
        avg_result_pct=avg([t["result_pct"] for t in done]), avg_r=avg([t["r_mult"] for t in done if t["r_mult"] is not None]),
        by_setup=group(lambda t: t["setup"]),
        by_regime=group(lambda t: t.get("regime") or "?"),
        by_times_suggested=group(lambda t: "3+ days" if t["times_suggested"] >= 3 else f"{t['times_suggested']} day" + ("s" if t["times_suggested"] > 1 else "")),
    )


def run(data_dir):
    daily = os.path.join(data_dir, "daily")
    picks = load_picks()
    trades, missing = [], []
    for sym, ps in picks.items():
        rows = prices(daily, sym, min(p["asof"] for p in ps))
        if rows is None:
            missing.append(sym)
            rows = []
        trades += track_symbol(ps, rows)
    trades.sort(key=lambda t: (t["suggested"][0], t["symbol"]), reverse=True)
    nifty = os.path.join(daily, "nifty 500.csv")
    data_date = prices(daily, "nifty 500", "2000-01-01")[-1].Date if os.path.exists(nifty) else None
    return dict(generated=dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(timespec="minutes"),
                data_date=data_date, first_pick=min((t["suggested"][0] for t in trades), default=None),
                rules=dict(skip_gap_pct=SKIP_GAP_PCT, max_days=MAX_DAYS, breakeven_at_r=BE_AT_R, exit_ema=EXIT_EMA,
                           dip_since="2026-11-12", track_since=TRACK_SINCE), missing_price_files=missing,
                summary=summarize(trades), trades=trades)


def write(out):
    json.dump(out, open(os.path.join(RES, "tracker.json"), "w"), indent=1)
    tpl = open(os.path.join(ROOT, "tracker_template.html"), encoding="utf-8").read()
    blob = json.dumps(out, ensure_ascii=False).replace("</", "<\\/")
    open(os.path.join(RES, "tracker.html"), "w", encoding="utf-8").write(tpl.replace("/*__DATA__*/{}", blob))


if __name__ == "__main__":
    o = run(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EOD2_DIR", "/tmp/eod2_data"))
    write(o)
    print(json.dumps(o["summary"], indent=1))
