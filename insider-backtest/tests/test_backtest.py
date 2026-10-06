import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import events  # noqa: E402
import prices  # noqa: E402
import universe as uni  # noqa: E402

T = pd.Timestamp


def series(start="2020-01-01", days=800, splits=None):
    """Business-day price frame: adjclose = close = raw_close unless a split is given."""
    idx = pd.bdate_range(start, periods=days)
    px = np.linspace(100, 200, days)
    df = pd.DataFrame({"close": px, "adjclose": px, "raw_close": px}, index=idx)
    return df


# ---------- 1. entry uses the filing date, never the transaction date ----------

class FakeUniverse:
    def row(self, cik, date):
        return ("accn", 1e9)

    def contains(self, cik, date):
        return True


class FakeBook:
    def __init__(self, s):
        self.s = s

    def series(self, cik):
        return self.s


def one_filing(acc, cik, filed, traded, owner, role="Director", shares=1000, price=10.0, pre=9000):
    return {"accession": acc, "issuer_cik": cik, "filing_date": T(filed), "trans_date": T(traded),
            "shares": shares, "dollars": shares * price, "pre_holding": pre, "kind": "insider",
            "insiders": (owner,), "roles": (role,)}


def test_entry_is_next_trading_day_after_filing_not_transaction():
    s = series()
    f = pd.DataFrame([one_filing("a1", 1, "2021-03-10", "2021-02-01", 100)])  # traded 5 weeks before filing
    ev = events.build_events(events.dedupe(f), {}, FakeUniverse(), FakeBook(s))
    assert ev.entry_date.iloc[0] == T("2021-03-11")          # day after the FILING date
    assert ev.entry_adj.iloc[0] == s.adjclose[T("2021-03-11")]
    assert ev.entry_date.iloc[0] > ev.first_trans_date.iloc[0] + pd.Timedelta(days=30)


def test_filing_on_friday_enters_monday():
    s = series()
    d, _, _, _ = prices.entry_point(s, T("2021-03-12"))       # a Friday
    assert d == T("2021-03-15")


# ---------- 2. the universe only sees 10-Ks filed before the date ----------

