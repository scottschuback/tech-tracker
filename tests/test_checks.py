"""Tests for the number checks. Run: pytest -q"""
from tracker import checks, metrics

def pack(**k):
    base = {"unit": "USD", "revenue": 100.0, "gross_profit": 60.0, "rd": 10.0, "net_income": 20.0, "op_income": 25.0,
            "revenue_yoy": 0.1, "rd_yoy": 0.1, "net_income_yoy": 0.1, "op_income_yoy": 0.1,
            "_q": {"revenue": {}, "net_income": {}}}
    base.update(k); return base

def test_clean_pack_has_no_failures():
    assert not [r for r in checks.run_checks(pack()) if r["status"] != "pass"]

def test_gross_profit_above_revenue_fails():
    assert any(r["status"] == "fail" for r in checks.run_checks(pack(gross_profit=150.0)))

def test_negative_revenue_fails():
    assert any(r["metric"] == "revenue" and r["status"] == "fail" for r in checks.run_checks(pack(revenue=-5.0)))

def test_fast_yearly_growth_is_not_flagged():
    assert not [r for r in checks.run_checks(pack(revenue_yoy=1.06, net_income_yoy=7.35)) if r["status"] != "pass"]

def test_single_quarter_jump_is_flagged():
    res = checks.run_checks(pack(revenue_q=[{"end": "a", "rev": 50.0}, {"end": "b", "rev": 100.0}]))
    assert any(r["metric"] == "revenue" and r["status"] == "flag" for r in res)

def test_rd_far_ahead_of_sales_flagged():
    res = checks.run_checks(pack(rd_yoy=4.27, revenue_yoy=0.74, rd_intensity=0.3))
    assert any(r["metric"] == "rd" and r["status"] == "flag" for r in res)

def test_profit_lifted_by_non_operating_items():
    res = checks.run_checks(pack(net_income=60.0, op_income=25.0))
    assert any(r["metric"] == "net_income" and r["status"] == "flag" for r in res)

def test_derived_quarter_not_text_checked():
    res = checks.run_checks(pack(revenue_derived=True), text="nothing printed here")
    assert not [r for r in res if r["status"] != "pass"]

def test_unknown_unit_fails():
    assert any(r["metric"] == "unit" for r in checks.run_checks(pack(unit="shares")))

def test_restatement_detected():
    q = {"2026-03-31": [{"value": 100.0, "filed": "2026-05-01"}, {"value": 90.0, "filed": "2026-08-01"}]}
    assert checks.restatements(q)

def test_same_value_refiled_is_not_restatement():
    q = {"2026-03-31": [{"value": 100.0, "filed": "2026-05-01"}, {"value": 100.0, "filed": "2026-08-01"}]}
    assert not checks.restatements(q)

def test_in_text_finds_millions():
    assert checks.in_text(96_221_000_000, "Revenue $ 96,221 $ 81,615")
    assert not checks.in_text(96_221_000_000, "Revenue $ 95,000")

def test_in_text_does_not_match_inside_bigger_number():
    assert not checks.in_text(221_000_000, "Revenue 96,221")

def test_fast_growth_alone_is_not_flagged():
    # receivables grow with sales, days to pay unchanged: no flag
    assert checks.forensic({"dso_days": 60, "dso_days_ya": 58}) == []

def test_slower_paying_customers_flagged():
    f = checks.forensic({"dso_days": 80, "dso_days_ya": 55})
    assert f and f[0][0] == "amber"

def test_good_signs_are_green():
    f = checks.forensic({"gross_margin": 0.75, "gross_margin_ya": 0.70, "backlog_yoy": 0.4, "backlog": 50, "revenue": 100})
    assert all(c == "green" for c, _ in f) and len(f) == 2

def test_traits():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "form": "10-K", "filed": f"{y+1}-02-01", "accn": "x"}
            for y, v in [(2021, 100), (2022, 120), (2023, 150), (2024, 200), (2025, 260)]]}},
        "GrossProfit": {"units": {"USD": [
            {"start": "2025-01-01", "end": "2025-12-31", "val": 170, "form": "10-K", "filed": "2026-02-01", "accn": "x"}]}},
        "OperatingIncomeLoss": {"units": {"USD": [
            {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "form": "10-K", "filed": f"{y+1}-02-01", "accn": "x"}
            for y, v in [(2022, 30), (2023, 40), (2024, 60), (2025, 80)]]}},
        "Assets": {"units": {"USD": [{"end": f"{y}-12-31", "val": 300, "form": "10-K", "filed": f"{y+1}-02-01", "accn": "x"} for y in range(2021, 2026)]}},
        "LiabilitiesCurrent": {"units": {"USD": [{"end": f"{y}-12-31", "val": 100, "form": "10-K", "filed": f"{y+1}-02-01", "accn": "x"} for y in range(2021, 2026)]}},
    }}}
    t = metrics.annual_traits(facts)
    assert t["grew_every_year"] and t["gm_60"] and t["roc_15"] and t["traits"] == 3

def test_derived_fourth_quarter():
    rows = [
        {"end": "2025-12-31", "start": "2025-01-01", "days": 364, "value": 400, "filed": "2026-02-01"},
        {"end": "2025-03-31", "start": "2025-01-01", "days": 89, "value": 90, "filed": "2025-05-01"},
        {"end": "2025-06-30", "start": "2025-04-01", "days": 90, "value": 100, "filed": "2025-08-01"},
        {"end": "2025-09-30", "start": "2025-07-01", "days": 91, "value": 100, "filed": "2025-11-01"},
    ]
    q = metrics.quarterly(rows)
    assert q["2025-12-31"][-1]["value"] == 110 and q["2025-12-31"][-1]["derived"]

from tracker import verdict

