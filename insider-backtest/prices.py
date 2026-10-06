"""Stage 3: daily prices from Yahoo's chart endpoint, cached to cache/prices/ and never refetched.

Returns use Yahoo's adjusted close (splits and dividends, i.e. total return). Market cap needs the price
actually traded that day, so the unadjusted close is rebuilt from the split-adjusted close and the split list.
Companies are keyed on CIK; the ticker is only the key Yahoo needs.
"""
import datetime as dt, json, sys

import numpy as np
import pandas as pd

from common import CACHE, RESULTS, STATE, YAHOO_THROTTLE, YAHOO_UA, http_get, log

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d&events=div,split"
PDIR = CACHE / "prices"
PDIR.mkdir(exist_ok=True)
BENCH = "SPY"
STALE_DAYS = 10  # a series ending more than this before the data end is treated as delisted


def yahoo_symbol(t):
    return t.upper().replace(".", "-").replace("/", "-")


def parse_chart(js):
    """DataFrame indexed by date: close (split-adjusted), adjclose (total return), raw_close (as traded)."""
    res = ((js or {}).get("chart") or {}).get("result") or []
    if not res or not res[0].get("timestamp"):
        return None
    r = res[0]
    off = (r.get("meta") or {}).get("gmtoffset") or 0
    dates = pd.to_datetime([dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date() for t in r["timestamp"]])
    q = (r.get("indicators") or {}).get("quote") or [{}]
    close = np.array([np.nan if c is None else c for c in q[0].get("close") or []], dtype=float)
    adj = (r.get("indicators") or {}).get("adjclose") or [{}]
    adjc = np.array([np.nan if c is None else c for c in adj[0].get("adjclose") or close], dtype=float)
    df = pd.DataFrame({"close": close, "adjclose": adjc}, index=dates)
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["adjclose"])
    df = df[df.adjclose > 0]
    splits = ((r.get("events") or {}).get("splits") or {}).values()
    factor = np.ones(len(df))
    for s in splits:
        ratio = (s.get("numerator") or 1) / (s.get("denominator") or 1)
        sd = pd.Timestamp(dt.datetime.fromtimestamp(s["date"] + off, dt.timezone.utc).date())
        factor[df.index < sd] *= ratio
    df["raw_close"] = df.close * factor
    return df.sort_index()


def fetch(sym):
    """Raw Yahoo JSON for sym, from disk if already fetched."""
    path = PDIR / f"{sym}.json"
    if path.exists():
        return json.loads(path.read_text())
    url = URL.format(sym=sym, p1=0, p2=int(dt.datetime.now().timestamp()))
    try:
        js = http_get(url, YAHOO_THROTTLE, {"User-Agent": YAHOO_UA, "Accept": "application/json"}, tries=3).json()
    except Exception as e:  # 404 for dead tickers: cache the miss too, so it is never refetched
        js = {"chart": {"result": None, "error": {"description": str(e)[:200]}}}
    path.write_text(json.dumps(js))
    return js


def ticker_map(ciks):
    """CIK -> (ticker, source, problem). Current SEC ticker first, else the last symbol on its Form 4s.
    A historical symbol now owned by another CIK is a re-used ticker and is rejected (wrong company's prices)."""
    cur = pd.read_parquet(STATE / "tickers.parquet")
    cur_by_cik = cur.groupby("cik").ticker.first().to_dict()
    owner_of = cur.drop_duplicates("ticker").set_index("ticker").cik.to_dict()
    tr = pd.read_parquet(STATE / "trades.parquet", columns=["issuer_cik", "issuer_symbol", "filing_date"])
    tr = tr[tr.issuer_symbol.str.len().between(1, 6) & ~tr.issuer_symbol.isin(["NONE", "N/A", "NA"])]
    last_sym = tr.sort_values("filing_date").groupby("issuer_cik").issuer_symbol.last().to_dict()
    out = {}
    for c in ciks:
        if c in cur_by_cik:
            out[c] = (yahoo_symbol(cur_by_cik[c]), "sec_current", None)
        elif c in last_sym:
            s = last_sym[c]
            if owner_of.get(s.replace("-", ".")) not in (None, c) or owner_of.get(s) not in (None, c):
                out[c] = (yahoo_symbol(s), "form4_symbol", "ticker now used by another company")
            else:
                out[c] = (yahoo_symbol(s), "form4_symbol", None)
        else:
            out[c] = (None, None, "no ticker found")
    return out


