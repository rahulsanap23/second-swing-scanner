#!/usr/bin/env python3
"""Indian swing screener: stage-2 momentum leaders.

Every threshold comes from a published source (named next to it); see README.md.
Usage: python3 screener.py <eod2_data_dir> [--json out.json]
"""
import argparse
import json
import math
import os

import numpy as np
import pandas as pd

P = dict(
    capital=500000,        # account size in Rs (user setting)
    min_turnover_cr=2.5,   # 20-day median turnover, Rs crore = 50x account (Qullamaggie); Indian backtests use a Rs 1 cr floor
    min_price=20.0,        # PKScreener default minimum price
    min_history=252,       # >= 1 year listed (NSE momentum index eligibility)
    max_circuit_frac=0.20, # drop stocks hitting circuit on >= 20% of days (NSE Midcap150 Momentum 50 rule)
    rs_min=70,             # Minervini: RS rating >= 70
    rs_pref=85,            # Minervini / RS-leader lists: 85+ preferred
    near_high=0.75,        # Minervini: within 25% of 52-week high
    above_low=1.30,        # Minervini: >= 30% above 52-week low
    prior_run=1.30,        # Qullamaggie: >= 30% low-to-high run in the last 126 sessions
    atr_pct_max=5.0,       # sharpely VCP: ATR(14) < 5% of price
    range21_max=10.0,      # sharpely VCP: 1-month range < 10% of price
    ready_within=3.0,      # India Qullamaggie port: READY when close is within 3% below the pivot
    bo_vol_mult=1.5,       # India Qullamaggie port: breakout volume >= 1.5x 50-day average
    max_ext_ema21=10.0,    # Kasliwal: skip if price is > 7-10% above the 21 EMA
    max_stop_pct=7.0,      # Kasliwal / Minervini: stop no wider than ~7%
    risk_per_trade=0.5,    # Qullamaggie: risk 0.25-1% of capital per trade
    max_position=25.0,     # Qullamaggie: positions 5-25% of capital
)

SECTORS = ["nifty auto", "nifty bank", "nifty it", "nifty pharma", "nifty fmcg", "nifty metal",
           "nifty realty", "nifty energy", "nifty pse", "nifty psu bank", "nifty private bank",
           "nifty financial services", "nifty healthcare index", "nifty consumer durables",
           "nifty oil & gas", "nifty media", "nifty capital markets", "nifty india defence",
           "nifty chemicals", "nifty infrastructure", "nifty commodities", "nifty india consumption",
           "nifty cement", "nifty capital goods", "nifty telecommunications", "nifty power"]
ACTIONABLE = ("BREAKOUT", "READY", "PULLBACK")


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def load(path, rows=320):
    d = pd.read_csv(path)
    if d.empty:
        return None
    d = d.tail(rows).copy()
    d["Date"] = pd.to_datetime(d["Date"])
    d = d[~d["Date"].duplicated(keep="last")]
    return d.set_index("Date")


def circuit_frac(d):
    c, h, l = d.Close, d.High, d.Low
    r = (c / c.shift(1) - 1) * 100
    hit = pd.Series(False, index=d.index)
    for b in (2, 5, 10, 20):
        hit |= ((c == h) & (r.sub(b).abs() <= 0.15)) | ((c == l) & (r.add(b).abs() <= 0.15))
    hit |= (h == l) & (r.abs() >= 1.9)
    return hit.tail(250).mean()


def index_state(d):
    c = d.Close
    f = lambda x, n=2: round(float(x), n)
    return dict(close=f(c.iloc[-1]), sma50=f(c.rolling(50).mean().iloc[-1]),
                sma200=f(c.rolling(200).mean().iloc[-1]),
                ema10=f(ema(c, 10).iloc[-1]), ema20=f(ema(c, 20).iloc[-1]),
                pct_off_high=f((c.iloc[-1] / d.High.tail(252).max() - 1) * 100, 1),
                ret_1m=f((c.iloc[-1] / c.iloc[-22] - 1) * 100, 1))


