"""Writes docs/data.json for the app (GitHub Pages serves docs/). The app page itself is docs/index.html."""
import os, json, datetime as dt

ROOT = os.path.join(os.path.dirname(__file__), "..")
ORDER = {"red": 0, "amber": 1, "green": 2, "grey": 3}

def _items(c, where, args=()):
    rows = c.execute(f"SELECT * FROM items WHERE {where} ORDER BY first_seen DESC", args).fetchall()
    out = [dict(r) | {"extra": json.loads(r["extra"] or "{}")} for r in rows]
    return sorted(out, key=lambda r: (ORDER.get(r["colour"], 9), r["published"] or ""), reverse=False)

def _clean(v):
    if isinstance(v, float): return round(v, 6)
    if isinstance(v, dict): return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list): return [_clean(x) for x in v]
    return v

def build(c, run_id, packs, status, new_ids, wl, sources_cfg, promises, missing, layers=None, guidance=None, prices=None):
    from . import scoreboard
    today = dt.date.today()
    new = [i for i in _items(c, "1=1") if i["id"] in set(new_ids)]
    recent = _items(c, "first_seen >= ?", ((today - dt.timedelta(days=30)).isoformat(),))
    srcs = [{"name": k, "what": v, "status": status.get(k, ("not run", 0, ""))[0],
             "new": status.get(k, ("", 0, ""))[1], "note": status.get(k, ("", 0, ""))[2], "live": True}
            for k, v in sources_cfg["live"].items()]
    srcs += [{"name": k, "what": v, "status": "planned", "new": 0, "note": "", "live": False}
             for k, v in sources_cfg["planned"].items()]
    prom = []
    for p in promises:
        due = p["due"] if isinstance(p["due"], dt.date) else dt.date.fromisoformat(str(p["due"]))
        state = p["status"]
        if state == "open":
            state = "overdue" if due < today else ("due soon" if (due - today).days <= 45 else "open")
        prom.append({**p, "due": due.isoformat(), "state": state})
    from . import verdict
    layers = layers or []; guidance = guidance or {}
    status_of, layers_of = {}, {}
    for L in layers:
        for t, st in L["tickers"].items():
            layers_of.setdefault(t, []).append(L["name"])
            # The company's verdict uses the grade from the first layer it is listed in (its main layer);
            # if that one is ungraded, the first graded layer is used.
            if t not in status_of or (status_of[t][0] == "ungraded" and st != "ungraded"): status_of[t] = (st, L["name"])
    import yaml as _y
    gp = os.path.join(ROOT, "config", "grades.yaml")
    grades = _y.safe_load(open(gp)) if os.path.exists(gp) else {}
    for t, g in (grades or {}).items():
        status_of[t] = (g["grade"], "")
    companies, cards = {}, {}
    for t, p in packs.items():
        st, from_layer = status_of.get(t, ("ungraded", ""))
        cards[t] = verdict.card(t, p, st, layers_of.get(t, []), guidance, prom)
        cards[t]["status_word"] = cards[t]["status_word"] + (f" ({from_layer})" if from_layer and len(layers_of.get(t, [])) > 1 else "")
        why = (grades or {}).get(t, {}).get("why")
        if why and cards[t]["q"].get("demand"):
            cards[t]["q"]["demand"]["lines"][0] = cards[t]["status_word"] + ": " + why
        cards[t]["why"] = why
        nxt = []
        for L in layers:
            if t in L["tickers"]:
                nxt += [f"In its layer ({L['name']}): {x[0]}, {int(x[1])}{'' if int(x[1])==int(x[2]) else '-'+str(int(x[2]))}" for x in L.get("timeline", [])]
        cards[t]["q"].setdefault("next", {"light": "grey", "lines": []})
        cards[t]["q"]["next"]["lines"] = (cards[t]["q"]["next"]["lines"] + nxt)[:6]
        companies[t] = {"pack": p, "card": cards[t], "items": [i for i in recent if i["ticker"] == t][:40]}
    from . import movers as _mv
    mv = _mv.detect(c, run_id, cards, packs, guidance, prom, recent, prices or {}, new_ids)
    new = [i for i in _items(c, "1=1") if i["id"] in set(new_ids)]
    recent = _items(c, "first_seen >= ?", ((today - dt.timedelta(days=30)).isoformat(),))
    layer_pages = []
    for L in layers:
        rows, tot, tot0 = [], 0.0, 0.0
        for t in L["tickers"]:
            p = packs.get(t) or {}
            if "error" in p or not p: rows.append({"ticker": t, "missing": True}); continue
            rows.append({"ticker": t, "name": p.get("name"), "unit": p.get("unit"), "revenue": p.get("revenue"),
                         "yoy": p.get("revenue_yoy"), "gm": p.get("gross_margin"), "om": p.get("op_margin"),
                         "rd": p.get("rd"), "backlog": p.get("backlog"), "backlog_yoy": p.get("backlog_yoy"),
                         "verdict": cards[t]["verdict"], "vc": cards[t]["verdict_colour"],
                         "status": (grades or {}).get(t, {}).get("grade", L["tickers"][t])})
            if p.get("unit") == "USD" and p.get("revenue") and p.get("revenue_yoy") is not None:
                tot += p["revenue"]; tot0 += p["revenue"] / (1 + p["revenue_yoy"])
        for r in rows:
            if not r.get("missing") and r.get("unit") == "USD" and tot: r["share"] = (r["revenue"] or 0) / tot
        tick = set(L["tickers"])
        layer_pages.append({**{k: L.get(k) for k in ("id", "name", "what", "alert", "timeline", "real", "noise", "watch", "offline")},
                            "rows": rows, "total_rev": tot or None, "total_yoy": (tot / tot0 - 1) if tot0 else None,
                            "promises": [p for p in prom if p["ticker"] in tick],
                            "items": [i for i in recent if i["ticker"] in tick][:30]})
    hist = [dict(r) for r in c.execute(
        "SELECT r.run_id, r.started, r.summary FROM runs r ORDER BY r.started DESC LIMIT 30").fetchall()]
    data = {
        "run": {"id": run_id, "time": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "new": len(new), "red": sum(i["colour"] == "red" for i in new),
                "amber": sum(i["colour"] == "amber" for i in new), "green": sum(i["colour"] == "green" for i in new),
                "missing": missing},
        "today": new, "recent": recent[:400], "sources": srcs, "companies": companies,
        "layers": wl["layers"], "offline": wl.get("offline", {}), "promises": prom,
        "layer_pages": layer_pages, "guidance": guidance,
        "movers": [i for i in recent if i["source"] == "movers"],
        "scoreboard": scoreboard.summary(c), "history": hist,
    }
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    json.dump(_clean(data), open(os.path.join(ROOT, "docs", "data.json"), "w"), default=str)
    return data
