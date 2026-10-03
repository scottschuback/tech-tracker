"""Tests for true earnings: every one-off is stripped once, from the company's own tags, with its source kept."""
from tracker import metrics

END, START = "2026-06-30", "2026-04-01"

def fact(val, start=START, end=END, form="10-Q", accn="0000000000-26-000001"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": "2026-08-01", "accn": accn}

def facts(**tags):
    """A minimal companyfacts document: one quarter per tag, plus whatever is passed."""
    base = {"NetIncomeLoss": 100.0, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 125.0,
            "IncomeTaxExpenseBenefit": 25.0, "OperatingIncomeLoss": 80.0,
            "RevenueFromContractWithCustomerExcludingAssessedTax": 1000.0}
    base.update(tags)
    return {"facts": {"us-gaap": {t: {"units": {"USD": v if isinstance(v, list) else [fact(v)]}} for t, v in base.items()}}}

def build(**tags):
    return metrics.build(facts(**tags))

def kinds(p):
    return {x["kind"]: x for x in p["one_offs"]}

def test_no_one_offs_true_earnings_equal_net_income():
    p = build()
    assert p["one_offs"] == [] and p["true_earnings"] == 100.0

def test_investment_gain_stripped_after_tax():
    p = build(GainLossOnInvestments=40.0)  # rate = 25 / 125 = 20%
    assert abs(p["true_earnings"] - (100.0 - 40.0 * 0.8)) < 1e-9
    assert kinds(p)["investments"]["src"][0]["tag"] == "GainLossOnInvestments"

def test_investment_tags_not_double_counted():
    # Arm reports both tags for the same gain: only the first preference is used
    p = build(GainLossOnInvestments=40.0, EquitySecuritiesFvNiGainLoss=39.0)
    assert len([x for x in p["one_offs"] if x["kind"] == "investments"]) == 1
    assert p["equity_gains"] == 40.0

def test_private_revaluation_added_when_separate():
    # Amazon: listed-stock gains small, private-stake revaluation separate; both fit inside non-operating income (45)
    p = build(EquitySecuritiesFvNiGainLoss=5.0,
              EquitySecuritiesWithoutReadilyDeterminableFairValueUpwardPriceAdjustmentAnnualAmount=38.0)
    assert set(kinds(p)) == {"investments", "private_revaluation"}
    assert p["equity_gains"] == 43.0

def test_private_revaluation_not_double_counted_when_included():
    # Alphabet: the listed-stock gain already includes the private revaluation; together they exceed non-operating income
    p = build(EquitySecuritiesFvNiGainLoss=44.0,
              EquitySecuritiesWithoutReadilyDeterminableFairValueUpwardPriceAdjustmentAnnualAmount=30.0)
    assert set(kinds(p)) == {"investments"} and p["equity_gains"] == 44.0

def test_downward_revaluation_netted():
    p = build(EquitySecuritiesWithoutReadilyDeterminableFairValueUpwardPriceAdjustmentAnnualAmount=30.0,
              EquitySecuritiesWithoutReadilyDeterminableFairValueDownwardPriceAdjustmentAnnualAmount=10.0)
    x = kinds(p)["private_revaluation"]
    assert x["amount"] == 20.0 and len(x["src"]) == 2

def test_business_sale_gain_stripped():
    # Synopsys: gain on selling the Processor IP business
    p = build(DisposalGroupNotDiscontinuedOperationGainLossOnDisposal=50.0)
    assert abs(p["true_earnings"] - (100.0 - 50.0 * 0.8)) < 1e-9
    assert kinds(p)["business_sale"]["label"] == "Gain on sale of a business"

def test_warrant_loss_added_back():
    # IonQ: a positive FairValueAdjustmentOfWarrants is a charge, so true earnings are HIGHER than reported
    p = build(NetIncomeLoss=-500.0, IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=-500.0,
              IncomeTaxExpenseBenefit=0.0, FairValueAdjustmentOfWarrants=400.0)
    x = kinds(p)["warrants"]
    assert x["amount"] == -400.0
    assert p["true_earnings"] == -100.0

def test_warrant_gain_stripped():
    p = build(FairValueAdjustmentOfWarrants=-30.0)
    assert kinds(p)["warrants"]["amount"] == 30.0 and p["true_earnings"] < 100.0

def test_derivative_revaluation_stripped():
    p = build(UnrealizedGainLossOnDerivatives=10.0)
    assert kinds(p)["derivatives"]["amount"] == 10.0

def test_tagged_tax_benefit_stripped_in_full():
    p = build(EffectiveIncomeTaxRateReconciliationShareBasedCompensationExcessTaxBenefitAmount=-15.0)
    x = kinds(p)["tax"]
    assert x["amount"] == 15.0 and x["after_tax"] == 15.0 and x["pretax"] is False
    assert p["true_earnings"] == 85.0

def test_untagged_tax_credit_on_profit_stripped():
    # Astera Labs: tax was a credit on a profit and no tag gives the cause
    p = build(NetIncomeLoss=153.1, IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=102.8,
              IncomeTaxExpenseBenefit=-50.3, OperatingIncomeLoss=89.2)
    x = kinds(p)["tax"]
    assert abs(x["amount"] - 50.3) < 1e-9 and "no tag" in x["label"]
    assert abs(p["true_earnings"] - 102.8) < 1e-9

def test_tax_credit_on_a_loss_is_not_stripped():
    p = build(NetIncomeLoss=-80.0, IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=-100.0,
              IncomeTaxExpenseBenefit=-20.0)
    assert "tax" not in kinds(p)

def test_stripped_tax_credit_does_not_flatter_rate_on_gains():
    # With the credit removed the tax rate is 0, so a gain comes off in full rather than at a negative rate
    p = build(NetIncomeLoss=150.0, IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=100.0,
              IncomeTaxExpenseBenefit=-50.0, GainLossOnInvestments=20.0)
    assert kinds(p)["investments"]["after_tax"] == 20.0
    assert p["true_earnings"] == 150.0 - 20.0 - 50.0

def test_every_strip_carries_its_source():
    p = build(GainLossOnInvestments=10.0, GainLossOnSaleOfBusiness=5.0, FairValueAdjustmentOfWarrants=-3.0)
    for x in p["one_offs"]:
        s = x["src"][0]
        assert s["tag"] and s["accn"] and s["form"] == "10-Q" and s["filed"]

def test_fourth_quarter_strip_derived_from_annual():
    # Sandisk/WDC: Q4 one-offs exist only in the annual report; derived as full year minus three quarters
    q = [("2025-07-01", "2025-09-30", 1.0), ("2025-10-01", "2025-12-31", 2.0), ("2026-01-01", "2026-03-31", 3.0)]
    rows = [fact(v, s, e) for s, e, v in q] + [fact(10.0, "2025-07-01", "2026-06-30", form="10-K")]
    rev = [fact(100.0, s, e) for s, e, _ in q] + [fact(400.0, "2025-07-01", "2026-06-30", form="10-K")]
    f = facts(EquitySecuritiesFvNiGainLoss=rows, RevenueFromContractWithCustomerExcludingAssessedTax=rev,
              NetIncomeLoss=[fact(v, s, e) for s, e, v in [(*x[:2], 20.0) for x in q]] + [fact(100.0, "2025-07-01", "2026-06-30", form="10-K")])
    r = metrics._tag_quarter(f, "EquitySecuritiesFvNiGainLoss", "2026-06-30")
    assert r["value"] == 4.0 and r["derived"]

def test_unrealized_listed_gain_tag_used_when_only_one():
    # Sandisk tags only the unrealised part of its listed-stock gain
    p = build(EquitySecuritiesFvNiUnrealizedGainLoss=30.0)
    assert kinds(p)["investments"]["src"][0]["tag"] == "EquitySecuritiesFvNiUnrealizedGainLoss"

def test_financial_firm_keeps_investment_and_derivative_results():
    # For an insurer or asset manager these are the business; a business sale is still stripped
    p = metrics.build(facts(UnrealizedGainLossOnDerivatives=30.0, GainLossOnInvestments=20.0, GainLossOnSaleOfBusiness=10.0),
                      financial=True)
    assert set(kinds(p)) == {"business_sale"}

def test_item_only_in_annual_report_uses_full_year_and_says_so():
    # Sandisk: the stock gain is tagged only in the 10-K; the three 10-Qs never reported it
    f = facts(EquitySecuritiesFvNiUnrealizedGainLoss=[fact(808.0, "2025-06-28", "2026-07-03", form="10-K")])
    r = metrics._tag_quarter(f, "EquitySecuritiesFvNiUnrealizedGainLoss", "2026-07-03")
    assert r["value"] == 808.0 and r["derived"] == "annual_only"

def test_annual_figure_not_used_when_a_quarter_reported_the_item():
    # One 10-Q reported it but the others did not: no safe split, so nothing is guessed
    f = facts(GainLossOnInvestments=[fact(5.0, "2025-07-01", "2025-09-30"), fact(10.0, "2025-07-01", "2026-06-30", form="10-K")])
    assert metrics._tag_quarter(f, "GainLossOnInvestments", "2026-06-30") is None

def test_yearly_tax_reconciliation_not_dumped_into_one_quarter():
    f = facts(IncomeTaxReconciliationChangeInDeferredTaxAssetsValuationAllowance=[fact(-40.0, "2025-07-01", "2026-06-30", form="10-K")])
    p = metrics.build(f)
    assert not [x for x in p["one_offs"] if x["kind"] == "tax"]
