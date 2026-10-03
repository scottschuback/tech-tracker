"""The company card: five plain questions, each with a light and the evidence, then Strong / Watch / Weak.
Built only from filed numbers, company guidance and the business grade. Never from the share price.

Verdict rules (fixed, so they can be tested and backtested; backtested 2014-2021 on 15,447 company-years):
  Strong    = at least 2 of the 3 filed traits, revenue growing, at most 1 amber health flag,
              and the business grade is not 'losing ground'.
  Weak      = business grade 'losing ground', OR (not a cyclical layer AND revenue shrinking),
              OR (not cyclical AND no traits AND no growth).
  Cycle low = a cyclical layer (chips, memory, storage, equipment, materials, foundry, test) with revenue shrinking:
              in the backtest these did as well as Strong over 3 and 5 years, so they are a watch, not a Weak.
  Watch     = everything else.
  'Slowing sharply' no longer blocks Strong: the backtest found it did not predict worse results.
"""
CYCLICAL = {"GPUs", "Custom AI chips", "Memory (HBM, DRAM)", "Storage", "Foundry and advanced packaging",
            "Materials and substrates", "Chipmaking equipment", "Test"}
STATUS_WORD = {"lead": "Leads its layer", "gain": "Gaining ground", "hold": "Holding position",
               "lose": "Losing ground", "unp": "Unproven", "ungraded": "Not graded yet"}

def trajectory_label(traj):
    if len(traj) < 3: return "not enough history", None
    ys = [t["yoy"] for t in traj]
    last, prior = ys[-1], sum(ys[-4:-1]) / len(ys[-4:-1])
    if last < 0: return "shrinking", "red"
    if last - prior >= 0.05: return "speeding up", "green"
    if prior - last >= 0.15: return "slowing sharply", "red"
    if prior - last >= 0.05: return "slowing", "amber"
    return "steady", "green"

def money(v, unit="USD"):
    if v is None: return "n/a"
    sym = {"USD": "$", "EUR": "€"}.get(unit, "")
    return f"{sym}{v/1e9:,.1f}B" if abs(v) >= 1e9 else f"{sym}{v/1e6:,.0f}M"