def facts_for(cik, accn, filed, fy_end, ni, op, ocf=200.0, capex=50.0, debt=100.0, eq=400.0, cash=50.0, shares=1e8):
    start = (T(fy_end) - pd.Timedelta(days=364)).strftime("%Y-%m-%d")
    prior = (T(fy_end) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    rows = [("NetIncomeLoss", start, fy_end, ni), ("OperatingIncomeLoss", start, fy_end, op),
            ("NetCashProvidedByUsedInOperatingActivities", start, fy_end, ocf),
            ("PaymentsToAcquirePropertyPlantAndEquipment", start, fy_end, capex),
            ("LongTermDebt", None, fy_end, debt), ("StockholdersEquity", None, fy_end, eq),
            ("CashAndCashEquivalentsAtCarryingValue", None, fy_end, cash),
            ("LongTermDebt", None, prior, debt), ("StockholdersEquity", None, prior, eq),
            ("CashAndCashEquivalentsAtCarryingValue", None, prior, cash),
            ("EntityCommonStockSharesOutstanding", None, fy_end, shares)]
    return [(cik, c, s and T(s), T(e), float(v), accn, T(filed), "10-K") for c, s, e, v in rows]


def build_u(rows):
    facts = pd.DataFrame(rows, columns=["cik", "concept", "start", "end", "val", "accn", "filed", "form"])
    comp = pd.DataFrame({"cik": [1], "sic": [3571], "name": ["Test Co"]})
    fin = uni.build_financials(facts, comp)
    return uni.Universe(uni.apply_market_cap(fin, lambda cik, d: 100.0))  # cap = 1e8 x 100 = $10bn


def test_universe_uses_only_10ks_filed_before_the_date():
    # FY2015 fails (loss), filed 2016-02-20; FY2016 passes, filed 2017-02-20; FY2017 fails, filed 2018-02-20
    rows = (facts_for(1, "k15", "2016-02-20", "2015-12-31", ni=-10, op=100)
            + facts_for(1, "k16", "2017-02-20", "2016-12-31", ni=80, op=100)
            + facts_for(1, "k17", "2018-02-20", "2017-12-31", ni=-5, op=100))
    u = build_u(rows)
    assert not u.contains(1, T("2017-02-19"))   # FY2016 10-K not yet filed: still under the failing FY2015 one
    assert u.contains(1, T("2017-02-20"))       # filed today -> visible
    assert u.contains(1, T("2018-02-19"))
    assert not u.contains(1, T("2018-02-20"))   # next 10-K (a loss) replaces it on its filed date


def test_universe_return_on_capital_rule():
    # NOPAT = 100 x 0.79 = 79; capital = 100 + 400 - 50 = 450 -> 17.6% passes; op 80 -> 14.0% fails
    good = build_u(facts_for(1, "a", "2017-02-20", "2016-12-31", ni=50, op=100))
    bad = build_u(facts_for(1, "b", "2017-02-20", "2016-12-31", ni=50, op=80))
    assert good.contains(1, T("2017-06-01"))
    assert not bad.contains(1, T("2017-06-01"))


def test_universe_membership_lapses_without_a_next_10k():
    u = build_u(facts_for(1, "a", "2017-02-20", "2016-12-31", ni=50, op=100))
    assert u.contains(1, T("2018-03-01"))
    assert not u.contains(1, T("2018-03-30"))   # 400 days after filing


# ---------- 3. 90-day dedupe and 30-day cluster folding ----------

def test_dedupe_and_cluster_folding():
    f = pd.DataFrame([
        one_filing("a", 1, "2020-01-01", "2019-12-30", 11),
        one_filing("b", 1, "2020-01-20", "2020-01-18", 12),   # day 19: folds in
        one_filing("c", 1, "2020-01-31", "2020-01-29", 13),   # day 30: folds in -> 3 insiders
        one_filing("d", 1, "2020-02-15", "2020-02-14", 14),   # day 45: dropped (31-89 days)
        one_filing("e", 1, "2020-03-31", "2020-03-30", 15),   # day 90: new event
        one_filing("f", 2, "2020-01-15", "2020-01-14", 21),   # another company: its own event
    ]).sort_values(["issuer_cik", "filing_date"]).reset_index(drop=True)
    d = events.dedupe(f).set_index("accession").event_id
    assert d["a"] == d["b"] == d["c"]
    assert d["d"] == -1
    assert d["e"] not in (d["a"], -1)
    assert d["f"] not in (d["a"], d["e"], -1)

    ev = events.build_events(events.dedupe(f), {}, FakeUniverse(), FakeBook(series()))
    first = ev[ev.filing_date == T("2020-01-01")].iloc[0]
    assert first.n_insiders == 3 and first.cluster == "Cluster"
    assert first.dollars == 30_000
    assert first.entry_date == T("2020-01-02")              # entry stays at the FIRST filing
    assert first.fd_3 == T("2020-01-31")
    assert ev[ev.filing_date == T("2020-03-31")].iloc[0].cluster == "Single"


def test_same_insider_twice_counts_once():
    f = pd.DataFrame([one_filing("a", 1, "2020-01-01", "2020-01-01", 11),
                      one_filing("b", 1, "2020-01-10", "2020-01-09", 11)])
    ev = events.build_events(events.dedupe(f), {}, FakeUniverse(), FakeBook(series()))
    assert len(ev) == 1 and ev.n_insiders.iloc[0] == 1 and ev.cluster.iloc[0] == "Single"


# ---------- 4. routine vs opportunistic ----------

def hist(rows):
    return pd.DataFrame([(T(d), c) for d, c in rows], columns=["trans_date", "trans_code"])


def test_routine_same_month_three_prior_years():
    h = hist([("2017-05-10", "P"), ("2018-05-03", "P"), ("2019-05-20", "P")])
    assert events.routine_label(h, T("2020-05-12")) == "Routine"


def test_opportunistic_has_history_but_breaks_pattern():
    h = hist([("2017-05-10", "P"), ("2018-11-03", "S"), ("2019-05-20", "P")])
    assert events.routine_label(h, T("2020-05-12")) == "Opportunistic"


def test_unclassified_without_three_years():
    h = hist([("2018-05-03", "P"), ("2019-05-20", "P")])
    assert events.routine_label(h, T("2020-05-12")) == "Unclassified"


def test_history_in_current_year_does_not_count():
    h = hist([("2017-05-10", "P"), ("2018-05-03", "P"), ("2020-01-20", "P")])
    assert events.routine_label(h, T("2020-05-12")) == "Unclassified"


def test_event_routine_label():
    assert events.event_routine(["Routine", "Opportunistic"]) == "Opportunistic"
    assert events.event_routine(["Routine", "Unclassified"]) == "Routine"
    assert events.event_routine(["Unclassified"]) == "Unclassified"


# ---------- 5. adjusted-close returns across a split ----------

def chart_json():
    """Yahoo-style chart: 4:1 split on 2020-01-06. Yahoo's close is already split-adjusted."""
    days = pd.bdate_range("2020-01-01", periods=6)            # Jan 1,2,3,6,7,8
    ts = [int(dt.datetime(d.year, d.month, d.day, 14, 30, tzinfo=dt.timezone.utc).timestamp()) for d in days]
    traded = [400, 404, 408, 103, 104, 105]                    # as traded: 4:1 split before Jan 6
    close = [400 / 4, 404 / 4, 408 / 4, 103, 104, 105]         # Yahoo's split-adjusted close
    adj = [c * 0.99 if i < 4 else c for i, c in enumerate(close)]  # a 1% dividend went ex on Jan 7
    split_ts = ts[3]
    return {"chart": {"result": [{
        "meta": {"gmtoffset": -18000}, "timestamp": ts,
        "indicators": {"quote": [{"close": close}], "adjclose": [{"adjclose": adj}]},
        "events": {"splits": {str(split_ts): {"date": split_ts, "numerator": 4, "denominator": 1}}},
    }]}}, traded


def test_split_raw_close_and_total_return():
    js, traded = chart_json()
    s = prices.parse_chart(js)
    assert np.allclose(s.raw_close.to_numpy(), traded)        # unadjusted price restored for market cap
    # total return Jan 2 -> Jan 8 uses adjusted close: (105 / (101 * 0.99)) - 1, NOT 105/404 - 1
    r, dead = prices.horizon_return(s, T("2020-01-02"), 6, data_end=T("2020-01-08"))
    assert r == pytest.approx(105 / (101 * 0.99) - 1)
    assert r > 0 and not dead
    assert 105 / 404 - 1 < -0.7                               # the naive raw-price return would be a fake crash


def test_horizon_incomplete_and_delisted_modes():
    s = series(days=300)                                      # stops ~Feb 2021
    end = T("2026-01-01")
    assert prices.horizon_return(s, s.index[0], 365 * 10, end)[0] is None   # horizon not finished yet
    r_last, dead = prices.horizon_return(s, s.index[0], 600, end, "last")
    assert dead and r_last == pytest.approx(s.adjclose.iloc[-1] / s.adjclose.iloc[0] - 1)
    assert prices.horizon_return(s, s.index[0], 600, end, "zero")[0] == -1.0
    assert prices.horizon_return(s, s.index[0], 600, end, "exclude")[0] is None


# ---------- odds and ends ----------

def test_role_and_entity_rules():
    assert events.role_of(True, False, "EVP and CFO") == "Top exec"
    assert events.role_of(True, False, "Senior Vice President, Sales") == "Officer"
    assert events.role_of(False, True, "Chairman of the Board") == "Top exec"
    assert events.role_of(False, True, "") == "Director"
    assert events.role_of(True, False, "President & CEO") == "Top exec"
    assert not events.is_person("ValueAct Holdings, L.P.")
    assert not events.is_person("Smith Family Trust")
    assert events.is_person("SMITH JOHN A")


def test_debt_not_double_counted():
    assert uni.total_debt({"LongTermDebt": 100, "ShortTermBorrowings": 20, "CommercialPaper": 20}) == 120
    assert uni.total_debt({"LongTermDebtNoncurrent": 80, "DebtCurrent": 30, "ShortTermBorrowings": 10}) == 110
    assert uni.total_debt({}) == 0
