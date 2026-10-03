"""Daily run. Each source runs on its own; one failing source never stops the others, and every failure
is shown in the app with its reason. Usage: python -m tracker.run [--backfill]"""
import os, sys, json, yaml, datetime as dt, traceback
from . import store, metrics, checks, alerts, scoreboard
from .sources import sec, govt, insiders, prices, discovery
from . import movers

ROOT = os.path.join(os.path.dirname(__file__), "..")
cfg = lambda f: yaml.safe_load(open(os.path.join(ROOT, "config", f)))

def main(backfill=False):
    c = store.connect()
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M")
    started = store.now()
    last = c.execute("SELECT MAX(started) m FROM runs").fetchone()["m"]
    since = (dt.date.fromisoformat(last[:10]) - dt.timedelta(days=2)).isoformat() if last \
        else (dt.date.today() - dt.timedelta(days=14)).isoformat()
    status, new_ids = {}, []
    def src(name, fn):
        try:
            n = fn()
            status[name] = ("ok" if n else "quiet", n or 0, "")
        except Exception as e:
            status[name] = ("failed", 0, str(e)[:300])
            traceback.print_exc()

    LAYERS = cfg("layers.yaml")
    wl = {"layers": {L["name"]: list(L["tickers"]) for L in LAYERS},
          "offline": {k: v for L in LAYERS for k, v in L.get("offline", {}).items()}}
    tickers = sorted({t for L in LAYERS for t in L["tickers"]})
    layer_of, policy = {}, {}
    financial = {t for L in LAYERS for t in L.get("financials", [])}
    for L in LAYERS:
        for t in L["tickers"]:
            layer_of.setdefault(t, L["name"])
            # a company alerts fully if any of its layers does
            if policy.get(t) != "all": policy[t] = L.get("alert", "all")
    found, missing = sec.resolve(tickers)
    filings, facts, packs, forms4 = {}, {}, {}, {}

    def do_filings():
        n = 0
        look = "2019-01-01" if backfill else since
        for t, (cik, name) in found.items():
            fl = sec.recent_filings(cik, look)
            filings[t] = fl
            for f in fl:
                if f["filed"] < since: continue
                if f["form"] == "4":
                    forms4[t] = forms4.get(t, 0) + 1
                    if not c.execute("SELECT 1 FROM insider_buys WHERE accn=?", (f["accn"],)).fetchone():
                        for b in insiders.parse_form4(f["url"]):
                            c.execute("INSERT OR IGNORE INTO insider_buys VALUES(?,?,?,?,?,?,?)",
                                      (f["accn"], t, b["owner"], f["filed"], b["shares"], b["price"], b["value"]))
                            n += 1
                    continue
                colour, why, label = alerts.classify_filing(f)
                if colour == "grey": continue
                if policy.get(t) in ("red_only", "watch") and colour != "red": continue
                iid = "sec:" + f["accn"]
                if store.add_item(c, id=iid, source="sec_filings", ticker=t, kind=label,
                                  title=f"{name}: {label}" + (f" ({f['desc']})" if f.get("desc") else ""),
                                  url=f["url"], published=f["filed"], colour=colour, tag="F", reason=why,
                                  extra={"layer": layer_of.get(t)}):
                    n += 1; new_ids.append(iid)
                    if colour in ("red", "amber"):
                        scoreboard.record(c, iid, t, label, colour, f["filed"])
        for t, k in forms4.items():
            iid = f"f4:{t}:{dt.date.today().isoformat()}"
            store.add_item(c, id=iid, source="sec_filings", ticker=t, kind="Insider trades",
                           title=f"{found[t][1]}: {k} insider trade filings (Form 4)", url=
                           f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={found[t][0]}&type=4",
                           published=dt.date.today().isoformat(), colour="grey", tag="F",
                           reason="Insider trades summarised; open to see buys and sells")
        return n

    def do_numbers():
        n = 0
        cache_p = os.path.join(ROOT, "data", "text_checks.json")
        cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
        for t, (cik, name) in found.items():
            try:
                facts[t] = sec.company_facts(cik)
            except Exception as e:
                packs[t] = {"error": f"no XBRL data: {str(e)[:80]}"}; continue
            p = metrics.build(facts[t], financial=t in financial)
            if t in financial: p["financial"] = True
            text = None
            src_ = p.get("revenue_src") if "error" not in p else None
            if src_ and src_["accn"] not in cache:
                doc = next((x for x in filings.get(t, []) if x["accn"] == src_["accn"]), None)
                if doc:
                    try:
                        text = sec.doc_text(doc["url"])
                        cache[src_["accn"]] = [r for r in checks.run_checks(p, text) if r["note"].startswith(("Matches", "Not found", "Fourth quarter"))]
                    except Exception: pass
            res = checks.run_checks(p)
            if src_ and src_["accn"] in cache: res += cache[src_["accn"]]
            fz = checks.forensic(p) if "error" not in p else []
            p.pop("_q", None); p.pop("_p", None)
            p["checks"] = res; p["forensic"] = fz; p["name"] = name; p["layer"] = layer_of.get(t); p["cik"] = cik
            packs[t] = p
            for col, msg in fz:
                iid = f"fx:{t}:{p['period_end']}:{msg[:25]}"
                if store.add_item(c, id=iid, source="sec_numbers", ticker=t, kind="Forensic flag", title=f"{name}: {msg}",
                                  url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-Q",
                                  published=p["period_end"], colour=col, tag="F", reason="Computed from the company's filed numbers"):
                    n += 1; new_ids.append(iid)
            for r in res:
                if r["status"] == "fail":
                    iid = f"ck:{t}:{p.get('period_end')}:{r['metric']}:{r['note'][:30]}"
                    if store.add_item(c, id=iid, source="sec_numbers", ticker=t, kind="Check failed",
                                      title=f"{name}: {r['metric']} {r['note']}", url="", published=p.get("period_end"),
                                      colour="red", tag="F", reason="A number failed its test and is held back"):
                        n += 1; new_ids.append(iid)
        json.dump(cache, open(cache_p, "w"))
        return n

    def do_fulltext():
        n = 0
        ciks = [v[0] for v in found.values()]
        by_cik = {v[0]: t for t, v in found.items()}
        for ph in cfg("rules.yaml")["watch_phrases"]:
            for h in sec.full_text_search(ph, since, ["8-K", "10-Q", "10-K", "6-K", "20-F"], ciks):
                t = by_cik.get(int(h["cik"] or 0), "")
                iid = f"ft:{h['accn']}:{ph}"
                if store.add_item(c, id=iid, source="sec_fulltext", ticker=t, kind=f"Phrase: {ph}",
                                  title=f"{', '.join(h['names'])[:90]}: '{ph}' in {h['form']}", url=h["url"],
                                  published=h["filed"], colour="amber", tag="F",
                                  reason="Supply-lock or technology phrase found: read the passage"):
                    n += 1; new_ids.append(iid)
        return n

    def do_form_d():
        n = 0
        for ph in ["semiconductor", "AI accelerator", "photonic", "inference chip"]:
            for h in sec.full_text_search(ph, since, ["D"]):
                iid = f"fd:{h['accn']}"
                if store.add_item(c, id=iid, source="sec_form_d", ticker="", kind="Private raise (Form D)",
                                  title=f"{', '.join(h['names'])[:90]}: raising money ('{ph}')", url=h["url"],
                                  published=h["filed"], colour="green", tag="F", reason="Private company fundraising notice"):
                    n += 1; new_ids.append(iid)
        return n

    def gov(name, fn, *a):
        def inner():
            n = 0
            for it in fn(*a):
                if store.add_item(c, id=it["id"], source=name, ticker="", kind=it["kind"], title=it["title"], url=it["url"],
                                  published=it["published"], colour=it["colour"], tag="F", reason=it["reason"], extra=it.get("extra", {})):
                    n += 1; new_ids.append(it["id"])
            return n
        return inner

    def do_promises():
        n = 0
        today = dt.date.today()
        for p in cfg("promises.yaml"):
            due = p["due"] if isinstance(p["due"], dt.date) else dt.date.fromisoformat(str(p["due"]))
            if p["status"] == "open" and due < today:
                iid = f"pr:{p['ticker']}:{p['promise'][:40]}:overdue"
                if store.add_item(c, id=iid, source="promises", ticker=p["ticker"], kind="Promise due",
                                  title=f"{p['ticker']}: due {due} — {p['promise']}", url="", published=today.isoformat(),
                                  colour="amber", tag=p.get("tag", "C"),
                                  reason="Due date passed: confirm delivered, late or missed in config/promises.yaml"):
                    n += 1; new_ids.append(iid)
        return n

    src("sec_filings", do_filings)
    src("sec_numbers", do_numbers)
    src("sec_fulltext", do_fulltext)
    src("sec_form_d", do_form_d)
    src("federal_register", gov("federal_register", govt.federal_register, since))
    src("usaspending", gov("usaspending", govt.usaspending, since))
    assignees = ["NVIDIA", "Advanced Micro Devices", "Broadcom", "Intel", "Micron", "SK hynix", "Samsung",
                 "Taiwan Semiconductor", "Marvell", "Google", "Amazon", "Qualcomm", "Cerebras", "Lightmatter", "Ayar"]
    src("patents", gov("patents", govt.patents, since, assignees))
    src("promises", do_promises)
    price_hist = {}
    def do_prices():
        n = 0
        for t in found:
            try:
                h = prices.history(t)
                price_hist[t] = h
                for d_, v in h[-5:]:
                    c.execute("INSERT OR IGNORE INTO prices VALUES(?,?,?)", (t, d_, v)); n += 1
            except Exception as e:
                if not price_hist and t == list(found)[0]: raise
        return n
    src("prices", do_prices)
    src("discovery", gov("discovery", discovery.scan, since, set(found)))

    if backfill:
        nb = scoreboard.backfill(c, filings, lambda it: (alerts.RULES["eight_k_items"].get(it) or {}).get("colour"))
        print(f"backfill: {nb} past alerts seeded")
    try:
        scoreboard.evaluate(c, facts)
    except Exception: traceback.print_exc()

    for s, (st, n, note) in status.items():
        c.execute("INSERT OR REPLACE INTO source_status VALUES(?,?,?,?,?)", (run_id, s, st, n, note))
    summary = {"since": since, "new": len(new_ids), "missing_tickers": missing}
    c.execute("INSERT INTO runs VALUES(?,?,?,?)", (run_id, started, store.now(), json.dumps(summary)))
    c.commit()
    from . import site
    site.build(c, run_id, packs, status, new_ids, wl, cfg("sources.yaml"), cfg("promises.yaml"), missing,
               LAYERS, cfg("guidance.yaml") or {}, price_hist)
    c.commit()
    return run_id, status, new_ids

if __name__ == "__main__":
    rid, st, new = main(backfill="--backfill" in sys.argv)
    print(rid, {k: v[:2] for k, v in st.items()}, len(new), "new")
