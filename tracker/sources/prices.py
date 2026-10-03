"""Daily share prices from Yahoo Finance's public chart endpoint (free, no key). Used ONLY for 'price down while
filings strong' alerts and for backtests on real returns. Prices never enter the verdict.
UNTESTED on GitHub: Stooq was replaced on 2026-10-03 after it began serving a browser check instead of CSV;
the first GitHub run proves this source."""
import datetime as dt
from ..http import get, UA

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
HEADERS = {"User-Agent": f"Mozilla/5.0 (compatible; {UA})", "Accept": "application/json"}

def symbol(ticker):
    """Yahoo writes share classes with a dash: BRK.B -> BRK-B."""
    return ticker.upper().replace(".", "-")

def parse(js, ticker, days=400, today=None):
    """[(YYYY-MM-DD, close)] from a chart response, oldest first. Days with no close are skipped."""
    res = ((js or {}).get("chart") or {}).get("result") or []
    if not res:
        err = ((js or {}).get("chart") or {}).get("error") or {}
        raise RuntimeError(f"no price data for {ticker}: {err.get('description') or 'empty response'}")
    r = res[0]
    ts = r.get("timestamp") or []
    closes = (((r.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    off = (r.get("meta") or {}).get("gmtoffset") or 0
    cutoff = ((today or dt.date.today()) - dt.timedelta(days=days)).isoformat()
    out = {}
    for t, c in zip(ts, closes):
        if c is None: continue
        d = dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()
        if d >= cutoff: out[d] = float(c)
    if not out:
        raise RuntimeError(f"no price data for {ticker}")
    return sorted(out.items())

def history(ticker, days=400):
    rng = "2y" if days > 365 else "1y"
    js = get(URL.format(sym=symbol(ticker)), params={"range": rng, "interval": "1d"}, headers=HEADERS)
    return parse(js, ticker, days)
