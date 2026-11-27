#!/usr/bin/env python3
"""Replay the screener's DIP rules on every trading day and follow each trade with the tracker's exits.

Builds wide (date x stock) panels from the EOD2 dataset with the same formulas as screener.py, then for
each signal day ranks DIP stocks, places the next-day limit order and follows the fill to the disaster stop,
a close above the 10 EMA, or the close of day 5. Costs: 0.25% a round trip (Zerodha delivery, approx).

Fills are STRICT by default: a limit counts as filled only if the day's low trades at least 0.2% below it
(or the stock opens below it). "touch" fills (low <= limit) overstate results because a bare touch often
doesn't fill you in real life.

Usage:
  python3 backtest.py <eod2_data_dir>                    # compare the old rules with the current ones
  python3 backtest.py <eod2_data_dir> --variants          # the full comparison table used in the README
"""
import argparse
import json
import os
import pickle

import numpy as np
import pandas as pd

import screener

COST = 0.25          # % round trip
START = "2016-06-01"  # warm-up for 200-day averages and 1-year RS before trading from 2018


def load_panels(data_dir, cache="/tmp/swing_panels.pkl"):
    if cache and os.path.exists(cache):
        return pickle.load(open(cache, "rb"))
    daily = os.path.join(data_dir, "daily")
    s2i = {k.lower(): v for k, v in json.load(open(os.path.join(data_dir, "isin_symbol_map.json")))["sym2isin"].items()}
    cols = {k: {} for k in ("Open", "High", "Low", "Close", "Volume", "EQ")}
    for fn in os.listdir(daily):
        isin = s2i.get(fn[:-4])
        if not isin or not isin.startswith("INE"):
            continue
        d = pd.read_csv(os.path.join(daily, fn), usecols=["Date", "Open", "High", "Low", "Close", "Volume", "Series"])
        d = d[d.Date >= START]
        if len(d) < 60:
            continue
        d["Date"] = pd.to_datetime(d.Date)
        d = d.drop_duplicates("Date", keep="last").set_index("Date")
        s = fn[:-4].upper()
        for k in ("Open", "High", "Low", "Close", "Volume"):
            cols[k][s] = d[k].astype(float)
        cols["EQ"][s] = (d.Series.astype(str) == "EQ")
    px = {k: pd.DataFrame(v).sort_index() for k, v in cols.items()}
    idx = pd.read_csv(os.path.join(daily, "nifty 500.csv"), usecols=["Date", "Close"])
    idx["Date"] = pd.to_datetime(idx.Date)
    idx = idx.drop_duplicates("Date", keep="last").set_index("Date").Close
    dates = px["Close"].index[px["Close"].notna().sum(axis=1) > 300]
    px = {k: v.reindex(dates) for k, v in px.items()}
    px["n500"] = idx.reindex(dates).ffill()
    if cache:
        pickle.dump(px, open(cache, "wb"))
    return px


