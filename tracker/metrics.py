"""Numbers built by code from each company's own XBRL filings. Claude never types a number from memory."""
import datetime as dt

# Each metric lists the XBRL tags companies use for it, in order of preference.
TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue"],
    "rd": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "op_income": ["OperatingIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "assets": ["Assets"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "customer_advances": ["ContractWithCustomerLiabilityCurrent"],
    "equity_gains": ["GainLossOnInvestments", "EquitySecuritiesFvNiGainLoss"],
    "backlog": ["RevenueRemainingPerformanceObligation"],
    "liab_current": ["LiabilitiesCurrent"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "cost_of_revenue": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
}
FLOW = {"revenue", "rd", "net_income", "op_income", "gross_profit", "cfo", "equity_gains", "pretax", "tax", "cost_of_revenue"}

# One-off items stripped from true earnings. Each group lists the company's own XBRL tags, in order of preference;
# the first tag with a value for the quarter is used, so one item is never counted twice within a group.
# sign: +1 = a positive value is a gain; -1 = a positive value is a loss or expense (e.g. warrant fair-value charges).
STRIPS = [
    {"kind": "investments", "label": "Gains on investments",
     "tags": [("GainLossOnInvestments", 1), ("EquitySecuritiesFvNiGainLoss", 1), ("EquitySecuritiesFvNiUnrealizedGainLoss", 1),
              ("GainLossOnSaleOfInvestments", 1)]},
    {"kind": "private_revaluation", "label": "Revaluation of private-company stakes",
     "tags": [("EquitySecuritiesWithoutReadilyDeterminableFairValueUpwardPriceAdjustmentAnnualAmount", 1)],
     "less": [("EquitySecuritiesWithoutReadilyDeterminableFairValueDownwardPriceAdjustmentAnnualAmount", 1)]},
    {"kind": "business_sale", "label": "Gain on sale of a business",
     "tags": [("GainLossOnSaleOfBusiness", 1), ("DisposalGroupNotDiscontinuedOperationGainLossOnDisposal", 1),
              ("GainLossOnDispositionOfBusiness", 1)]},
    {"kind": "stake_sale", "label": "Gain on sale of an equity-method stake",
     "tags": [("EquityMethodInvestmentRealizedGainLossOnDisposal", 1)]},
    {"kind": "warrants", "label": "Warrant revaluation",
     "tags": [("FairValueAdjustmentOfWarrants", -1)]},
    {"kind": "derivatives", "label": "Derivative revaluation (not hedges)",
     "tags": [("UnrealizedGainLossOnDerivatives", 1), ("DerivativeInstrumentsNotDesignatedAsHedgingInstrumentsGainLossNet", 1)]},
]
# For lenders, insurers and asset managers investment and derivative results ARE the business: not stripped.
FINANCIAL_KEEP = {"investments", "private_revaluation", "derivatives"}
# One-off tax benefits the company tags itself. A negative value here lowers the tax charge.
TAX_STRIPS = [("EffectiveIncomeTaxRateReconciliationShareBasedCompensationExcessTaxBenefitAmount", "Excess tax benefit from stock pay"),
              ("IncomeTaxReconciliationChangeInDeferredTaxAssetsValuationAllowance", "Release of a tax valuation allowance")]

def _tag_rows(facts, tag):
    for ns in ("us-gaap", "ifrs-full"):
        node = facts.get("facts", {}).get(ns, {}).get(tag)
        if not node: continue
        unit = next((u for u in node["units"] if u in ("USD", "EUR", "TWD", "KRW", "JPY")), None)
        if not unit: continue
        return [{"end": r["end"], "start": r.get("start"), "days": _days(r), "value": r["val"], "form": r.get("form"),
                 "filed": r.get("filed"), "accn": r.get("accn"), "unit": unit, "tag": tag} for r in node["units"][unit]]
    return []

def _tag_quarter(facts, tag, end, annual_ok=True):
    """The quarter's value for a one-off tag. A fourth quarter is full year minus three quarters; if the 10-Qs
    for that year never reported the item at all, the full-year figure is used and marked 'annual_only'.
    Tax reconciliation items are only ever filed yearly, so they never take the full-year route (annual_ok=False)."""
    rows = _tag_rows(facts, tag)
    if not rows: return None
    r = quarterly(rows).get(end, [None])[-1]
    if r or not annual_ok: return r
    fy = next((x for x in sorted(rows, key=lambda x: x["filed"] or "", reverse=True)
               if x["end"] == end and 350 <= x["days"] <= 380 and x.get("start")), None)
    if fy and not any(x["start"] and fy["start"] <= x["start"] and x["end"] < end for x in rows):
        d = dict(fy); d["derived"] = "annual_only"
        return d
    return None

def _src(r):
    return {"tag": r["tag"], "accn": r["accn"], "form": r["form"], "filed": r["filed"], "derived": r.get("derived") or False}

def one_offs(facts, end, pretax=None, op_income=None, tax=None, financial=False):
    """The one-off items in a quarter, each with the tag and filing it came from. amount > 0 = flattered profit.
    Pre-tax items are listed before tax; the tax item (if any) is already an after-tax amount."""
    found = []
    for g in STRIPS:
        if financial and g["kind"] in FINANCIAL_KEEP: continue
        hit = next(((r, s) for t, s in g["tags"] if (r := _tag_quarter(facts, t, end)) and r["value"]), None)
        if not hit: continue
        r, s = hit
        amt = s * r["value"]; srcs = [_src(r)]
        for t, s2 in g.get("less", []):
            r2 = _tag_quarter(facts, t, end)
            if r2 and r2["value"]:
                amt -= s2 * r2["value"]; srcs.append(_src(r2))
        found.append({"kind": g["kind"], "label": g["label"], "amount": amt, "pretax": True, "src": srcs})
    # Listed-stock gains often already include private-stake revaluations (Alphabet); count them once.
    inv = next((x for x in found if x["kind"] == "investments"), None)
    prv = next((x for x in found if x["kind"] == "private_revaluation"), None)
    if inv and prv and pretax is not None and op_income is not None:
        nonop = abs(pretax - op_income)
        if abs(inv["amount"]) + abs(prv["amount"]) > 1.1 * nonop:
            found.remove(prv if abs(prv["amount"]) <= abs(inv["amount"]) else inv)
    # One-off tax benefits: the company's own tag first; otherwise a tax credit on a profit is treated as one-off.
    tx = next(((r, lab) for t, lab in TAX_STRIPS if (r := _tag_quarter(facts, t, end, annual_ok=False)) and r["value"] < 0), None)
    if tx:
        r, lab = tx
        found.append({"kind": "tax", "label": lab, "amount": -r["value"], "pretax": False, "src": [_src(r)]})
    elif tax is not None and tax < 0 and pretax is not None and pretax > 0:
        found.append({"kind": "tax", "label": "Tax credit on a profit (no tag for the cause): treated as one-off",
                      "amount": -tax, "pretax": False, "src": [{"tag": "IncomeTaxExpenseBenefit"}]})
    return found

def _days(r):
    if "start" not in r: return 0
    return (dt.date.fromisoformat(r["end"]) - dt.date.fromisoformat(r["start"])).days

def series(facts, metric):
    if metric == "revenue":
        return merged_revenue(facts)
    return _series(facts, metric)

def merged_revenue(facts):
    """Revenue is merged across every revenue tag: for each period the largest value wins.
    This survives tag switches and stops a product sub-line being mistaken for total revenue."""
    best = {}
    for ns in ("us-gaap", "ifrs-full"):
        for tag in TAGS["revenue"]:
            node = facts.get("facts", {}).get(ns, {}).get(tag)
            if not node: continue
            unit = next((u for u in node["units"] if u in ("USD", "EUR", "TWD", "KRW", "JPY")), None)
            if not unit: continue
            for r in node["units"][unit]:
                row = {"end": r["end"], "start": r.get("start"), "days": _days(r), "value": r["val"], "form": r.get("form"),
                       "filed": r.get("filed"), "accn": r.get("accn"), "unit": unit, "tag": tag}
                k = (row["end"], row["start"], row["filed"])
                if k not in best or row["value"] > best[k]["value"]:
                    best[k] = row
    return list(best.values())

def _series(facts, metric):
    """All reported values for a metric. Companies switch tags over time, so the tag with the most
    recent data wins (e.g. NVIDIA moved from RevenueFromContract... to Revenues)."""
    best, best_end = [], ""
    for ns in ("us-gaap", "ifrs-full"):
        for tag in TAGS[metric]:
            node = facts.get("facts", {}).get(ns, {}).get(tag)
            if not node: continue
            unit = next((u for u in node["units"] if u in ("USD", "EUR", "TWD", "KRW", "JPY")), None)
            if not unit: continue
            rows = [{"end": r["end"], "start": r.get("start"), "days": _days(r), "value": r["val"], "form": r.get("form"),
                     "filed": r.get("filed"), "accn": r.get("accn"), "unit": unit, "tag": tag}
                    for r in node["units"][unit]]
            end = max((r["end"] for r in rows), default="")
            if end > best_end: best, best_end = rows, end
    return best

def quarterly(rows):
    """One value list per quarter (80-100 day periods), every filed version kept for restatement checks.
    A fourth quarter that is only in the annual report is derived as full year minus the three quarters."""
    q = {}
    for r in rows:
        if 80 <= r["days"] <= 100:
            q.setdefault(r["end"], []).append(r)
    for r in rows:
        if 350 <= r["days"] <= 380 and r["end"] not in q and r.get("start"):
            fy_start, fy_end = dt.date.fromisoformat(r["start"]), dt.date.fromisoformat(r["end"])
            inside = [v[-1] for k, v in q.items() if fy_start < dt.date.fromisoformat(k) < fy_end
                      and v[-1].get("start") and dt.date.fromisoformat(v[-1]["start"]) >= fy_start - dt.timedelta(days=7)]
            if len(inside) == 3:
                d = dict(r); d["value"] = r["value"] - sum(x["value"] for x in inside)
                d["days"] = 91; d["derived"] = True
                q[r["end"]] = [d]
    for k in q: q[k] = sorted(q[k], key=lambda r: r["filed"] or "")
    return dict(sorted(q.items()))

def point(rows):
    """Balance-sheet values (no period)."""
    q = {}
    for r in rows:
        if r["days"] == 0:
            q.setdefault(r["end"], []).append(r)
    return {k: sorted(v, key=lambda r: r["filed"] or "") for k, v in sorted(q.items())}

def latest(d):
    if not d: return None
    k = list(d)[-1]
    return d[k][-1]

def year_ago(d, end):
    e = dt.date.fromisoformat(end)
    for k in reversed(list(d)):
        if 340 <= (e - dt.date.fromisoformat(k)).days <= 390:
            return d[k][-1]
    return None

def build(facts, financial=False):
    """The metric pack for one company: latest quarter values, year-ago comparisons and forensic ratios."""
    out = {}
    q = {m: quarterly(series(facts, m)) for m in FLOW}
    for k, v in q["pretax"].items():  # quarters without an operating-income tag (e.g. KLA) use pre-tax income
        q["op_income"].setdefault(k, v)
    q["op_income"] = dict(sorted(q["op_income"].items()))
    p = {m: point(series(facts, m)) for m in ("receivables", "inventory", "assets", "customer_advances", "backlog", "liab_current")}
    rev = q["revenue"]
    # Some companies (e.g. Vertiv) report cost of sales but not gross profit: gross profit = revenue - cost of sales
    if not q["gross_profit"] and q["cost_of_revenue"]:
        for end, rows in q["cost_of_revenue"].items():
            if end in rev:
                d = dict(rev[end][-1]); d["value"] = rev[end][-1]["value"] - rows[-1]["value"]; d["derived"] = True
                q["gross_profit"][end] = [d]
    last = latest(rev)
    if not last:
        return {"error": "no quarterly revenue in XBRL (foreign filer or new listing)"}
    out["period_end"] = last["end"]; out["unit"] = last["unit"]; out["revenue_derived"] = bool(last.get("derived"))
    for m in FLOW:
        cur = q[m].get(last["end"], [None])[-1]
        prev = year_ago(q[m], last["end"]) if cur else None
        out[m] = cur["value"] if cur else None
        out[m + "_yoy"] = (cur["value"] / prev["value"] - 1) if cur and prev and prev["value"] else None
        out[m + "_src"] = {"accn": cur["accn"], "form": cur["form"], "filed": cur["filed"]} if cur else None
    for m in p:
        v = p[m].get(last["end"], [None])[-1]
        out[m] = v["value"] if v else None
    r = out["revenue"]
    if r:
        if out.get("receivables"): out["dso_days"] = out["receivables"] / r * 91
        if out.get("inventory") and out.get("gross_profit") is not None:
            cogs = r - out["gross_profit"]
            if cogs > 0: out["dio_days"] = out["inventory"] / cogs * 91
        if out.get("rd"): out["rd_intensity"] = out["rd"] / r
        # True earnings: net income minus every one-off item. Pre-tax items come off after tax at the company's own
        # rate this quarter; a stripped tax credit comes off in full. Each strip keeps its tag and filing for the card.
        offs = one_offs(facts, last["end"], out.get("pretax"), out.get("op_income"), out.get("tax"), financial)
        inv = sum(x["amount"] for x in offs if x["kind"] in ("investments", "private_revaluation"))
        if inv: out["equity_gains"] = inv
        # Investment gains are pre-tax; compare with pre-tax profit so the share is not overstated.
        if out.get("equity_gains") and out.get("pretax"):
            out["investment_gain_share"] = out["equity_gains"] / out["pretax"]
        if out.get("net_income") is not None:
            # Tax rate without the stripped tax credit, so pre-tax items are not taxed at a flattered rate.
            tax_strip = sum(x["amount"] for x in offs if x["kind"] == "tax")
            rate = ((out["tax"] + tax_strip) / out["pretax"]) if out.get("tax") is not None and out.get("pretax") else 0.21
            rate = min(max(rate, 0.0), 0.35)
            for x in offs:
                x["after_tax"] = x["amount"] * (1 - rate) if x["pretax"] else x["amount"]
                x["rate"] = rate if x["pretax"] else None
            out["one_offs"] = offs
            out["true_earnings"] = out["net_income"] - sum(x["after_tax"] for x in offs)
        if out.get("gross_profit") is not None: out["gross_margin"] = out["gross_profit"] / r
        if out.get("op_income") is not None: out["op_margin"] = out["op_income"] / r
    # receivables and inventory growth against sales growth (forensic)
    for m in ("receivables", "inventory"):
        cur = p[m].get(last["end"], [None])[-1]
        prev = year_ago(p[m], last["end"]) if cur else None
        out[m + "_yoy"] = (cur["value"] / prev["value"] - 1) if cur and prev and prev["value"] else None
    # Year-ago ratios so flags compare the company with itself, not raw growth rates
    ya = {m: year_ago(q[m], last["end"]) for m in ("revenue", "gross_profit", "op_income")}
    if ya["revenue"] and ya["revenue"]["value"]:
        rv0 = ya["revenue"]["value"]
        if ya["gross_profit"]: out["gross_margin_ya"] = ya["gross_profit"]["value"] / rv0
        if ya["op_income"]: out["op_margin_ya"] = ya["op_income"]["value"] / rv0
        rec0 = year_ago(p["receivables"], last["end"])
        if rec0: out["dso_days_ya"] = rec0["value"] / rv0 * 91
        inv0 = year_ago(p["inventory"], last["end"])
        if inv0 and ya["gross_profit"] and rv0 - ya["gross_profit"]["value"] > 0:
            out["dio_days_ya"] = inv0["value"] / (rv0 - ya["gross_profit"]["value"]) * 91
    # Backlog (remaining performance obligations) and its change
    bl = p["backlog"]
    if bl:
        lastb = list(bl)[-1]; b = bl[lastb][-1]; b0 = year_ago(bl, lastb)
        # Only use a backlog figure dated close to the latest quarter; an old one would mislead
        if abs((dt.date.fromisoformat(lastb) - dt.date.fromisoformat(last["end"])).days) <= 120:
            out["backlog"] = b["value"]; out["backlog_date"] = lastb
            out["backlog_yoy"] = (b["value"] / b0["value"] - 1) if b0 and b0["value"] else None
    # Six-quarter trajectory of revenue growth
    traj = []
    for end in list(rev)[-8:]:
        cur = rev[end][-1]; prev = year_ago(rev, end)
        if prev and prev["value"]:
            traj.append({"end": end, "rev": cur["value"], "yoy": cur["value"] / prev["value"] - 1})
    out["trajectory"] = traj[-6:]
    out["revenue_q"] = [{"end": k, "rev": v[-1]["value"]} for k, v in list(rev.items())[-8:]]
    out["annual"] = annual_traits(facts)
    out["_q"] = q; out["_p"] = p
    return out

def annual(rows):
    a = {}
    for r in rows:
        if 350 <= r["days"] <= 380:
            a.setdefault(r["end"], []).append(r)
    return {k: sorted(v, key=lambda r: r["filed"] or "")[-1]["value"] for k, v in sorted(a.items())}

def annual_traits(facts, years=4):
    """Scott's three filed traits over the last `years` fiscal years:
    revenue grew every year; gross margin of 60% or more; return on capital of 15% or more every year.
    Return on capital here = operating income / (total assets - current liabilities), year-end figures."""
    rev = annual(series(facts, "revenue")); gp = annual(series(facts, "gross_profit"))
    if not gp:
        cor = annual(series(facts, "cost_of_revenue"))
        gp = {k: rev[k] - v for k, v in cor.items() if k in rev}
    op = annual(series(facts, "op_income")); pt = annual(series(facts, "pretax"))
    for k, v in pt.items(): op.setdefault(k, v)   # years without an operating-income tag use pre-tax income
    assets = {k: v[-1]["value"] for k, v in point(series(facts, "assets")).items()}
    lc = {k: v[-1]["value"] for k, v in point(series(facts, "liab_current")).items()}
    ends = list(rev)[-(years + 1):]
    rows = []
    for e in ends:
        cap = (assets.get(e) or 0) - (lc.get(e) or 0)
        rows.append({"end": e, "rev": rev[e], "gm": (gp[e] / rev[e]) if e in gp and rev[e] else None,
                     "roc": (op[e] / cap) if e in op and cap > 0 else None})
    if len(rows) < 4:
        return {"years": rows, "traits": None, "note": "under four filed years: too young to score the traits"}
    grew = all(rows[i]["rev"] > rows[i - 1]["rev"] for i in range(1, len(rows)))
    last = rows[-1]
    gm60 = last["gm"] is not None and last["gm"] >= 0.60
    rocs = [r["roc"] for r in rows[1:] if r["roc"] is not None]
    roc15 = bool(rocs) and all(x >= 0.15 for x in rocs)
    return {"years": rows, "grew_every_year": grew, "gm_60": gm60, "roc_15": roc15,
            "traits": int(grew) + int(gm60) + int(roc15)}
