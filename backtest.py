#!/usr/bin/env python3
"""History test: how did the scanner's setups do over the next week, in each market mood?

Replays the screener's rules on every trading day of the last N years (default 5) of EOD2 data,
then follows each setup for 5 trading sessions exactly the way the daily report tells you to trade it:

  * buy-stop at the planned entry on the next session; skipped if that session opens > 3% above it
  * stop-loss as planned; target tested in several versions (see TARGETS)
  * stop moves to the buy price after the trade is up 1R
  * time stop: sell at the close of the 5th session
  * Zerodha delivery charges taken off every trade (CHARGES_PCT round trip)
  * same session hits both stop and target -> counted as a stop (conservative)

Writes results/backtest.json (read by enrich.py to rank picks) and results/backtest.md (readable).
Usage: python3 backtest.py [eod2_dir] [--years 5]

Limits, stated plainly: EOD2 holds the stocks listed today, so companies that were delisted are missing
(this flatters results a little); results-date skips can't be replayed; the 6-month "prior run" test uses
the 126-day high / low instead of the low before the high.
"""
import argparse
import datetime as dt
import json
import os

import numpy as np
import pandas as pd

import screener

P = screener.P
ROOT = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(ROOT, "results")
HOLD = P.get("hold_sessions", 5)   # sessions: the one-week swing (time stop used by the live plan)
HOLDS_COMPARE = (5, 10, 20)        # holding periods compared for the live target
GAP_SKIP = 0.03             # skip if the next session opens > 3% above the entry
CHARGES_PCT = 0.25          # Zerodha delivery round trip: STT 0.1% x2, stamp, exchange, GST, DP (approx.)
TARGETS = {                 # target price as a function of entry, stop and ADR (%)
    "2.5 ADR": lambda e, s, a: e * (1 + P.get("t1_adr", 2.5) * a / 100),
    "4 ADR": lambda e, s, a: e * (1 + 4 * a / 100),
    "1.5 ADR": lambda e, s, a: e * (1 + 1.5 * a / 100),
    "1 ADR": lambda e, s, a: e * (1 + 1.0 * a / 100),
    "2R": lambda e, s, a: e + 2 * (e - s),
    "nearer of 1.5 ADR / 2R": lambda e, s, a: min(e * (1 + 1.5 * a / 100), e + 2 * (e - s)),
    "no target (sell on the last day)": lambda e, s, a: e * 1000,
}
LIVE_TARGET = "2.5 ADR"     # the target the daily plan prints (screener P["t1_adr"])
MIN_N = 30                  # fewer trades than this is "not enough history"


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def regime_series(daily):
    n5 = pd.read_csv(os.path.join(daily, "nifty 500.csv"), parse_dates=["Date"]).drop_duplicates("Date", keep="last")
    c = n5.set_index("Date").Close
    a, b = c > c.rolling(200).mean(), ema(c, 10) > ema(c, 20)
    reg = pd.Series(np.where(a & b, "GREEN", np.where(a | b, "YELLOW", "RED")), index=c.index)
    return reg, c


