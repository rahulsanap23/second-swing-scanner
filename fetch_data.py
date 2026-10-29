#!/usr/bin/env python3
"""Bring the EOD2 NSE dataset up to the latest trading day.

EOD2 (github.com/BennyThadikaran/eod2_data) is split/bonus adjusted but updated about weekly.
This script appends every missing trading day from NSE's official daily files:
  stocks : sec_bhavdata_full_DDMMYYYY.csv, else the UDiFF BhavCopy zip
  indices: ind_close_all_DDMMYYYY.csv
If NSE refuses, it falls back to Yahoo Finance (stocks + Nifty 50/500 only).
Appended days are NOT split-adjusted; the screener flags any 15% gap.
"""
import datetime as dt
import io
import json
import os
import time
import zipfile

import pandas as pd
import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/csv,application/zip,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
SERIES_PRIORITY = ["EQ", "BE", "BZ"]          # main trading series, in order of preference
YAHOO_INDEX = {"nifty 50": "^NSEI", "nifty 500": "^CRSLDX"}
PUBLISH_HOUR_IST = 18                          # NSE daily files are usually online by ~6 PM IST


def holidays(eod2_dir):
    meta = json.load(open(os.path.join(eod2_dir, "meta.json")))
    out = set()
    for k in meta.get("holidays", {}):
        try:
            out.add(dt.datetime.strptime(k, "%d-%b-%Y").date())
        except ValueError:
            pass
    return out, meta


def is_trading_day(d, hol):
    return d.weekday() < 5 and d not in hol


def expected_date(hol, now=None):
    """Latest trading day whose files should be published by now."""
    now = now or dt.datetime.now(IST)
    d = now.date()
    if not (is_trading_day(d, hol) and now.hour >= PUBLISH_HOUR_IST):
        d -= dt.timedelta(days=1)
        while not is_trading_day(d, hol):
            d -= dt.timedelta(days=1)
    return d


def last_date(path):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 400))
        tail = f.read().decode(errors="ignore").strip().splitlines()
    return dt.date.fromisoformat(tail[-1].split(",")[0])


