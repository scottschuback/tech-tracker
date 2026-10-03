"""Movers: the few events that mean a company is on the move, from filings only (prices used for one alert).
Each one is written as 'candidate to price', never 'buy'. Backtested where stated; untested where stated."""
import datetime as dt
from . import store

BIG_CUSTOMERS = ["NVIDIA", "Microsoft", "Google", "Alphabet", "Amazon", "Meta", "Oracle", "OpenAI", "Anthropic", "xAI",
                 "Apple", "Broadcom", "AMD", "Advanced Micro Devices", "TSMC", "Taiwan Semiconductor", "CoreWeave"]

def _prev_snapshot(c, ticker):
    r = c.execute("SELECT * FROM snapshots WHERE ticker=? ORDER BY run_id DESC LIMIT 1", (ticker,)).fetchone()
    return dict(r) if r else None

def detect(c, run_id, cards, packs, guidance, promises, items_recent, prices, new_ids):
    """Returns the list of mover alerts raised this run; also inserts them as items."""
    today = dt.date.today().isoformat()
    movers = []
    def raise_(kind, ticker, title, reason, colour="amber", url="", tag="F", tested="backtested"):
        iid = f"mv:{ticker}:{kind}:{today}"
        if store.add_item(c, id=iid, source="movers", ticker=ticker, kind=f"Mover: {kind}", title=title, url=url,
                          published=today, colour=colour, tag=tag, reason=reason + " Candidate to price, not a buy signal.",
                          extra={"tested": tested}):
            new_ids.append(iid); movers.append({"kind": kind, "ticker": ticker, "title": title, "colour": colour})
    for t, card in cards.items():
        p = packs.get(t) or {}
        prev = _prev_snapshot(c, t)
        g = guidance.get(t) or {}
        # 1. Verdict changes (backtested rules)
        if prev and prev["verdict"] and prev["verdict"] != card["verdict"]:
            up = card["verdict"] in ("Strong", "Cycle low") and prev["verdict"] not in ("Strong",)
            raise_("verdict change", t, f"{t}: verdict moved from {prev['verdict']} to {card['verdict']}",
                   "; ".join(card.get("reasons", [])[:2]), colour="green" if up else "amber")
        # 2. Guidance raised (company's own number changed up in config/guidance.yaml)
        if prev and g.get("revenue") and prev["guidance_rev"] and g.get("period") == prev["guidance_period"]:
            chg = float(g["revenue"]) / prev["guidance_rev"] - 1
            if chg >= 0.03:
                raise_("guidance raised", t, f"{t}: guidance for {g['period']} raised {chg:+.0%}",
                       f"Company now expects about {float(g['revenue'])/1e9:,.1f}B. {g.get('note','')}", colour="green",
                       tag=g.get("tag", "C"), tested="not backtested")
            elif chg <= -0.03:
                raise_("guidance cut", t, f"{t}: guidance for {g['period']} cut {chg:+.0%}",
                       f"Company now expects about {float(g['revenue'])/1e9:,.1f}B. {g.get('note','')}", colour="red",
                       tag=g.get("tag", "C"), tested="not backtested")
        # 3. Backlog up 15%+ in a NEW filing (period changed since last snapshot)
        if "error" not in p and p.get("backlog") and p.get("backlog_yoy") is not None and prev and prev["period_end"] != p.get("period_end"):
            if p["backlog_yoy"] >= 0.15:
                raise_("backlog up", t, f"{t}: filed backlog up {p['backlog_yoy']:+.0%} on a year ago",
                       f"Backlog {p['backlog']/1e9:,.1f}B at {p.get('backlog_date')}.", colour="green", tested="not backtested")
        # 5. The turn: a cyclical whose quarterly revenue rises for the first time after at least two falls.
        # Fires when that quarter is newly filed (or, on the first run, filed in the last 60 days).
        rq = [x["rev"] for x in (p.get("revenue_q") or [])]
        filed = ((p.get("revenue_src") or {}).get("filed")) or ""
        fresh = (prev is not None and prev["period_end"] != p.get("period_end")) or \
                (prev is None and filed >= (dt.date.today() - dt.timedelta(days=60)).isoformat())
        # Backtest (2012-2025, every US filer): a turn on its own means nothing. In chips and hardware, a turn that is
        # ALSO above the same quarter a year ago returned +56% over 3 years and +156% over 5 (66 cases: small sample).
        if len(rq) >= 5 and rq[-2] < rq[-3] < rq[-4] and rq[-1] > rq[-2] and rq[-1] > rq[-5] and fresh and card.get("cyclical"):
            raise_("the turn", t, f"{t}: quarterly revenue rose again after two falls, and is above a year ago",
                   f"Revenue {rq[-1]/1e9:,.2f}B after {rq[-2]/1e9:,.2f}B. Backtest: in chips and hardware this returned +56% over 3 years and +156% over 5, small sample (66).",
                   colour="green", tested="backtested, small sample")
        # 7. Promise delivered early: only when the ledger's state just changed to delivered before the due date
        for pr in promises:
            if pr["ticker"] != t: continue
            key = f"{t}|{pr['promise'][:60]}"
            old = c.execute("SELECT state FROM promise_state WHERE key=?", (key,)).fetchone()
            if old and old["state"] != pr["state"] and pr["state"] == "delivered" and pr["due"] > today:
                raise_("promise delivered early", t, f"{t}: delivered early, {pr['promise']}", f"Due {pr['due']}. {pr.get('note','')}",
                       colour="green", tag=pr.get("tag", "C"), tested="not backtested")
            c.execute("INSERT OR REPLACE INTO promise_state VALUES(?,?)", (key, pr["state"]))
        # price-based: Strong company's price down 20%+ from its 90-day high while filings stay strong (untested)
        px = prices.get(t) or []
        if card["verdict"] == "Strong" and len(px) >= 60:
            hi = max(v for _, v in px[-90:]); last = px[-1][1]
            dd = last / hi - 1
            if dd <= -0.20:
                raise_("price down, filings strong", t, f"{t}: share price {dd:+.0%} from its 90-day high while rated Strong",
                       "Nothing in the filings changed the verdict.", colour="green", tag="R", tested="not backtested")
        # snapshot for next time
        c.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?,?,?)",
                  (run_id, t, card["verdict"], float(g["revenue"]) if g.get("revenue") else None, g.get("period"),
                   p.get("backlog"), p.get("period_end"), p.get("revenue")))
    # 4. New supply or warrant deal naming a big customer (from this run's filing-text hits and 8-K items 1.01 / 3.02)
    for it in items_recent:
        if it["id"] not in new_ids: continue
        text = (it.get("title") or "") + " " + (it.get("reason") or "")
        is_deal = it["source"] == "sec_fulltext" or ("1.01" in it.get("kind", "")) or ("3.02" in it.get("kind", ""))
        own = ((packs.get(it.get("ticker")) or {}).get("name") or "").lower()
        named = [b for b in BIG_CUSTOMERS if b.lower() in text.lower() and b.lower() not in own
                 and not (b == "AMD" and "advanced micro" in own) and not (b == "Google" and "alphabet" in own)]
        if is_deal and named and it.get("ticker"):
            raise_("supply deal", it["ticker"], f"{it['ticker']}: deal naming {', '.join(named[:2])}", it["reason"],
                   colour="green", url=it.get("url", ""), tested="not backtested")
    # 6. Insider buying cluster: two or more different insiders buying in the last 30 days
    since = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    for r in c.execute("SELECT ticker, COUNT(DISTINCT owner) n, SUM(value) v FROM insider_buys WHERE filed>=? GROUP BY ticker HAVING n>=2", (since,)):
        raise_("insider buying", r["ticker"], f"{r['ticker']}: {r['n']} insiders bought shares in the last 30 days",
               f"About {r['v']/1e6:,.1f}M bought on the open market (Form 4).", colour="green", tested="not backtested")
    return movers