def stock_features(d):
    c, h, l, v, o = d.Close, d.High, d.Low, d.Volume, d.Open
    f = pd.DataFrame(index=d.index)
    f["o"], f["h"], f["l"], f["c"] = o, h, l, c
    f["is_eq"] = d["Series"].astype(str).eq("EQ")
    f["n"] = np.arange(1, len(d) + 1)
    f["sma50"], f["sma150"], f["sma200"] = (c.rolling(k).mean() for k in (50, 150, 200))
    f["sma200_1m"] = f.sma200.shift(22)
    f["hi252"], f["lo252"] = h.rolling(252).max(), l.rolling(252).min()
    r = lambda k: c / c.shift(k) - 1
    f["rs_raw"] = 0.4 * r(63) + 0.2 * r(126) + 0.2 * r(189) + 0.2 * r(251)
    f["r10"], f["r20"] = r(10), r(20)
    f["turnover_cr"] = (c * v).rolling(20).median() / 1e7
    ret = (c / c.shift() - 1) * 100
    hit = pd.Series(False, index=d.index)
    for bnd in (2, 5, 10, 20):
        hit |= ((c == h) & (ret.sub(bnd).abs() <= 0.15)) | ((c == l) & (ret.add(bnd).abs() <= 0.15))
    hit |= (h == l) & (ret.abs() >= 1.9)
    f["circuit_frac"] = hit.astype(float).rolling(250, min_periods=1).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr14, atr50 = tr.rolling(14).mean(), tr.rolling(50).mean()
    f["atr_pct"], f["atr_contract"] = atr14 / c * 100, atr14 < atr50
    f["adr"] = (h / l - 1).rolling(20).mean() * 100
    f["vol_dry"] = v.rolling(20).mean() < v.rolling(50).mean()
    f["vol_ratio"] = v / v.shift(1).rolling(50).mean()
    f["range21"] = (h.rolling(21).max() - l.rolling(21).min()) / c * 100
    f["no_undercut"] = l.rolling(10).min() >= l.shift(10).rolling(11).min()
    f["piv"] = h.shift(1).rolling(21).max()
    f["prior_run"] = h.rolling(126).max() / l.rolling(126).min()
    e20, e21 = ema(c, 20), ema(c, 21)
    f["ema20"], f["ema20_rising"], f["ema21"] = e20, e20 > e20.shift(5), e21
    f["touched20"] = (l <= e20 * 1.01).astype(float).rolling(3).max().astype(bool)
    f["up_today"] = c > c.shift()
    f["low3"] = l.rolling(3).min()
    f["gap15"] = ((o / c.shift() - 1).abs() >= 0.15).astype(float).rolling(90, min_periods=1).max().astype(bool)
    return f


def classify(f):
    ext = (f.c / f.ema21 - 1) * 100 > P["max_ext_ema21"]
    run_ok = f.prior_run >= P["prior_run"]
    tight = ((f.atr_pct < P["atr_pct_max"]) & f.atr_contract & f.vol_dry & (f.range21 < P["range21_max"]) &
             f.no_undercut)
    bo = run_ok & (f.c > f.piv) & (f.vol_ratio >= P["bo_vol_mult"])
    ready = run_ok & tight & (f.c <= f.piv) & ((f.piv - f.c) / f.piv * 100 <= P["ready_within"])
    pb = f.ema20_rising & f.touched20 & (f.c > f.ema20) & f.up_today
    s = pd.Series("", index=f.index)
    s[pb & ~tight] = "PULLBACK"
    s[ready] = "READY"
    s[bo] = "BREAKOUT"
    s[ext] = ""
    return s


def plan(setup, x):
    """Entry and stop exactly as screener.plan() computes them."""
    cap = min(x.adr, P["max_stop_pct"])
    if setup in ("READY", "BREAKOUT"):
        entry = (x.piv if setup == "READY" else x.h) * 1.002
        stop = entry * (1 - cap / 100)
    else:
        entry = x.h * 1.002
        atr = x.atr_pct / 100 * x.c
        stop = min(max(x.low3, entry - 3 * atr), entry - atr)
        stop = max(stop, entry * (1 - P["max_stop_pct"] / 100))
    return entry, stop


