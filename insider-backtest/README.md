# Insider buying backtest

Does an open-market insider purchase predict better 1-, 3- and 5-year returns inside Scott's universe of
profitable, cash-generating, high-return US companies, compared with equally good companies with no insider buying?

This code lives on its own branch (`insider-backtest`). It is not wired into the tech tracker.

```
pip install -r requirements.txt
python -m pytest -q tests
python run.py          # all stages; resumes from state/ if stopped
```

| Stage | File | Output |
|---|---|---|
| SEC Form 3/4/5 bulk files, XBRL companyfacts, submissions (SIC) | `fetch_sec.py` | `cache/`, `state/trades, owners, facts, companies` |
| Point-in-time universe (rules 1-3, SIC, then $2bn cap) | `universe.py` | `state/universe.parquet` |
| Yahoo prices, cached and never refetched | `prices.py` | `cache/prices/` |
| Insider-buy events and tags | `events.py` | `state/events.parquet` |
| Returns, control, statistics, robustness | `backtest.py` | `state/events_returns.parquet` |
| Report | `report.py` | `results/` |

SEC requests send `User-Agent: Scott Research <email>` (set `SEC_UA` to override) at no more than 8 a second.
Yahoo requests are capped at 2 a second.
The companyfacts and submissions data comes from the SEC's nightly bulk zips: the same JSON as the per-CIK API,
fetched in one request each instead of thousands.
