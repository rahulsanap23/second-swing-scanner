#!/usr/bin/env python3
"""Follow every past best-5 pick: did the buy-stop trigger, then did it hit Target 1 or the Stop, and when.

Rules (kept in line with the morning report's "How to trade" lines):
  - A pick's buy-stop is valid on the next trading day only.
  - If that day opens more than 3% above "Buy above", the trade is SKIPPED (report rule).
  - Otherwise it TRIGGERS when the day's high reaches "Buy above"; fill = max(open, Buy above).
  - After entry, each day: low <= Stop -> STOP HIT; high >= Target 1 -> TARGET HIT.
    Both on the same day counts as STOP HIT (we can't know which came first), unless the day
    opened beyond one of them. A gap through a level fills at the open.
  - Day 1 = the entry day. No hit after 20 trading days -> EXPIRED at that day's close.
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

ROOT = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(ROOT, "results")
SKIP_GAP_PCT = 3.0
MAX_DAYS = 20


def load_picks():
    picks = {}
    for f in sorted(glob.glob(os.path.join(RES, "history", "*.json"))):
        d = json.load(open(f))
        for c in d.get("best5") or []:
            if c.get("entry") is None or c.get("stop") is None or c.get("t1") is None:
                continue
            c = dict(c, asof=d["asof"], regime=d.get("regime"))
            picks.setdefault(c["symbol"], []).append(c)
    return picks


def prices(daily, symbol, since):
    f = os.path.join(daily, symbol.lower() + ".csv")
    if not os.path.exists(f):
        return None
    d = pd.read_csv(f, usecols=["Date", "Open", "High", "Low", "Close"])
    d["Date"] = d["Date"].astype(str).str[:10]
    d = d[d["Date"] >= since].drop_duplicates("Date", keep="last").sort_values("Date")
    return [r for r in d.itertuples(index=False)]


def new_trade(p):
    return dict(symbol=p["symbol"], company=p.get("company"), setup=p["setup"], regime=p.get("regime"),
                suggested=[p["asof"]], close_at_pick=p["close"], entry=p["entry"], stop=p["stop"], t1=p["t1"],
                status="WAITING", entry_date=None, fill=None, exit_date=None, exit_price=None,
                days=None, last_close=None, result_pct=None, r_mult=None, flag="")


def finish(t, status, date, price):
    t.update(status=status, exit_date=date, exit_price=round(price, 2))
    t["result_pct"] = round((price / t["fill"] - 1) * 100, 2)
    t["r_mult"] = round((price - t["fill"]) / (t["fill"] - t["stop"]), 2) if t["fill"] > t["stop"] else None


def check_exit(t, r):
    o, h, l, c = r.Open, r.High, r.Low, r.Close
    hit_stop, hit_t1 = l <= t["stop"], h >= t["t1"]
    if hit_stop and hit_t1:
        if o >= t["t1"]:
            return finish(t, "TARGET HIT", r.Date, o)
        t["flag"] = "stop and target both touched that day; counted as stop"
        return finish(t, "STOP HIT", r.Date, min(o, t["stop"]))
    if hit_stop:
        return finish(t, "STOP HIT", r.Date, min(o, t["stop"]))
    if hit_t1:
        return finish(t, "TARGET HIT", r.Date, max(o, t["t1"]))
    if t["days"] >= MAX_DAYS:
        return finish(t, "EXPIRED", r.Date, c)


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
            if r.Open > cur["entry"] * (1 + SKIP_GAP_PCT / 100):
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
            cur["result_pct"] = round((r.Close / cur["fill"] - 1) * 100, 2)
        # 2) a new pick published after today's close
        p = by_date.get(r.Date)
        if not p:
            continue
        if cur and cur["status"] == "OPEN":
            cur["suggested"].append(r.Date)
        elif cur and cur["status"] in ("NOT TRIGGERED", "SKIPPED") and cur.get("eval_date") == r.Date:
            cur["suggested"].append(r.Date)
            cur.update(status="WAITING", entry=p["entry"], stop=p["stop"], t1=p["t1"], setup=p["setup"], flag="")
        else:
            if cur:
                trades.append(cur)
            cur = new_trade(p)
    for p in picks:                                      # picks newer than the last price row
        if not rows or p["asof"] > rows[-1].Date:
            if cur and cur["status"] in ("WAITING", "OPEN") and p["asof"] not in cur["suggested"]:
                cur["suggested"].append(p["asof"])
            elif not cur or p["asof"] not in cur["suggested"]:
                if cur:
                    trades.append(cur)
                cur = new_trade(p)
    if cur:
        trades.append(cur)
    for t in trades:
        t.pop("eval_date", None)
        t["cmp"] = rows[-1].Close if rows else None          # latest NSE close for the dashboard
        t["cmp_date"] = rows[-1].Date if rows else None
        t["times_suggested"] = len(t["suggested"])
        t["t1_pct"] = round((t["t1"] / t["entry"] - 1) * 100, 1)
        t["stop_pct"] = round((t["stop"] / t["entry"] - 1) * 100, 1)
    return trades


def summarize(trades):
    done = [t for t in trades if t["status"] in ("TARGET HIT", "STOP HIT", "EXPIRED")]
    tgt = [t for t in done if t["status"] == "TARGET HIT"]
    stp = [t for t in done if t["status"] == "STOP HIT"]
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None

    def group(key):
        out = {}
        for t in trades:
            k = key(t)
            g = out.setdefault(k, dict(picks=0, triggered=0, target=0, stop=0, expired=0, open=0))
            g["picks"] += 1
            g["triggered"] += t["entry_date"] is not None
            g["target"] += t["status"] == "TARGET HIT"
            g["stop"] += t["status"] == "STOP HIT"
            g["expired"] += t["status"] == "EXPIRED"
            g["open"] += t["status"] == "OPEN"
        for g in out.values():
            n = g["target"] + g["stop"]
            g["win_rate"] = round(g["target"] / n * 100) if n else None
        return out

    count = lambda s: sum(t["status"] == s for t in trades)
    return dict(
        stock_ideas=len(trades), triggered=sum(t["entry_date"] is not None for t in trades),
        target_hit=len(tgt), stop_hit=len(stp), expired=count("EXPIRED"), open=count("OPEN"),
        waiting=count("WAITING"), not_triggered=count("NOT TRIGGERED"), skipped=count("SKIPPED"),
        data_issue=count("DATA ISSUE"),
        win_rate=round(len(tgt) / (len(tgt) + len(stp)) * 100) if tgt or stp else None,
        avg_days_to_target=avg([t["days"] for t in tgt]), avg_days_to_stop=avg([t["days"] for t in stp]),
        avg_result_pct=avg([t["result_pct"] for t in done]), avg_r=avg([t["r_mult"] for t in done if t["r_mult"] is not None]),
        by_setup=group(lambda t: t["setup"]),
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
                rules=dict(skip_gap_pct=SKIP_GAP_PCT, max_days=MAX_DAYS), missing_price_files=missing,
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