def follow(o, h, l, c, entry, stop, t1, hold=HOLD, at_open=False):
    """Trade the plan over the next HOLD sessions. o/h/l/c are arrays starting at the next session."""
    if len(o) < hold:
        return None
    o, h, l, c = o[:hold], h[:hold], l[:hold], c[:hold]
    if at_open:
        fill = o[0]
    else:
        if o[0] > entry * (1 + GAP_SKIP):
            return dict(outcome="GAP_SKIP")
        if h[0] < entry:
            return dict(outcome="NOT_TRIGGERED")
        fill = max(o[0], entry)
    if fill <= stop:
        return dict(outcome="NOT_TRIGGERED")
    risk, s, be_armed, out, px = fill - stop, stop, False, "TIME", c[hold - 1]
    for k in range(hold):
        if k > 0 and o[k] <= s:
            out, px = ("BREAKEVEN" if s >= fill else "STOP"), o[k]
            break
        if l[k] <= s:
            out, px = ("BREAKEVEN" if s >= fill else "STOP"), s
            break
        if k > 0 and o[k] >= t1:
            out, px = "TARGET", o[k]
            break
        if h[k] >= t1:
            out, px = "TARGET", max(t1, fill)
            break
        if not be_armed and h[k] >= fill + risk:
            be_armed = True
        if be_armed:
            s = max(s, fill)
    ret = (px / fill - 1) * 100 - CHARGES_PCT
    return dict(outcome=out, ret=ret, r=ret / 100 * fill / risk, days=k + 1)


def run(eod2, years=5, end=None):
    daily = os.path.join(eod2, "daily")
    s2i = {k.lower(): v for k, v in json.load(open(os.path.join(eod2, "isin_symbol_map.json")))["sym2isin"].items()}
    reg, n500 = regime_series(daily)
    end = pd.Timestamp(end) if end else reg.index[-1]
    start = end - pd.DateOffset(years=years)
    n500_r10 = n500 / n500.shift(10) - 1
    warm = start - pd.DateOffset(days=420)

    feats = {}
    for fn in sorted(os.listdir(daily)):
        sym = fn[:-4]
        isin = s2i.get(sym)
        if not isin or not isin.startswith("INE"):
            continue
        d = pd.read_csv(os.path.join(daily, fn), parse_dates=["Date"])
        d = d[~d.Date.duplicated(keep="last")].set_index("Date")
        n_before = int((d.index < warm).sum())
        d = d[d.index >= warm]
        if len(d) < 260:
            continue
        f = stock_features(d)
        f["n"] += n_before
        feats[sym.upper()] = f

    # RS rating: percentile of rs_raw across every listed equity on each date (as the screener does)
    rs = pd.DataFrame({s: f.rs_raw.where(f.n >= P["min_history"]) for s, f in feats.items()})
    rating = (rs.rank(axis=1, pct=True) * 99).round().clip(1, 99)

    rows = []
    for sym, f in feats.items():
        f = f.copy()
        f["rs_rating"] = rating[sym].reindex(f.index)
        ok = (f.is_eq & (f.n >= P["min_history"]) & (f.c >= P["min_price"]) &
              (f.turnover_cr >= P["min_turnover_cr"]) & (f.circuit_frac < P["max_circuit_frac"]) &
              (f.c > f.sma150) & (f.c > f.sma200) & (f.sma150 > f.sma200) & (f.sma200 > f.sma200_1m) &
              (f.sma50 > f.sma150) & (f.sma50 > f.sma200) & (f.c > f.sma50) &
              (f.c >= P["above_low"] * f.lo252) & (f.c >= P["near_high"] * f.hi252) &
              (f.rs_rating >= P["rs_min"]) & ~f.gap15 & (f.index >= start) & (f.index <= end))
        setup = classify(f)
        sig = ok & setup.ne("")
        if not sig.any():
            continue
        O, H, L, C = f.o.values, f.h.values, f.l.values, f.c.values
        for i in np.flatnonzero(sig.values):
            x = f.iloc[i]
            st = setup.iloc[i]
            date = f.index[i]
            entry, stop = plan(st, x)
            if not (np.isfinite(entry) and np.isfinite(stop)) or stop >= entry:
                continue
            nxt = slice(i + 1, i + 1 + max(HOLDS_COMPARE + (HOLD,)))
            if len(O[i + 1:i + 1 + HOLD]) < HOLD:
                continue
            base = dict(date=date, symbol=sym, setup=st, regime=reg.get(date, "RED"), rs=int(x.rs_rating),
                        r10=float(x.r10), n500_r10=float(n500_r10.get(date, np.nan)),
                        risk_pct=(entry - stop) / entry * 100, adr=float(x.adr))
            for name, tf in TARGETS.items():
                res = follow(O[nxt], H[nxt], L[nxt], C[nxt], entry, stop, tf(entry, stop, x.adr))
                rows.append(dict(base, target=name, **res))
            t_live = TARGETS[LIVE_TARGET](entry, stop, x.adr)
            res = follow(O[nxt], H[nxt], L[nxt], C[nxt], entry, stop, t_live, at_open=True)
            rows.append(dict(base, target="bought at open", **res))
            for hd in HOLDS_COMPARE:
                res = follow(O[nxt], H[nxt], L[nxt], C[nxt], entry, stop, t_live, hold=hd)
                if res is not None:
                    rows.append(dict(base, target=f"hold {hd}", **res))
    return pd.DataFrame(rows), dict(start=str(start.date()), end=str(end.date()), stocks=len(feats))


