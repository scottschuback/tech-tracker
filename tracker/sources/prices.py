"""Daily share prices from Stooq (free, no key). Used ONLY for 'price down while filings strong' alerts and
for backtests on real returns. Prices never enter the verdict. UNTESTED in the build sandbox: first GitHub run proves it."""
import csv, io, datetime as dt
from ..http import get

def history(ticker, days=400):
    sym = ticker.lower() + ".us"
    txt = get(f"https://stooq.com/q/d/l/?s={sym}&i=d", as_json=False)
    rows = list(csv.DictReader(io.StringIO(txt)))
    if not rows or "Close" not in rows[0]:
        raise RuntimeError(f"no price data for {ticker}")
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return [(r["Date"], float(r["Close"])) for r in rows if r.get("Date", "") >= cutoff and r.get("Close") not in ("", None)]