def run(data_dir, prev=None):
    daily = os.path.join(data_dir, "daily")
    s2i = json.load(open(os.path.join(data_dir, "isin_symbol_map.json")))["sym2isin"]
    s2i = {k.lower(): v for k, v in s2i.items()}

    # ---------- Scan date = latest date that most stock files have (indices may run a day ahead)
    from collections import Counter
    last = Counter()
    for fn in os.listdir(daily):
        isin = s2i.get(fn[:-4])
        if isin and isin.startswith("INE"):
            with open(os.path.join(daily, fn), "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 200))
                last[fh.read().decode(errors="ignore").strip().splitlines()[-1][:10]] += 1
    top = max(last.values())
    asof = pd.Timestamp(max(d for d, n in last.items() if n >= 0.5 * top))

    # ---------- Market regime (Clenow index > 200 DMA; Qullamaggie 10 > 20 MA; MarketSmith market direction)
    n500 = load(os.path.join(daily, "nifty 500.csv")).loc[:asof]
    n50 = load(os.path.join(daily, "nifty 50.csv")).loc[:asof]
    s500, s50 = index_state(n500), index_state(n50)
    s50["date"] = str(n50.index[-1].date())
    conds = [s500["close"] > s500["sma200"], s500["ema10"] > s500["ema20"]]
    regime = "GREEN" if all(conds) else ("YELLOW" if any(conds) else "RED")

    # ---------- Sector leadership (50-session RS vs Nifty 50)
    sectors = []
    for s in SECTORS:
        f = os.path.join(daily, s + ".csv")
        if not os.path.exists(f):
            continue
        d = load(f).loc[:asof]
        j = d.Close.align(n50.Close, join="inner")
        rsl = j[0] / j[1]
        rs50 = (rsl.iloc[-1] / rsl.iloc[-51] - 1) * 100
        name = " ".join(t.upper() if t in ("it", "pse", "psu", "fmcg") else t.title() for t in s[6:].split())
        sectors.append(dict(sector=name, rs50_vs_nifty=round(float(rs50), 1), date=str(d.index[-1].date()),
                            rs_rising=bool(rsl.iloc[-1] > rsl.tail(10).mean()),
                            above_50dma=bool(d.Close.iloc[-1] > d.Close.rolling(50).mean().iloc[-1])))
    sectors.sort(key=lambda x: -x["rs50_vs_nifty"])

    # ---------- Per-stock metrics
    rows, bars = [], {}
    for fn in os.listdir(daily):
        sym = fn[:-4]
        isin = s2i.get(sym)
        if not isin or not isin.startswith("INE"):   # equities only (drops ETFs 'INF', SME, indices)
            continue
        d = load(os.path.join(daily, fn))
        if d is not None:
            d = d.loc[:asof]
        if d is None or d.empty or d.index[-1] != asof:
            continue
        bars[sym.upper()] = dict(high=float(d.High.iloc[-1]), low=float(d.Low.iloc[-1]), close=float(d.Close.iloc[-1]))
        if len(d) < P["min_history"] or str(d["Series"].iloc[-1]) != "EQ":   # BE / trade-to-trade excluded
            continue
        c, h, l, v = d.Close, d.High, d.Low, d.Volume
        C = c.iloc[-1]
        sma50, sma150, sma200 = (c.rolling(n).mean() for n in (50, 150, 200))
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr14, atr50 = tr.rolling(14).mean().iloc[-1], tr.rolling(50).mean().iloc[-1]
        e10, e20, e21 = ema(c, 10), ema(c, 20), ema(c, 21)
        r = lambda n: C / c.iloc[-1 - n] - 1
        lr = np.log(c / c.shift()).tail(252)
        sigma = lr.std() * np.sqrt(252)
        hi252, lo252 = h.tail(252).max(), l.tail(252).min()
        w = d.tail(126)
        ih = int(np.argmax(w.High.values))
        run_ = w.High.iloc[ih] / w.Low.iloc[: ih + 1].min()
        j = c.align(n500.Close, join="inner")
        rsl = j[0] / j[1]
        rows.append(dict(
            symbol=sym.upper(), close=C,
            turnover_cr=float((c * v).tail(20).median() / 1e7),
            circuit_frac=float(circuit_frac(d)),
            sma50=sma50.iloc[-1], sma150=sma150.iloc[-1], sma200=sma200.iloc[-1], sma200_1m=sma200.iloc[-23],
            hi252=hi252, lo252=lo252,
            new_high=bool(h.iloc[-1] >= h.iloc[-252:-1].max()), new_low=bool(l.iloc[-1] <= l.iloc[-252:-1].min()),
            rs_raw=0.4 * r(63) + 0.2 * r(126) + 0.2 * r(189) + 0.2 * r(251),
            r21=r(21), r63=r(63), r126=r(126), r251=r(251),
            volmom=(r(126) / sigma + r(251) / sigma) / 2 if sigma > 0 else np.nan,
            rs65=float(rsl.iloc[-1] / rsl.iloc[-66] - 1),
            atr_pct=atr14 / C * 100, atr_contract=bool(atr14 < atr50),
            adr=float((h / l - 1).tail(20).mean() * 100),
            vol_dry=bool(v.tail(20).mean() < v.tail(50).mean()),
            vol_ratio=float(v.iloc[-1] / v.tail(51).head(50).mean()),
            range21=(h.tail(21).max() - l.tail(21).min()) / C * 100,
            no_undercut=bool(l.tail(10).min() >= l.iloc[-21:-10].min()),
            pivot=float(h.iloc[-22:-1].max()), prior_run=float(run_),
            ema20=e20.iloc[-1], ema20_rising=bool(e20.iloc[-1] > e20.iloc[-6]),
            ema21=e21.iloc[-1], ema10=e10.iloc[-1],
            touched20=bool((l.tail(3) <= e20.tail(3) * 1.01).any()),
            up_today=bool(C > c.iloc[-2]),
            low=l.iloc[-1], high=h.iloc[-1], low3=l.tail(3).min(),
            gap15=bool(((d.Open / c.shift() - 1).abs().tail(90) >= 0.15).any())))  # Clenow 15% gap flag
    df = pd.DataFrame(rows)
    # RS rating: IBD-style weighted 3/6/9/12-month return, percentile across all listed equities
    df["rs_rating"] = (df.rs_raw.rank(pct=True) * 99).round().clip(1, 99).astype(int)

    breadth = dict(date=str(asof.date()), stocks=len(df),
                   pct_above_50dma=round(float((df.close > df.sma50).mean() * 100), 1),
                   pct_above_200dma=round(float((df.close > df.sma200).mean() * 100), 1),
                   net_new_highs=int(df.new_high.sum() - df.new_low.sum()))

    funnel = {"equities_with_1y_history": len(df)}
    u = df[(df.close >= P["min_price"]) & (df.turnover_cr >= P["min_turnover_cr"]) &
           (df.circuit_frac < P["max_circuit_frac"])].copy()
    funnel["tradable"] = len(u)
    tt = ((u.close > u.sma150) & (u.close > u.sma200) & (u.sma150 > u.sma200) & (u.sma200 > u.sma200_1m) &
          (u.sma50 > u.sma150) & (u.sma50 > u.sma200) & (u.close > u.sma50) &
          (u.close >= P["above_low"] * u.lo252) & (u.close >= P["near_high"] * u.hi252))
    u = u[tt]
    funnel["trend_template"] = len(u)
    u = u[u.rs_rating >= P["rs_min"]].copy()
    funnel["rs_70_plus"] = len(u)

    # ---------- Setup classification
    def classify(x):
        if (x.close / x.ema21 - 1) * 100 > P["max_ext_ema21"]:
            return "EXTENDED"
        run_ok = x.prior_run >= P["prior_run"]
        tight = (x.atr_pct < P["atr_pct_max"] and x.atr_contract and x.vol_dry and
                 x.range21 < P["range21_max"] and x.no_undercut)
        if run_ok and x.close > x.pivot and x.vol_ratio >= P["bo_vol_mult"]:
            return "BREAKOUT"
        if run_ok and tight and x.close <= x.pivot and (x.pivot - x.close) / x.pivot * 100 <= P["ready_within"]:
            return "READY"
        if tight:
            return "BASING"
        if x.ema20_rising and x.touched20 and x.close > x.ema20 and x.up_today:
            return "PULLBACK"
        return "TREND"

    if len(u):
        u["setup"] = u.apply(classify, axis=1)
    else:
        u["setup"] = pd.Series(dtype=str)
    u["ext_ema21"] = (u.close / u.ema21 - 1) * 100
    u["off_high"] = (u.close / u.hi252 - 1) * 100

    def plan(x):
        cap = min(x.adr, P["max_stop_pct"])
        note = "gap>=15% in 90d (check news/split)" if x.gap15 else ""
        empty = dict(entry=np.nan, stop=np.nan, risk_pct=np.nan, pos_pct=np.nan, qty=np.nan, amount=np.nan,
                     t1=np.nan, trail="", note=note)
        if x.setup in ("READY", "BREAKOUT"):
            # Qullamaggie: stop = entry-day low, never wider than 1 ADR; Kasliwal/Minervini: <= 7%
            entry = (x.pivot if x.setup == "READY" else x.high) * 1.002
            stop = entry * (1 - cap / 100)
            note = ("or entry-day low if tighter; " + note).strip("; ")
        elif x.setup == "PULLBACK":
            # stock-signals: structure stop (3-day swing low) kept within 1-3 ATR; still <= 7%
            entry = x.high * 1.002
            atr = x.atr_pct / 100 * x.close
            stop = min(max(x.low3, entry - 3 * atr), entry - atr)
            stop = max(stop, entry * (1 - P["max_stop_pct"] / 100))
        else:
            return pd.Series(empty)
        risk = (entry - stop) / entry * 100
        qty = math.floor(P["capital"] * P["risk_per_trade"] / 100 / (entry - stop))
        qty = min(qty, math.floor(P["capital"] * P["max_position"] / 100 / entry))
        return pd.Series(dict(entry=entry, stop=stop, risk_pct=risk, pos_pct=qty * entry / P["capital"] * 100,
                              qty=qty, amount=qty * entry, t1=entry * (1 + 2.5 * x.adr / 100),
                              trail="10 EMA" if x.adr >= 5 else "20 EMA", note=note))

    if len(u):
        u = pd.concat([u, u.apply(plan, axis=1)], axis=1)
        u["o"] = u.setup.map({"BREAKOUT": 0, "READY": 1, "PULLBACK": 2, "BASING": 3, "TREND": 4, "EXTENDED": 5})
        u = u.sort_values(["o", "rs_rating", "volmom"], ascending=[True, False, False])
    funnel["actionable_setups"] = int(u.setup.isin(ACTIONABLE).sum()) if len(u) else 0

    cols = ["symbol", "setup", "rs_rating", "close", "off_high", "ext_ema21", "adr", "turnover_cr", "r63", "r126",
            "rs65", "volmom", "sma50", "ema21", "pivot", "entry", "stop", "risk_pct", "pos_pct", "qty", "amount",
            "t1", "trail", "note"]
    cands = json.loads(u[cols].round(2).to_json(orient="records")) if len(u) else []

    # ---------- Yesterday's shortlist: did the buy-stop fill today? did the stop get hit?
    follow = []
    if prev and prev.get("asof") and prev["asof"] < str(asof.date()):
        for c in prev.get("best5") or prev.get("candidates", []):
            if c.get("setup") not in ACTIONABLE or c.get("entry") is None:
                continue
            b = bars.get(c["symbol"])
            if not b:
                follow.append(dict(symbol=c["symbol"], result="no data today"))
                continue
            hit = b["high"] >= c["entry"]
            stopped = b["low"] <= c["stop"]
            res = ("TRIGGERED, stop also touched (check order of moves)" if hit and stopped else
                   "TRIGGERED" if hit else "STOP LEVEL BROKEN, setup void" if stopped else "not triggered")
            follow.append(dict(symbol=c["symbol"], setup=c["setup"], entry=c["entry"], stop=c["stop"],
                               high=b["high"], low=b["low"], close=b["close"], result=res))

    return dict(asof=str(asof.date()), regime=regime,
                regime_reason=dict(n500_above_200sma=bool(conds[0]), n500_ema10_above_ema20=bool(conds[1])),
                nifty500=s500, nifty50=s50, breadth=breadth, sectors_top=sectors[:6], sectors_bottom=sectors[-3:],
                funnel=funnel, params=P, previous_asof=(prev or {}).get("asof"), yesterday=follow,
                candidates=cands)


def to_markdown(o, status=None):
    L = [f"# Swing shortlist: data as of {o['asof']}", ""]
    if status:
        fresh = "fresh" if status.get("fresh") else f"STALE (expected {status.get('expected_date')})"
        L += [f"Data status: {fresh}. Run at {status.get('run_at_ist')}.", ""]
    rr = o["regime_reason"]
    L += [f"**Regime: {o['regime']}** (Nifty 500 above 200-day SMA: {rr['n500_above_200sma']}; "
          f"10 EMA above 20 EMA: {rr['n500_ema10_above_ema20']})",
          f"Nifty 50 {o['nifty50']['close']} (50-day {o['nifty50']['sma50']}, 200-day {o['nifty50']['sma200']}); "
          f"Nifty 500 {o['nifty500']['close']} (200-day {o['nifty500']['sma200']})",
          f"Breadth: {o['breadth']['pct_above_50dma']}% above 50-day, {o['breadth']['pct_above_200dma']}% above "
          f"200-day, net new highs {o['breadth']['net_new_highs']}",
          "Top sectors (50-day RS vs Nifty): " + ", ".join(f"{s['sector']} {s['rs50_vs_nifty']:+}" for s in o["sectors_top"][:3]),
          f"Funnel: {o['funnel']}", ""]
    if "best5" in o:
        L += ["## Best 5", ""]
        if o["regime"] == "RED":
            L += ["**Market is RED: the research rule is no new buys. These are the best 5 if you buy anyway.**", ""]
        L += ["| # | Symbol | Setup | RS | Close | Buy above | Stop | Qty | Amount | Target 1 | Trail | Profit YoY | Next results |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for i, c in enumerate(o["best5"], 1):
            yoy = f"{c['profit_yoy_pct']:+.0f}% ({c.get('latest_quarter')})" if c.get("profit_yoy_pct") is not None else "n/a"
            L.append(f"| {i} | {c['symbol']} | {c['setup']} | {c['rs_rating']} | {c['close']:.2f} | {c['entry']:.2f} | "
                     f"{c['stop']:.2f} | {c['qty']:.0f} | {c['amount']:,.0f} | {c['t1']:.2f} | {c['trail']} | {yoy} | "
                     f"{c.get('next_results') or 'not announced'} |")
        if not o["best5"]:
            L.append("| - | no stock passed today | | | | | | | | | | | |")
        if o.get("skipped"):
            L += ["", "Skipped: " + ", ".join(f"{x['symbol']} ({x['reason']})" for x in o["skipped"])]
        L.append("")
    setups = [c for c in o["candidates"] if c["setup"] in ACTIONABLE + ("BASING",)]
    L += ["## Setups", "", "| Symbol | Setup | RS | Close | Entry | Stop | Risk % | Qty | Amount | Target 1 | Trail |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in setups:
        g = lambda k, f="{:.2f}": f.format(c[k]) if c.get(k) is not None else "-"
        L.append(f"| {c['symbol']} | {c['setup']} | {c['rs_rating']} | {g('close')} | {g('entry')} | {g('stop')} | "
                 f"{g('risk_pct', '{:.1f}')} | {g('qty', '{:.0f}')} | {g('amount', '{:,.0f}')} | {g('t1')} | {c['trail'] or '-'} |")
    if not setups:
        L.append("| none | | | | | | | | | | |")
    lead = [c for c in o["candidates"] if c["setup"] == "TREND"][:5]
    L += ["", "## Strongest without a setup", "", "| Symbol | RS | Close | % off high | Pivot |", "|---|---|---|---|---|"]
    L += [f"| {c['symbol']} | {c['rs_rating']} | {c['close']} | {c['off_high']:.1f} | {c['pivot']} |" for c in lead]
    ext = [c["symbol"] for c in o["candidates"] if c["setup"] == "EXTENDED"]
    L += ["", "Extended, don't chase: " + (", ".join(ext) or "none")]
    if o["yesterday"]:
        L += ["", f"## Previous shortlist ({o['previous_asof']}) today", ""]
        L += [f"- {y['symbol']}: {y['result']}" for y in o["yesterday"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--json", default=None)
    ap.add_argument("--prev", default=None)
    a = ap.parse_args()
    prev = json.load(open(a.prev)) if a.prev and os.path.exists(a.prev) else None
    out = run(a.data_dir, prev)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    print(to_markdown(out))