def stats(g):
    t = g[g.outcome.isin(["TARGET", "STOP", "TIME", "BREAKEVEN"])]
    n_sig = len(g)
    if not len(t):
        return dict(signals=n_sig, trades=0)
    oc = t.outcome.value_counts(normalize=True) * 100
    return dict(signals=n_sig, trades=len(t), trigger_pct=round(len(t) / n_sig * 100, 1),
                win_pct=round(float((t.ret > 0).mean() * 100), 1),
                target_pct=round(float(oc.get("TARGET", 0)), 1), stop_pct=round(float(oc.get("STOP", 0)), 1),
                breakeven_pct=round(float(oc.get("BREAKEVEN", 0)), 1), time_pct=round(float(oc.get("TIME", 0)), 1),
                avg_ret=round(float(t.ret.mean()), 2), median_ret=round(float(t.ret.median()), 2),
                avg_r=round(float(t.r.mean()), 2), avg_win=round(float(t.ret[t.ret > 0].mean()), 2) if (t.ret > 0).any() else None,
                avg_loss=round(float(t.ret[t.ret <= 0].mean()), 2) if (t.ret <= 0).any() else None,
                avg_days=round(float(t.days.mean()), 1), enough=len(t) >= MIN_N)


def summarize(df, meta):
    out = dict(generated=dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(timespec="minutes"),
               **meta, hold_sessions=HOLD, charges_pct=CHARGES_PCT, gap_skip_pct=GAP_SKIP * 100, min_trades=MIN_N,
               live_target=LIVE_TARGET)
    regimes, setups = ("GREEN", "YELLOW", "RED"), ("BREAKOUT", "READY", "PULLBACK")
    out["targets"] = {name: dict(all=stats(df[df.target == name]),
                                 by_regime={r: stats(df[(df.target == name) & (df.regime == r)]) for r in regimes})
                      for name in list(TARGETS) + ["bought at open"]}
    out["holds"] = {}
    for hd in HOLDS_COMPARE:
        g = df[df.target == f"hold {hd}"]
        out["holds"][hd] = dict(all=stats(g), by_setup_regime={
            s: {r: stats(g[(g.setup == s) & (g.regime == r)]) for r in regimes} for s in setups})
    g = df[df.target == LIVE_TARGET]
    # enrich.py ranks the daily picks by this table (average return of the live plan, per setup and market mood)
    out["by_setup_regime"] = {s: {r: stats(g[(g.setup == s) & (g.regime == r)]) for r in regimes} for s in setups}
    out["by_setup"] = {s: stats(g[g.setup == s]) for s in setups}
    red = g[g.regime == "RED"]
    lead = red[(red.r10 > 0) & (red.n500_r10 < 0) & (red.rs >= 90)]
    out["red_leaders"] = dict(rule="RS 90+, up over 10 sessions while Nifty 500 is down",
                              all_red=stats(red), leaders=stats(lead))
    out["regime_days"] = {r: int(g[g.regime == r].date.nunique()) for r in regimes}
    out["by_year"] = {int(y): stats(x) for y, x in g.assign(y=g.date.dt.year).groupby("y")}
    return out