def card(t, pack, status, layer_names, guidance, promises):
    q = {}
    if not pack or "error" in pack:
        return {"ticker": t, "verdict": "Not scored", "verdict_colour": "grey",
                "reasons": [pack.get("error", "no filed numbers yet") if pack else "no filed numbers yet"],
                "status": status, "status_word": STATUS_WORD.get(status, status), "layers": layer_names, "q": {}}
    unit = pack.get("unit", "USD")
    # 1. Where it is going
    going, gc = [], "grey"
    g = guidance.get(t)
    if g and pack.get("revenue"):
        g = {**g, "revenue": float(g["revenue"])}
        same_unit = g.get("unit", "USD") == unit
        chg = (g["revenue"] / pack["revenue"] - 1) if same_unit else None
        going.append(f"Company expects about {money(g['revenue'], g.get('unit','USD'))} revenue in {g['period']}"
                     + (f", {chg:+.0%} on the last reported quarter" if chg is not None else "")
                     + f" [{g.get('tag','C')}]. {g.get('note','')}".rstrip())
        gc = "green" if chg is None or chg >= 0 else "red"
    if pack.get("backlog"):
        by = pack.get("backlog_yoy")
        yr_rev = (pack.get("revenue") or 0) * 4
        if yr_rev and pack["backlog"] < 0.10 * yr_rev:
            going.append(f"Filed backlog is small ({money(pack['backlog'], unit)}, under 10% of a year's sales), so it is not a real order book for this company [F]")
        else:
            going.append(f"Contracted backlog {money(pack['backlog'], unit)}" + (f", {by:+.0%} on a year ago" if by is not None else "") + " [F]")
            if by is not None: gc = "green" if by >= 0.05 else ("amber" if by > -0.05 else "red")
    mine = [p for p in promises if p["ticker"] == t]
    done = sum(p["state"] == "delivered" for p in mine); late = sum(p["state"] in ("late", "missed", "overdue") for p in mine)
    nxt = sorted([p for p in mine if p["state"] in ("open", "due soon")], key=lambda p: p["due"])
    if done or late: going.append(f"Promise record: {done} delivered, {late} late or missed")
    if late and gc == "green": gc = "amber"
    if nxt: going.append(f"Next promise: {nxt[0]['promise']} by {nxt[0]['due']}")
    if not going: going.append("No guidance or backlog on file yet. The morning routine adds guidance from results releases.")
    q["going"] = {"light": gc, "lines": going}
    # 2. Is it growing
    label, lc = trajectory_label(pack.get("trajectory", []))
    lines = [f"Revenue {money(pack.get('revenue'), unit)} last quarter, {(pack.get('revenue_yoy') or 0):+.0%} on a year ago; growth is {label}"]
    gm, gm0 = pack.get("gross_margin"), pack.get("gross_margin_ya")
    if gm is not None:
        lines.append(f"Gross margin {gm:.0%}" + (f" (was {gm0:.0%})" if gm0 is not None else ""))
    om, om0 = pack.get("op_margin"), pack.get("op_margin_ya")
    if om is not None:
        lines.append(f"Operating margin {om:.0%}" + (f" (was {om0:.0%})" if om0 is not None else ""))
    if pack.get("true_earnings") is not None and pack.get("net_income"):
        te, ni = pack["true_earnings"], pack["net_income"]
        if abs(te - ni) / abs(ni) > 0.03:
            lines.append(f"True earnings {money(te, unit)} vs reported {money(ni, unit)} (investment gains stripped)")
    q["growing"] = {"light": lc or "grey", "lines": lines}
    # 3. Is its technology in demand
    flags = pack.get("forensic", [])
    amb = [m for c, m in flags if c == "amber"]; grn = [m for c, m in flags if c == "green"]
    dem = [STATUS_WORD.get(status, status) + " (business grade, our judgement)"]
    dem += grn[:2] + amb[:2]
    if pack.get("customer_advances"):
        dem.append(f"Customers have paid {money(pack['customer_advances'], unit)} in advance (kept out of earnings)")
    dc = "green" if (status in ("lead", "gain") and len(amb) <= 1) else ("red" if status == "lose" else "amber")
    if status == "ungraded": dc = "green" if not amb and (pack.get("revenue_yoy") or 0) > 0.1 else "amber"
    q["demand"] = {"light": dc, "lines": dem}
    # 4. What is next: filled from the layer timelines by the site builder
    q["next"] = {"light": "grey", "lines": [p["promise"] + f" (due {p['due']})" for p in nxt[:3]]}
    # 5. Verdict
    reasons = []
    tr = (pack.get("annual") or {}).get("traits")
    if tr is None and (pack.get("annual") or {}).get("note"):
        reasons.append((pack["annual"]["note"]).capitalize())
    growing = (pack.get("revenue_yoy") or 0) > 0
    if tr is not None:
        a = pack["annual"]
        reasons.append(f"{tr} of 3 filed traits: revenue grew every year {'yes' if a['grew_every_year'] else 'no'}, "
                       f"gross margin 60%+ {'yes' if a['gm_60'] else 'no'}, return on capital 15%+ every year {'yes' if a['roc_15'] else 'no'}")
    cyclical = bool(set(layer_names) & CYCLICAL)
    if status == "lose" or (not cyclical and (label == "shrinking" or (tr == 0 and not growing))):
        v, vc = "Weak", "red"
    elif tr is not None and tr >= 2 and growing and len(amb) <= 1 and status != "lose":
        v, vc = "Strong", "green"
    elif cyclical and label == "shrinking":
        v, vc = "Cycle low", "blue"
    else:
        v, vc = "Watch", "amber"
    if label == "slowing sharply":
        reasons.append("Growth is slowing sharply: in the backtest this meant bigger swings, not worse results")
    reasons.append(f"Growth {label}; {len(amb)} health warning{'s' if len(amb)!=1 else ''}, {len(grn)} good sign{'s' if len(grn)!=1 else ''}")
    return {"ticker": t, "name": pack.get("name"), "verdict": v, "verdict_colour": vc, "reasons": reasons, "cyclical": cyclical,
            "status": status, "status_word": STATUS_WORD.get(status, status), "layers": layer_names, "q": q,
            "period_end": pack.get("period_end")}