def _pack(**k):
    p = {"unit": "USD", "revenue": 100e9, "revenue_yoy": 0.5, "forensic": [],
         "trajectory": [{"yoy": 0.4}, {"yoy": 0.45}, {"yoy": 0.5}, {"yoy": 0.5}],
         "annual": {"traits": 3, "grew_every_year": True, "gm_60": True, "roc_15": True}}
    p.update(k); return p

def test_verdict_strong():
    assert verdict.card("X", _pack(), "lead", [], {}, [])["verdict"] == "Strong"

def test_verdict_weak_when_losing_ground():
    assert verdict.card("X", _pack(), "lose", [], {}, [])["verdict"] == "Weak"

def test_slowing_sharply_no_longer_blocks_strong():
    p = _pack(trajectory=[{"yoy": 0.9}, {"yoy": 0.8}, {"yoy": 0.8}, {"yoy": 0.3}], revenue_yoy=0.3)
    c = verdict.card("X", p, "lead", [], {}, [])
    assert c["verdict"] == "Strong" and any("slowing sharply" in r for r in c["reasons"])

def test_shrinking_cyclical_is_cycle_low_not_weak():
    p = _pack(trajectory=[{"yoy": 0.1}, {"yoy": 0.0}, {"yoy": -0.05}, {"yoy": -0.1}], revenue_yoy=-0.1)
    assert verdict.card("X", p, "hold", ["Memory (HBM, DRAM)"], {}, [])["verdict"] == "Cycle low"

def test_verdict_weak_when_shrinking():
    p = _pack(trajectory=[{"yoy": 0.1}, {"yoy": 0.0}, {"yoy": -0.05}, {"yoy": -0.1}], revenue_yoy=-0.1)
    assert verdict.card("X", p, "hold", [], {}, [])["verdict"] == "Weak"

def test_lenders_not_flagged_for_investment_gains():
    assert checks.forensic({"investment_gain_share": 0.9, "financial": True}) == []
    assert checks.forensic({"investment_gain_share": 0.9})

import sqlite3, os
from tracker import store, movers

def _db(tmp_path):
    store.DB = str(tmp_path / "t.db")
    return store.connect()

def _card(v): return {"verdict": v, "reasons": ["r1", "r2"], "cyclical": False}

def test_mover_verdict_change_only_after_a_change(tmp_path):
    c = _db(tmp_path); packs = {"X": {"period_end": "2026-06-30", "revenue": 1e9}}
    ids = []
    assert movers.detect(c, "r1", {"X": _card("Watch")}, packs, {}, [], [], {}, ids) == []
    out = movers.detect(c, "r2", {"X": _card("Strong")}, packs, {}, [], [], {}, ids)
    assert out and out[0]["kind"] == "verdict change" and out[0]["colour"] == "green"
    assert movers.detect(c, "r3", {"X": _card("Strong")}, packs, {}, [], [], {}, ids) == []

def test_mover_guidance_raised(tmp_path):
    c = _db(tmp_path); packs = {"X": {"period_end": "2026-06-30", "revenue": 1e9}}
    g1 = {"X": {"revenue": 100e9, "period": "Q3", "tag": "F"}}; g2 = {"X": {"revenue": 108e9, "period": "Q3", "tag": "F"}}
    movers.detect(c, "r1", {"X": _card("Watch")}, packs, g1, [], [], {}, [])
    out = movers.detect(c, "r2", {"X": _card("Watch")}, packs, g2, [], [], {}, [])
    assert [m["kind"] for m in out] == ["guidance raised"]

def test_mover_deal_ignores_own_name(tmp_path):
    c = _db(tmp_path); packs = {"AMD": {"name": "ADVANCED MICRO DEVICES INC", "period_end": "x", "revenue": 1}}
    item = {"id": "i1", "source": "sec_fulltext", "ticker": "AMD", "title": "AMD: warrant", "reason": "Advanced Micro Devices", "kind": "", "url": ""}
    assert movers.detect(c, "r1", {"AMD": _card("Watch")}, packs, {}, [], [item], {}, ["i1"]) == []
    item2 = dict(item, id="i2", reason="agreement with OpenAI")
    out = movers.detect(c, "r2", {"AMD": _card("Watch")}, packs, {}, [], [item2], {}, ["i2"])
    assert out and out[0]["kind"] == "supply deal"

def test_mover_price_down_while_strong(tmp_path):
    c = _db(tmp_path); packs = {"X": {"period_end": "x", "revenue": 1}}
    px = {"X": [(f"2026-01-{i:02d}", 100.0) for i in range(1, 29)] + [(f"2026-02-{i:02d}", 100.0) for i in range(1, 29)] + [("2026-03-01", 75.0)] * 10}
    out = movers.detect(c, "r1", {"X": _card("Strong")}, packs, {}, [], [], px, [])
    assert [m["kind"] for m in out] == ["price down, filings strong"]

def test_mover_turn_needs_cyclical_and_above_year_ago(tmp_path):
    c = _db(tmp_path)
    today = __import__("datetime").date.today().isoformat()
    pk = {"period_end": "x", "revenue": 1, "revenue_src": {"filed": today},
          "revenue_q": [{"rev": 100}, {"rev": 120}, {"rev": 110}, {"rev": 95}, {"rev": 125}]}
    card = dict(_card("Cycle low"), cyclical=True)
    out = movers.detect(c, "r1", {"X": card}, {"X": pk}, {}, [], [], {}, [])
    assert [m["kind"] for m in out] == ["the turn"]
    pk2 = dict(pk, revenue_q=[{"rev": 100}, {"rev": 120}, {"rev": 110}, {"rev": 95}, {"rev": 98}])  # rise but below a year ago
    assert movers.detect(c, "r2", {"Y": card}, {"Y": pk2}, {}, [], [], {}, []) == []