def to_markdown(o):
    pct = lambda v: "" if v is None else f"{v:+.2f}%"
    H = o["hold_sessions"]
    row = lambda name, a: (f"| {name} | {a['trades']}{'' if a.get('enough') else ' (few)'} | {a['win_pct']} | "
                           f"{a['target_pct']} | {a['stop_pct']} | {pct(a['avg_ret'])} | {pct(a['avg_win'])} | "
                           f"{pct(a['avg_loss'])} |")
    head = ["| {} | Trades | Win % | Target hit % | Stop hit % | Avg return | Avg win | Avg loss |",
            "|---|---|---|---|---|---|---|---|"]
    L = [f"# History test: {o['start']} to {o['end']}", "",
         f"Every setup the scanner would have found on each trading day, traded the way the report says: buy-stop "
         f"next session (skipped if it opens > {o['gap_skip_pct']:.0f}% above), stop as planned, stop moved to the "
         f"buy price after +1R, sell at the close of day {H} if neither stop nor target was hit. Returns are after "
         f"about {o['charges_pct']}% Zerodha charges. {o['stocks']} stocks. Generated {o['generated']}.", "",
         f"## 1. Setup × market mood (live plan: target {o['live_target']}, sell on day {H})", "",
         "This is the table the daily ranking uses.", "", head[0].format("Setup · market"), head[1]]
    for s, d in o["by_setup_regime"].items():
        for r, a in d.items():
            if a.get("trades"):
                L.append(row(f"{s} · {r}", a))
    L += ["", f"## 2. Which target works best when selling on day {H}", "", head[0].format("Target"), head[1]]
    for k, v in o["targets"].items():
        if v["all"].get("trades"):
            L.append(row(k + (" (live)" if k == o["live_target"] else ""), v["all"]))
    L += ["", "\"Bought at open\" = buying at the next open without waiting for the Buy-above price.", "",
          f"## 3. How long to hold (target {o['live_target']})", "", head[0].format("Hold"), head[1]]
    for hd, v in o["holds"].items():
        if v["all"].get("trades"):
            L.append(row(f"{hd} sessions", v["all"]))
    L += ["", "Pullbacks only, by hold:", "", "| Hold | GREEN | YELLOW | RED |", "|---|---|---|---|"]
    for hd, v in o["holds"].items():
        p = v["by_setup_regime"]["PULLBACK"]
        L.append(f"| {hd} sessions | " + " | ".join(pct(p[r].get("avg_ret")) for r in ("GREEN", "YELLOW", "RED")) + " |")
    rl = o["red_leaders"]
    L += ["", "## 4. RED market: all setups vs leaders only", "", f"Leaders = {rl['rule']}.", "",
          head[0].format("Group"), head[1]]
    for k, a in (("All RED setups", rl["all_red"]), ("RED leaders", rl["leaders"])):
        if a.get("trades"):
            L.append(row(k, a))
    L += ["", "## 5. By year (live plan)", "", head[0].format("Year"), head[1]]
    for y, a in o["by_year"].items():
        if a.get("trades"):
            L.append(row(str(y), a))
    L += ["", "Limits: delisted companies are missing from the data (flatters results a little); results dates "
          "can't be replayed; overlapping signals on the same stock are counted separately. Past results do not "
          "promise future ones.", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eod2", nargs="?", default=os.environ.get("EOD2_DIR", "/tmp/eod2_data"))
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--trades-csv", default=None)
    a = ap.parse_args()
    df, meta = run(a.eod2, a.years)
    if a.trades_csv:
        df.to_csv(a.trades_csv, index=False)
    o = summarize(df, meta)
    os.makedirs(RES, exist_ok=True)
    json.dump(o, open(os.path.join(RES, "backtest.json"), "w"), indent=1, default=str)
    md = to_markdown(o)
    open(os.path.join(RES, "backtest.md"), "w").write(md)
    print(md)


if __name__ == "__main__":
    main()