class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        try:
            self.s.get("https://www.nseindia.com", timeout=15)   # cookies; may be refused, that's fine
        except requests.RequestException:
            pass

    def get(self, url, tries=2):
        last = None
        for i in range(tries):
            try:
                r = self.s.get(url, timeout=25)
                if r.status_code == 200 and len(r.content) > 500:
                    return r.content
                last = f"HTTP {r.status_code} ({len(r.content)} bytes)"
            except requests.RequestException as e:
                last = type(e).__name__
            time.sleep(5 * (i + 1))
        raise RuntimeError(f"{url}: {last}")

    def stocks(self, d):
        errs = []
        try:
            raw = self.get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
            df = pd.read_csv(io.BytesIO(raw))
            df.columns = [c.strip() for c in df.columns]
            for c in df.columns:   # NSE pads text fields with spaces (pandas 3 uses a string dtype, not object)
                if not pd.api.types.is_numeric_dtype(df[c]):
                    df[c] = df[c].astype(str).str.strip()
            out = pd.DataFrame(dict(
                symbol=df.SYMBOL, series=df.SERIES,
                open=pd.to_numeric(df.OPEN_PRICE, errors="coerce"), high=pd.to_numeric(df.HIGH_PRICE, errors="coerce"),
                low=pd.to_numeric(df.LOW_PRICE, errors="coerce"), close=pd.to_numeric(df.CLOSE_PRICE, errors="coerce"),
                volume=pd.to_numeric(df.TTL_TRD_QNTY, errors="coerce"),
                trades=pd.to_numeric(df.NO_OF_TRADES, errors="coerce"),
                dlv=pd.to_numeric(df.DELIV_QTY, errors="coerce")))
            return out, "nse-sec_bhavdata_full"
        except Exception as e:  # noqa: BLE001
            errs.append(str(e))
        try:
            raw = self.get(f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
            z = zipfile.ZipFile(io.BytesIO(raw))
            df = pd.read_csv(z.open(z.namelist()[0]))
            out = pd.DataFrame(dict(
                symbol=df.TckrSymb.astype(str).str.strip(), series=df.SctySrs.astype(str).str.strip(),
                open=df.OpnPric, high=df.HghPric, low=df.LwPric, close=df.ClsPric,
                volume=df.TtlTradgVol, trades=df.TtlNbOfTxsExctd, dlv=float("nan")))
            return out, "nse-udiff-bhavcopy"
        except Exception as e:  # noqa: BLE001
            errs.append(str(e))
        raise RuntimeError(" | ".join(errs))

    def indices(self, d):
        raw = self.get(f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv")
        df = pd.read_csv(io.BytesIO(raw))
        df.columns = [c.strip() for c in df.columns]
        num = lambda c: pd.to_numeric(df[c].astype(str).str.replace(",", "").str.strip(), errors="coerce")
        return pd.DataFrame(dict(
            name=df["Index Name"].astype(str).str.strip().str.lower(),
            open=num("Open Index Value"), high=num("High Index Value"), low=num("Low Index Value"),
            close=num("Closing Index Value"), volume=num("Volume"), pe=num("P/E")))


def fmt(x):
    if pd.isna(x):
        return ""
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:.2f}".rstrip("0").rstrip(".")


def append_stocks(daily, d, df, src_note):
    df = df.dropna(subset=["close", "high", "low"])
    df = df[df.series.isin(SERIES_PRIORITY)].copy()
    df["rank"] = df.series.map({s: i for i, s in enumerate(SERIES_PRIORITY)})
    df = df.sort_values("rank").drop_duplicates("symbol")
    n = 0
    for r in df.itertuples():
        f = os.path.join(daily, r.symbol.lower() + ".csv")
        if not os.path.exists(f) or last_date(f) >= d:
            continue
        qpt = r.volume / r.trades if r.trades and r.trades == r.trades and r.trades > 0 else float("nan")
        line = ",".join([d.isoformat(), fmt(r.open), fmt(r.high), fmt(r.low), fmt(r.close), fmt(r.volume),
                         r.series, fmt(r.trades), fmt(qpt), fmt(r.dlv)])
        with open(f, "a") as fh:
            fh.write(line + "\n")
        n += 1
    return n


def append_indices(daily, d, df):
    n = 0
    for r in df.dropna(subset=["close"]).itertuples():
        f = os.path.join(daily, r.name + ".csv")
        if not os.path.exists(f) or last_date(f) >= d:
            continue
        o, h, l = (r.open if r.open == r.open else r.close), (r.high if r.high == r.high else r.close), \
            (r.low if r.low == r.low else r.close)
        line = ",".join([d.isoformat(), fmt(o), fmt(h), fmt(l), fmt(r.close), fmt(r.volume), fmt(r.pe), "", "", "", ""])
        with open(f, "a") as fh:
            fh.write(line + "\n")
        n += 1
    return n


def yahoo_fill(daily, days, s2i):
    """Fallback: fill missing days for equities and Nifty 50/500 from Yahoo Finance."""
    import yfinance as yf
    syms = sorted(s for s, isin in s2i.items() if isin.startswith("INE")
                  and os.path.exists(os.path.join(daily, s.lower() + ".csv")))
    start, end = min(days), max(days) + dt.timedelta(days=1)
    tick = {f"{s}.NS": os.path.join(daily, s.lower() + ".csv") for s in syms}
    tick.update({t: os.path.join(daily, n + ".csv") for n, t in YAHOO_INDEX.items()})
    names = list(YAHOO_INDEX.values()) + [t for t in tick if not t.startswith("^")]   # indices first
    added, empty_chunks = 0, 0
    for i in range(0, len(names), 150):
        chunk = names[i:i + 150]
        try:
            data = yf.download(chunk, start=start.isoformat(), end=end.isoformat(), group_by="ticker",
                               auto_adjust=False, progress=False, threads=True)
        except Exception:  # noqa: BLE001
            data = None
        if data is None or data.empty:
            empty_chunks += 1
            if empty_chunks >= 2 and added == 0:     # Yahoo is refusing too; give up quickly
                raise RuntimeError("Yahoo returned no data")
            time.sleep(10)
            continue
        for t in chunk:
            try:
                sub = data[t].dropna(subset=["Close"])
            except (KeyError, TypeError):
                continue
            f = tick[t]
            is_index = t.startswith("^")
            for ts, row in sub.iterrows():
                d = ts.date()
                if d not in days or last_date(f) >= d:
                    continue
                vol = row.get("Volume", float("nan"))
                if is_index:
                    line = ",".join([d.isoformat(), fmt(row.Open), fmt(row.High), fmt(row.Low), fmt(row.Close),
                                     fmt(vol), "", "", "", "", ""])
                else:
                    line = ",".join([d.isoformat(), fmt(row.Open), fmt(row.High), fmt(row.Low), fmt(row.Close),
                                     fmt(vol), "EQ", "", "", ""])
                with open(f, "a") as fh:
                    fh.write(line + "\n")
                added += 1
        time.sleep(3)
    return added


def update(eod2_dir, now=None):
    daily = os.path.join(eod2_dir, "daily")
    hol, meta = holidays(eod2_dir)
    s2i = json.load(open(os.path.join(eod2_dir, "isin_symbol_map.json")))["sym2isin"]
    exp = expected_date(hol, now)
    base = last_date(os.path.join(daily, "nifty 500.csv"))
    days = []
    d = base + dt.timedelta(days=1)
    while d <= exp:
        if is_trading_day(d, hol):
            days.append(d)
        d += dt.timedelta(days=1)

    status = dict(run_at_ist=dt.datetime.now(IST).isoformat(timespec="minutes"), expected_date=exp.isoformat(),
                  eod2_last_update=str(meta.get("lastUpdate", ""))[:10], days_needed=[x.isoformat() for x in days],
                  days=[], errors=[])
    nse = NSE() if days else None
    yahoo_days, consecutive_fail = [], 0
    for d in days:
        rec = dict(date=d.isoformat())
        if consecutive_fail >= 2:            # NSE is refusing this runner: stop hammering it, go to Yahoo
            rec["stocks_source"] = rec["indices_source"] = "skipped (NSE unreachable)"
            yahoo_days.append(d)
            status["days"].append(rec)
            continue
        ok = True
        try:
            df, src = nse.stocks(d)
            rec["stocks_source"], rec["stocks_rows"] = src, append_stocks(daily, d, df, src)
        except Exception as e:  # noqa: BLE001
            ok = False
            rec["stocks_source"] = "failed"
            status["errors"].append(f"{d} stocks: {e}"[:400])
            yahoo_days.append(d)
        try:
            rec["indices_rows"] = append_indices(daily, d, nse.indices(d))
            rec["indices_source"] = "nse-ind_close_all"
        except Exception as e:  # noqa: BLE001
            ok = False
            rec["indices_source"] = "failed"
            status["errors"].append(f"{d} indices: {e}"[:400])
            if d not in yahoo_days:
                yahoo_days.append(d)
        consecutive_fail = 0 if ok else consecutive_fail + 1
        status["days"].append(rec)

    if yahoo_days:
        try:
            n = yahoo_fill(daily, set(yahoo_days), s2i)
            status["yahoo_fallback"] = dict(days=[x.isoformat() for x in sorted(yahoo_days)], rows=n)
        except Exception as e:  # noqa: BLE001
            status["errors"].append(f"yahoo fallback: {e}"[:400])

    data_date = last_date(os.path.join(daily, "nifty 500.csv"))
    status["data_date"] = data_date.isoformat()
    status["fresh"] = data_date >= exp
    status["sectors_stale"] = any(r.get("indices_source") == "failed" for r in status["days"])
    return status


if __name__ == "__main__":
    import sys
    print(json.dumps(update(sys.argv[1]), indent=1))
