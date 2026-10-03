"""Every number is tested before it is shown. A failed test marks the number red; nothing is guessed."""
import re

def restatements(qseries, keep=8):
    """Same recent period reported with a different value in a later filing = restated.
    Only the last `keep` quarters are checked; old reclassifications are history, not news."""
    found = []
    for end, rows in list(qseries.items())[-keep:]:
        rows = [r for r in rows if not r.get("derived")]
        if len(rows) < 2: continue
        vals = {round(r["value"]) for r in rows}
        if len(vals) > 1 and rows[-1]["filed"] and rows[0]["filed"] != rows[-1]["filed"]:
            first, lastv = rows[0]["value"], rows[-1]["value"]
            if first and abs(lastv / first - 1) > 0.005:
                found.append({"period_end": end, "was": first, "now": lastv, "filed": rows[-1]["filed"]})
    return found

def in_text(value, text):
    """Double pull: is the XBRL value printed in the filing itself (in units, thousands or millions)?"""
    if value is None: return None
    cands = set()
    for div in (1, 1e3, 1e6):
        v = abs(value) / div
        if v >= 1:
            cands.add(f"{round(v):,}")
            cands.add(f"{v:,.1f}")
    return any(re.search(r"(?<![\d,.])" + re.escape(c) + r"(?![\d,])", text) for c in cands)

NAMES = {"revenue": "Revenue", "rd": "R&D spending", "net_income": "Net profit", "op_income": "Operating profit",
         "gross_profit": "Gross profit", "unit": "Currency", "all": "All numbers", "receivables": "Money owed by customers",
         "inventory": "Inventory", "assets": "Total assets"}

def run_checks(pack, text=None):
    """Returns list of {metric, status, note}. status: pass (OK) | flag (check by hand) | fail (held back).
    Fast growth on its own is never flagged: AI suppliers are meant to grow fast. Flags are kept for things that
    usually mean a one-off, an acquisition, a spin-off or a data problem."""
    res = []
    if "error" in pack:
        return [{"metric": "all", "status": "info",
                 "note": "No quarterly numbers filed with the SEC (foreign company or new listing). Filings and alerts still tracked."}]
    if pack.get("unit") not in ("USD", "EUR", "TWD", "KRW", "JPY"):
        res.append({"metric": "unit", "status": "fail", "note": f"Unexpected currency {pack.get('unit')}"})
    for m in ("revenue", "rd", "receivables", "inventory", "assets"):
        v = pack.get(m)
        if v is not None and v < 0:
            res.append({"metric": m, "status": "fail", "note": "Negative where that is impossible: held back"})
    rev = pack.get("revenue")
    if rev and pack.get("gross_profit") and pack["gross_profit"] > rev:
        res.append({"metric": "gross_profit", "status": "fail", "note": "Gross profit above revenue: held back"})
    yrs = (pack.get("annual") or {}).get("years") or []
    if rev and yrs and yrs[-1].get("rev") and rev < 0.3 * yrs[-1]["rev"] / 4:
        res.append({"metric": "revenue", "status": "fail",
                    "note": "Quarter is under 30% of last year's average quarter: the wrong revenue line may have been read"})
    # One-quarter swings (quarter on quarter, not year on year) usually mean an acquisition, a spin-off or a one-off
    rq = pack.get("revenue_q") or []
    if len(rq) >= 2 and rq[-2]["rev"]:
        qq = rq[-1]["rev"] / rq[-2]["rev"] - 1
        if qq > 0.5 or qq < -0.25:
            res.append({"metric": "revenue", "status": "flag",
                        "note": f"Revenue moved {qq:+.0%} in a single quarter: a price surge, an acquisition, a spin-off or a one-off? Worth a look"})
    # R&D running far ahead of sales
    ry, sy = pack.get("rd_yoy"), pack.get("revenue_yoy")
    if ry is not None and sy is not None and ry - sy > 1.0 and (pack.get("rd_intensity") or 0) > 0.2:
        res.append({"metric": "rd", "status": "flag",
                    "note": f"R&D up {ry:+.0%} against sales {sy:+.0%}: often share-based pay after a listing, or an acquisition"})
    # Profit lifted by things that are not the business (gains, tax credits)
    ni, oi = pack.get("net_income"), pack.get("op_income")
    if rev and ni is not None and oi is not None and ni - oi > 0.15 * rev:
        res.append({"metric": "net_income", "status": "flag",
                    "note": "Net profit well above operating profit: lifted by investment gains, interest or tax items. True earnings strip these"})
    for m, rows in (("revenue", pack.get("_q", {}).get("revenue", {})), ("net_income", pack.get("_q", {}).get("net_income", {}))):
        rs = restatements(rows)
        if rs:
            big = max(abs(r["now"] / r["was"] - 1) for r in rs if r["was"])
            what = "re-cut after a spin-off or sale" if big > 0.2 else "changed in a later filing"
            res.append({"metric": m, "status": "flag",
                        "note": f"{len(rs)} past quarter(s) {what} (largest change {big:.0%}). A true restatement also brings an 8-K item 4.02 alert"})
    if text is not None:
        derived = pack.get("revenue_derived")
        for m in ("revenue", "net_income", "rd"):
            v = pack.get(m)
            if v is None: continue
            if derived:
                res.append({"metric": m, "status": "pass",
                            "note": "Fourth quarter worked out as full year minus three quarters; the full-year figure is in the annual report"})
                continue
            ok = in_text(v, text)
            res.append({"metric": m, "status": "pass" if ok else "flag",
                        "note": "Matches the figure printed in the filing" if ok else "Not found printed in the filing: check by hand"})
    return res

