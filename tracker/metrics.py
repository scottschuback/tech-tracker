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

def build(facts):
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
        # Investment gains are pre-tax; compare with pre-tax profit so the share is not overstated.
        if out.get("equity_gains") and out.get("pretax"):
            out["investment_gain_share"] = out["equity_gains"] / out["pretax"]
        # True earnings: net income minus investment gains after tax at the company's own tax rate this quarter.
        if out.get("net_income") is not None:
            rate = (out["tax"] / out["pretax"]) if out.get("tax") is not None and out.get("pretax") else 0.21
            rate = min(max(rate, 0.0), 0.35)
            out["true_earnings"] = out["net_income"] - (out.get("equity_gains") or 0) * (1 - rate)
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
