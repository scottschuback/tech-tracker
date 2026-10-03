"""Alert scoreboard: did each kind of alert actually lead to anything in the business?
Measured on the company's own reported revenue 3, 6 and 12 months after the alert's filing date.
Share prices are not used (no free, reliable daily price source); this tests business outcomes only."""
import datetime as dt
from . import metrics

def _rev_quarters(facts):
    q = metrics.quarterly(metrics.series(facts, "revenue"))
    return [(k, v[-1]["value"]) for k, v in q.items()]

def _at(qs, date):
    """Latest quarter whose period ended on or before `date`."""
    best = None
    for end, val in qs:
        if end <= date: best = (end, val)
    return best

def record(c, item_id, ticker, rule, colour, alert_date):
    c.execute("INSERT OR IGNORE INTO scoreboard(item_id,ticker,rule,colour,alert_date) VALUES(?,?,?,?,?)",
              (item_id, ticker, rule, colour, alert_date))

def evaluate(c, facts_by_ticker):
    today = dt.date.today()
    rows = c.execute("SELECT * FROM scoreboard WHERE evaluated IS NULL OR rev_12m IS NULL").fetchall()
    for r in rows:
        facts = facts_by_ticker.get(r["ticker"])
        if not facts: continue
        qs = _rev_quarters(facts)
        a = dt.date.fromisoformat(r["alert_date"])
        base = _at(qs, a.isoformat())
        if not base or not base[1]: continue
        vals = {}
        for col, days in (("rev_3m", 120), ("rev_6m", 210), ("rev_12m", 390)):
            when = a + dt.timedelta(days=days)
            if when > today: vals[col] = None; continue
            later = _at(qs, when.isoformat())
            vals[col] = (later[1] / base[1] - 1) if later and later[0] > base[0] else None
        c.execute("UPDATE scoreboard SET base_rev=?, rev_3m=?, rev_6m=?, rev_12m=?, evaluated=? WHERE item_id=?",
                  (base[1], vals["rev_3m"], vals["rev_6m"], vals["rev_12m"], today.isoformat(), r["item_id"]))

def summary(c):
    out = []
    for r in c.execute("""SELECT rule, colour, COUNT(*) n,
            AVG(rev_3m) a3, AVG(rev_6m) a6, AVG(rev_12m) a12,
            SUM(CASE WHEN rev_12m < 0 THEN 1 ELSE 0 END) neg12, SUM(CASE WHEN rev_12m IS NOT NULL THEN 1 ELSE 0 END) n12
            FROM scoreboard GROUP BY rule, colour ORDER BY n DESC""").fetchall():
        out.append(dict(r))
    base = c.execute("SELECT AVG(rev_12m) a12, COUNT(rev_12m) n FROM scoreboard").fetchone()
    return {"rules": out, "baseline_12m": base["a12"], "baseline_n": base["n"]}

def backfill(c, filings_by_ticker, classify):
    """Seed the scoreboard from past 8-Ks so it is useful from day one. Uses the filing date, never the period date."""
    n = 0
    for t, fl in filings_by_ticker.items():
        for f in fl:
            if not f["form"].startswith("8-K"): continue
            for it in f["items"]:
                colour = classify(it)
                if colour not in ("red", "amber"): continue
                record(c, f"bf:{f['accn']}:{it}", t, "8-K " + it, colour, f["filed"]); n += 1
    return n