def forensic(pack):
    """Plain-English health flags. Each compares the company with itself a year ago, so fast growth alone
    never looks like a problem. Returns (colour, message); green = a good sign."""
    f = []
    d, d0 = pack.get("dso_days"), pack.get("dso_days_ya")
    if d and d0:
        if d - d0 >= 15 and d / d0 >= 1.25:
            f.append(("amber", f"Customers taking longer to pay: {d0:.0f} days a year ago, {d:.0f} now"))
        elif d0 - d >= 10:
            f.append(("green", f"Customers paying faster: {d0:.0f} days a year ago, {d:.0f} now"))
    i, i0 = pack.get("dio_days"), pack.get("dio_days_ya")
    if i and i0:
        if i - i0 >= 20 and i / i0 >= 1.25:
            f.append(("amber", f"Inventory building up: {i0:.0f} days of stock a year ago, {i:.0f} now"))
        elif i0 - i >= 15:
            f.append(("green", f"Inventory turning faster: {i0:.0f} days a year ago, {i:.0f} now"))
    g = pack.get("investment_gain_share")
    if g is not None and g > 0.15 and not pack.get("financial"):  # for lenders and asset managers gains ARE the business
        f.append(("amber", f"Investment gains are {g:.0%} of pre-tax profit: true earnings strip them out"))
    ni, cfo = pack.get("net_income"), pack.get("cfo")
    if ni and cfo is not None and ni > 0 and cfo < 0.7 * ni:
        f.append(("amber", "Operating cash flow under 70% of profit this quarter"))
    gm, gm0 = pack.get("gross_margin"), pack.get("gross_margin_ya")
    if gm is not None and gm0 is not None:
        if gm - gm0 >= 0.02: f.append(("green", f"Gross margin up: {gm0:.0%} to {gm:.0%}"))
        elif gm0 - gm >= 0.03: f.append(("amber", f"Gross margin down: {gm0:.0%} to {gm:.0%}"))
    by = pack.get("backlog_yoy")
    if by is not None and pack.get("backlog") and pack.get("revenue") and pack["backlog"] >= 0.4 * pack["revenue"]:
        if by >= 2.0: f.append(("green", f"Backlog more than tripled ({by:+.0%}): check what was added, often one very large contract or an acquisition"))
        elif by >= 0.15: f.append(("green", f"Backlog up {by:+.0%} on a year ago"))
        elif by <= -0.10: f.append(("amber", f"Backlog down {by:+.0%} on a year ago"))
    return f