class PriceBook:
    """Price lookups by CIK. Loads each cached series once."""
    def __init__(self, tmap, data_end=None):
        self.tmap = tmap
        self._mem = {}
        self.bench = self._series(BENCH)
        self.data_end = data_end or self.bench.index.max()

    def _series(self, sym):
        if sym not in self._mem:
            self._mem[sym] = parse_chart(fetch(sym))
        return self._mem[sym]

    def series(self, cik):
        t = self.tmap.get(cik)
        if not t or t[2] or not t[0]:
            return None
        return self._series(t[0])

    def raw_close_on(self, cik, date):
        s = self.series(cik)
        if s is None:
            return None
        s = s.loc[:date]
        if s.empty or (date - s.index[-1]).days > 7:
            return None
        return float(s.raw_close.iloc[-1])


def entry_point(s, filing_date):
    """Next trading day strictly after the filing date: (date, adjclose, close, raw_close) or None."""
    i = s.index.searchsorted(filing_date, side="right")
    if i >= len(s) or (s.index[i] - filing_date).days > 7:
        return None
    return s.index[i], s.adjclose.iloc[i], s.close.iloc[i], s.raw_close.iloc[i]


def horizon_return(s, entry_date, days, data_end, mode="last"):
    """Total return from entry_date's adjusted close to the last close on or before entry_date + days.
    None if the horizon is not complete yet. If the series stops early (delisted):
      mode 'exclude' -> None, 'last' -> return at the last known price, 'zero' -> -100%."""
    target = entry_date + pd.Timedelta(days=days)
    if target > data_end:
        return None, False
    p0 = s.adjclose.get(entry_date)
    if p0 is None or np.isnan(p0):
        return None, False
    w = s.adjclose.loc[:target]
    last = w.index[-1]
    dead = (target - last).days > STALE_DAYS
    if dead:
        if mode == "exclude":
            return None, True
        if mode == "zero":
            return -1.0, True
    return float(w.iloc[-1] / p0 - 1), dead


def drawdown_52w(s, entry_date):
    w = s.close.loc[entry_date - pd.Timedelta(days=365):entry_date]
    return float(1 - w.iloc[-1] / w.max()) if len(w) else np.nan


def fetch_all(ciks):
    tmap = ticker_map(ciks)
    pd.DataFrame([(c, *v) for c, v in tmap.items()], columns=["cik", "ticker", "source", "problem"]) \
        .to_parquet(STATE / "ticker_map.parquet")
    syms = sorted({v[0] for v in tmap.values() if v[0] and not v[2]} | {BENCH})
    todo = [s for s in syms if not (PDIR / f"{s}.json").exists()]
    log(f"prices: {len(syms)} tickers, {len(todo)} to fetch (~{len(todo) // 2 // 60} min)")
    for i, s in enumerate(todo):
        fetch(s)
        if i % 100 == 0:
            log(f"prices {i}/{len(todo)}")
    return tmap


def write_missing(tmap, names, extra=()):
    rows = []
    for c, (t, src, prob) in tmap.items():
        if prob:
            rows.append({"cik": c, "name": names.get(c), "ticker": t, "problem": prob})
        elif t and parse_chart(fetch(t)) is None:
            rows.append({"cik": c, "name": names.get(c), "ticker": t, "problem": "Yahoo has no prices"})
    rows.extend(extra)
    df = pd.DataFrame(rows, columns=["cik", "name", "ticker", "problem"])
    df.to_csv(RESULTS / "missing_prices.csv", index=False)
    return df


if __name__ == "__main__":
    u = pd.read_parquet(STATE / "universe_fin.parquet")
    fetch_all(sorted(u[u.pass_fin].cik.unique()))
    sys.exit(0)
