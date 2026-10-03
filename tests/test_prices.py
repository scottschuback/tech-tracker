"""Tests for the Yahoo chart parser. Network is never touched here."""
import datetime as dt
import pytest
from tracker.sources import prices

def chart(ts, closes, off=-14400):
    return {"chart": {"result": [{"meta": {"gmtoffset": off}, "timestamp": ts,
                                  "indicators": {"quote": [{"close": closes}]}}], "error": None}}

def test_parse_dates_in_exchange_time_and_skips_gaps():
    # 2026-10-02 20:00 UTC = 16:00 New York; a None close (half day, halt) is skipped
    t = int(dt.datetime(2026, 10, 2, 20, 0, tzinfo=dt.timezone.utc).timestamp())
    out = prices.parse(chart([t - 86400, t, t + 86400 * 3], [100.0, None, 101.5]), "ALAB", today=dt.date(2026, 10, 6))
    assert out == [("2026-10-01", 100.0), ("2026-10-05", 101.5)]

def test_parse_drops_old_rows():
    t = int(dt.datetime(2024, 1, 2, 20, 0, tzinfo=dt.timezone.utc).timestamp())
    with pytest.raises(RuntimeError):
        prices.parse(chart([t], [10.0]), "X", days=30, today=dt.date(2026, 10, 3))

def test_parse_reports_yahoo_error():
    with pytest.raises(RuntimeError, match="No data found"):
        prices.parse({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}, "ZZZZ")

def test_share_class_symbol():
    assert prices.symbol("brk.b") == "BRK-B" and prices.symbol("ALAB") == "ALAB"

def test_prices_never_enter_the_verdict():
    # The verdict module must not import the price source or read the prices table
    import ast, inspect
    from tracker import verdict
    src = inspect.getsource(verdict)
    names = {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert "prices" not in names and "FROM prices" not in src.upper().replace("  ", " ")
