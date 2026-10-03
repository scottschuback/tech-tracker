"""SQLite store. Everything the tracker sees is kept, so nothing is reported twice and every alert can be scored later."""
import sqlite3, json, os, datetime as dt

DB = os.environ.get("TRACKER_DB", os.path.join(os.path.dirname(__file__), "..", "data", "tracker.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY, source TEXT, ticker TEXT, kind TEXT, title TEXT, url TEXT,
  published TEXT, first_seen TEXT, colour TEXT, tag TEXT, reason TEXT, extra TEXT);
CREATE TABLE IF NOT EXISTS numbers(
  ticker TEXT, metric TEXT, period_end TEXT, period_days INT, value REAL, unit TEXT,
  form TEXT, filed TEXT, accn TEXT, check_status TEXT, check_note TEXT,
  PRIMARY KEY(ticker, metric, period_end, period_days));
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, started TEXT, finished TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS source_status(
  run_id TEXT, source TEXT, status TEXT, new_items INT, note TEXT,
  PRIMARY KEY(run_id, source));
CREATE TABLE IF NOT EXISTS snapshots(
  run_id TEXT, ticker TEXT, verdict TEXT, guidance_rev REAL, guidance_period TEXT, backlog REAL,
  period_end TEXT, revenue REAL, PRIMARY KEY(run_id, ticker));
CREATE TABLE IF NOT EXISTS insider_buys(
  accn TEXT PRIMARY KEY, ticker TEXT, owner TEXT, filed TEXT, shares REAL, price REAL, value REAL);
CREATE TABLE IF NOT EXISTS promise_state(key TEXT PRIMARY KEY, state TEXT);
CREATE TABLE IF NOT EXISTS prices(
  ticker TEXT, date TEXT, close REAL, PRIMARY KEY(ticker, date));
CREATE TABLE IF NOT EXISTS scoreboard(
  item_id TEXT PRIMARY KEY, ticker TEXT, rule TEXT, colour TEXT, alert_date TEXT,
  base_rev REAL, rev_3m REAL, rev_6m REAL, rev_12m REAL, evaluated TEXT);
"""

def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c

def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def add_item(c, **k):
    """Insert an item if new. Returns True when it was not seen before."""
    k.setdefault("extra", {})
    k["extra"] = json.dumps(k["extra"])
    k.setdefault("first_seen", now())
    cur = c.execute(
        "INSERT OR IGNORE INTO items(id,source,ticker,kind,title,url,published,first_seen,colour,tag,reason,extra) "
        "VALUES(:id,:source,:ticker,:kind,:title,:url,:published,:first_seen,:colour,:tag,:reason,:extra)", k)
    return cur.rowcount == 1