def features(px):
    P = screener.P
    O, H, L, C, V = (px[k] for k in ("Open", "High", "Low", "Close", "Volume"))
    f = {}
    listed = C.notna().cumsum()                 # sessions of history in the window
    f["hist_ok"] = listed >= P["min_history"]
    f["eq"] = px["EQ"].fillna(False).astype(bool)
    f["turnover"] = (C * V).rolling(20, min_periods=15).median() / 1e7
    r = (C / C.shift() - 1) * 100
    hit = pd.DataFrame(False, index=C.index, columns=C.columns)
    for b in (2, 5, 10, 20):
        hit |= ((C == H) & ((r - b).abs() <= 0.15)) | ((C == L) & ((r + b).abs() <= 0.15))
    hit |= (H == L) & (r.abs() >= 1.9)
    f["circuit"] = hit.astype(float).rolling(250, min_periods=60).mean()
    s50, s150, s200 = (C.rolling(n, min_periods=n).mean() for n in (50, 150, 200))
    hi252, lo252 = H.rolling(252, min_periods=200).max(), L.rolling(252, min_periods=200).min()
    f["tt"] = ((C > s150) & (C > s200) & (s150 > s200) & (s200 > s200.shift(22)) & (s50 > s150) & (s50 > s200)
               & (C > s50) & (C >= P["above_low"] * lo252) & (C >= P["near_high"] * hi252))
    ret = lambda n: C / C.shift(n) - 1
    rs_raw = 0.4 * ret(63) + 0.2 * ret(126) + 0.2 * ret(189) + 0.2 * ret(251)
    rs_raw = rs_raw.where(f["hist_ok"])
    f["rs"] = (rs_raw.rank(axis=1, pct=True) * 99).round().clip(1, 99)
    dc = C.diff()
    up = dc.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-dc.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    f["rsi2"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    f["ema10"] = C.ewm(span=10, adjust=False).mean()
    tr = np.maximum(H - L, np.maximum((H - C.shift()).abs(), (L - C.shift()).abs()))
    f["atr_pct"] = tr.rolling(14).mean() / C * 100
    f["gap15"] = ((O / C.shift() - 1).abs() >= 0.15).astype(float).rolling(90, min_periods=1).max() > 0
    f["up_close"] = C > H.shift()               # closed above yesterday's high (bounce confirmation)
    # breadth (all equities with 1 year of history), market regime from the Nifty 500
    univ = f["hist_ok"] & C.notna()
    f["breadth50"] = ((C > s50) & univ).sum(axis=1) / univ.sum(axis=1) * 100
    nh = ((H >= H.shift().rolling(251, min_periods=200).max()) & univ).sum(axis=1)
    nl = ((L <= L.shift().rolling(251, min_periods=200).min()) & univ).sum(axis=1)
    f["nnh"] = nh - nl
    f["nnh10"] = f["nnh"].rolling(10).mean()
    n = px["n500"]
    e10, e20 = n.ewm(span=10, adjust=False).mean(), n.ewm(span=20, adjust=False).mean()
    g = (n > n.rolling(200).mean()).astype(int) + (e10 > e20).astype(int)
    f["regime"] = g.map({2: "GREEN", 1: "YELLOW", 0: "RED"})
    return f


DEFAULT = dict(rsi2_max=30, limit_mode="pct", limit_pct=2.0, limit_pct_red=3.0, limit_atr=0.5, min_turnover=2.5,
               breadth_min=None, weak_min_turnover=None, weak_picks=None, first_dip_only=False, no_open_repeat=True,
               confirm=False, entry="limit", exit_mode="ema10", weak_limit_atr=None, exit_next_open=True, extra=None, buy_days=1, fill="strict", stop_pct=12.0, hold=5, picks=(5, 5, 3), start="2018-01-01",
               end=None)


def simulate(px, f, **kw):
    k = dict(DEFAULT, **kw)
    O, H, L, C = (px[x].values for x in ("Open", "High", "Low", "Close"))
    E = f["ema10"].values
    if k["exit_mode"] == "prevhigh":
        E = px["High"].shift().values
    elif k["exit_mode"] == "sma5":
        E = px["Close"].rolling(5).mean().values
    dates = px["Close"].index
    syms = np.array(px["Close"].columns)
    base = (f["hist_ok"] & f["eq"] & (px["Close"] >= screener.P["min_price"]) & (f["circuit"] < screener.P["max_circuit_frac"])
            & f["tt"] & (f["rs"] >= screener.P["rs_min"]) & ~f["gap15"]).values
    if k["extra"] is not None:
        base = base & k["extra"].reindex_like(px["Close"]).fillna(False).values
    turn = f["turnover"].values
    rsi = f["rsi2"].values
    dip = rsi < k["rsi2_max"]
    if k["confirm"]:          # yesterday was a dip, today closed above yesterday's high
        sig = np.roll(dip, 1, axis=0) & f["up_close"].values & (rsi < 70)
        sig[0] = False
    else:
        sig = dip
    if k["first_dip_only"]:
        prev = np.roll(dip, 1, axis=0)
        prev[0] = False
        sig = sig & ~prev
    atr = f["atr_pct"].values
    regime = f["regime"].values
    b50, nnh10 = f["breadth50"].values, f["nnh10"].values
    i0 = dates.searchsorted(pd.Timestamp(k["start"]))
    i1 = len(dates) - 1 if not k["end"] else dates.searchsorted(pd.Timestamp(k["end"]), side="right") - 1
    busy_until = {}
    trades = []
    for i in range(i0, min(i1, len(dates) - 1)):
        reg = regime[i]
        weak = bool(b50[i] < (k["breadth_min"] or 45) or nnh10[i] < 0)     # weak breadth, always reported
        n_pick = dict(zip(("GREEN", "YELLOW", "RED"), k["picks"]))[reg]
        min_t = k["min_turnover"]
        if weak and k["breadth_min"] is not None:
            if k["weak_picks"] == 0:
                continue
            if k["weak_picks"]:
                n_pick = min(n_pick, k["weak_picks"])
            if k["weak_min_turnover"]:
                min_t = max(min_t, k["weak_min_turnover"])
        ok = base[i] & sig[i] & (turn[i] >= min_t)
        js = np.where(ok)[0]
        if not len(js):
            continue
        js = js[np.argsort(rsi[i, js])]
        if k["no_open_repeat"]:
            js = [j for j in js if busy_until.get(j, -1) < i + 1]
        for j in js[:n_pick]:
            c = C[i, j]
            if k["entry"] == "open":
                lim = None
            elif weak and k["weak_limit_atr"]:
                lim = c * (1 - k["weak_limit_atr"] * atr[i, j] / 100)
            elif k["limit_mode"] == "atr":
                lim = c * (1 - k["limit_atr"] * atr[i, j] / 100)
            else:
                lim = c * (1 - (k["limit_pct_red"] if reg == "RED" else k["limit_pct"]) / 100)
            d = i + 1
            if np.isnan(O[d, j]):
                continue
            if lim is None:
                fill = O[d, j]
            else:
                trig = lim * 0.998 if k["fill"] == "strict" else lim
                fill = None
                for dd in range(i + 1, min(i + 1 + k["buy_days"], len(dates))):
                    if np.isnan(O[dd, j]):
                        continue
                    if O[dd, j] <= lim:
                        fill, d = O[dd, j], dd
                        break
                    if L[dd, j] <= trig:
                        fill, d = lim, dd
                        break
                if fill is None:
                    trades.append(dict(date=dates[i], sym=syms[j], filled=False, regime=reg, weak=weak,
                                       turn=turn[i, j], fwd=(C[min(d + 4, len(dates) - 1), j] / c - 1) * 100))
                    continue
            stop = fill * (1 - k["stop_pct"] / 100) if k["stop_pct"] else -1.0
            exitp, n, why = None, 0, None
            for t in range(d, min(d + k["hold"], len(dates))):
                n += 1
                if np.isnan(C[t, j]):
                    continue
                if L[t, j] <= stop:
                    exitp, why = min(O[t, j], stop) if t > d else stop, "stop"
                    break
                if C[t, j] > E[t, j]:
                    if k["exit_next_open"] and t + 1 < len(dates) and not np.isnan(O[t + 1, j]):
                        exitp, why = O[t + 1, j], "ema"
                    else:
                        exitp, why = C[t, j], "ema"
                    break
                if n >= k["hold"]:
                    if k["exit_next_open"] and t + 1 < len(dates) and not np.isnan(O[t + 1, j]):
                        exitp, why = O[t + 1, j], "day5"
                    else:
                        exitp, why = C[t, j], "day5"
                    break
            if exitp is None:            # ran out of data
                continue
            busy_until[j] = t
            trades.append(dict(date=dates[i], sym=syms[j], filled=True, regime=reg, weak=weak, turn=turn[i, j],
                               ret=(exitp / fill - 1) * 100 - COST, why=why, days=n,
                               fwd=(C[min(d + 4, len(dates) - 1), j] / c - 1) * 100))
    return pd.DataFrame(trades)


def stats(t, label=""):
    if t.empty or not t.filled.any():
        return dict(rules=label, trades=0)
    x = t[t.filled]
    yr = x.groupby(x.date.dt.year).ret.mean()
    days = t.date.nunique()
    return dict(rules=label, trades=len(x), per_day=round(len(x) / max(days, 1), 2),
                fill_rate=round(t.filled.mean() * 100), win=round((x.ret > 0).mean() * 100, 1),
                avg=round(x.ret.mean(), 2), median=round(x.ret.median(), 2),
                avg_win=round(x.ret[x.ret > 0].mean(), 2), avg_loss=round(x.ret[x.ret <= 0].mean(), 2),
                worst5pct=round(x.ret.quantile(0.05), 1), years_pos=f"{(yr > 0).sum()} of {len(yr)}",
                y2025_26=round(x[x.date >= "2025-01-01"].ret.mean(), 2),
                weak_tape=round(x[x.weak].ret.mean(), 2) if "weak" in x and x.weak.any() else None,
                unfilled_fwd5=round(t[~t.filled].fwd.median(), 2) if (~t.filled).any() else None,
                filled_fwd5=round(x.fwd.median(), 2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--variants", action="store_true")
    a = ap.parse_args()
    px = load_panels(a.data_dir)
    f = features(px)
    print(json.dumps(stats(simulate(px, f, fill="touch"), "old live rules, touch fills"), default=str))
    print(json.dumps(stats(simulate(px, f), "old live rules, strict fills"), default=str))
