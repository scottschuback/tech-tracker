"""Stage 4: insider-buy events.

A qualifying filing: Form 4/5, non-derivative, TRANS_CODE 'P', common stock, price > 0, shares > 0, by an
officer or director who is a person (not a fund or company), issuer IN the universe on the FILING date.
One event per company per 90 days: the first qualifying filing starts it, qualifying filings by any insider in
the next 30 days fold into it (that is how clusters are counted), filings 31-90 days later are dropped.
Entry is the next trading day's close after the starting filing date - never the transaction date.
Buys by funds/companies (no officer/director person on the filing) form a separate "10% owner / entity" set.
"""
import re, sys

import numpy as np
import pandas as pd

from common import STATE, log
from prices import drawdown_52w, entry_point

EVENT_DAYS = 90
FOLD_DAYS = 30

COMMON = re.compile(r"common|ordinary|class [a-c] (stock|shares)|^\s*class [a-c]\s*$|capital stock", re.I)
NOT_COMMON = re.compile(r"preferred|warrant|unit|note|debenture|option|right|depositary", re.I)
ENTITY = re.compile(r"\b(L\.?P\.?|L\.?L\.?C\.?|INC\.?|CORP\.?|CORPORATION|COMPANY|HOLDINGS?|TRUST|TRUSTEES?|FUND|"
                    r"PARTNERS|PARTNERSHIP|CAPITAL|MANAGEMENT|LTD\.?|LIMITED|GROUP|ADVISORS|ADVISERS|INVESTMENTS?|"
                    r"FOUNDATION|ASSOCIATES|VENTURES|ENTERPRISES|PLC|N\.?V\.?|S\.?A\.?|AG|GMBH|BANK)\b", re.I)
TOP = re.compile(r"\bCEO\b|\bCFO\b|CHIEF EXECUTIVE|CHIEF FINANCIAL|PRINCIPAL EXECUTIVE|PRINCIPAL FINANCIAL|CHAIR", re.I)
PRES = re.compile(r"PRESIDENT|\bPRES\b", re.I)
VP = re.compile(r"VICE[\s-]*PRES|\bV\.?P\b|\bEVP\b|\bSVP\b", re.I)
ROLE_RANK = {"Top exec": 3, "Officer": 2, "Director": 1}


def role_of(is_officer, is_director, title):
    title = title or ""
    if TOP.search(title) or (PRES.search(title) and not VP.search(title)):
        return "Top exec"
    if is_officer:
        return "Officer"
    if is_director:
        return "Director"
    return None


def is_person(name):
    return not ENTITY.search(name or "")


def purchases(trades):
    t = trades[(trades.trans_code == "P") & (trades.price > 0) & (trades.shares > 0)
               & (trades.acq_disp.isin(["A", ""]))]
    title = t.security_title.fillna("")
    return t[title.str.contains(COMMON) & ~title.str.contains(NOT_COMMON)]


def owner_roles(owners):
    o = owners.copy()
    o["role"] = [role_of(a, b, c) for a, b, c in zip(o.is_officer, o.is_director, o.officer_title)]
    o["person"] = o.owner_name.map(is_person)
    o["insider"] = o.role.notna() & o.person
    return o


def filings(buys, owners):
    """One row per buy filing: issuer, filing date, its insiders (or entity), shares, dollars, pre-holding."""
    o = owner_roles(owners)
    io = o[o.insider].drop_duplicates(["accession", "owner_cik"]).sort_values(["accession", "owner_cik"])
    ins = io.groupby("accession").agg(insiders=("owner_cik", tuple), roles=("role", tuple))
    b = buys.copy()
    b["dollars"] = b.shares * b.price
    b["pre"] = b.shares_after - b.shares
    first = b.sort_values(["accession", "trans_date"]).groupby(["accession", "direct"]).pre.first() \
        .clip(lower=0).groupby("accession").sum()
    f = b.groupby("accession").agg(issuer_cik=("issuer_cik", "first"), filing_date=("filing_date", "first"),
                                   trans_date=("trans_date", "min"), shares=("shares", "sum"),
                                   dollars=("dollars", "sum"))
    f["pre_holding"] = first
    f = f.join(ins, how="left")
    f["kind"] = np.where(f.insiders.notna(), "insider", "entity")
    f["insiders"] = f.insiders.where(f.insiders.notna(), None)
    return f.reset_index().sort_values(["issuer_cik", "filing_date", "accession"])


def dedupe(f):
    """Group filings (one issuer or many) into events. Returns f with an `event_id` column; filings 31-90 days
    after an event start get event_id = -1 (dropped)."""
    ids = np.full(len(f), -1)
    nxt = 0
    for _, idx in f.groupby("issuer_cik", sort=False).indices.items():
        start, eid = None, -1
        for i in idx:
            d = f.filing_date.iloc[i]
            if start is None or (d - start).days >= EVENT_DAYS:
                start, eid, nxt = d, nxt, nxt + 1
                ids[i] = eid
            elif (d - start).days <= FOLD_DAYS:
                ids[i] = eid
    out = f.copy()
    out["event_id"] = ids
    return out


def insider_history(trades, owners):
    """Open-market trades (P and S) per insider per issuer: owner_cik, issuer_cik, date, code."""
    o = owners[["accession", "owner_cik"]]
    t = trades[trades.trans_code.isin(["P", "S"])][["accession", "issuer_cik", "trans_date", "trans_code"]]
    h = t.merge(o, on="accession").dropna(subset=["trans_date"])
    return h.drop_duplicates(["owner_cik", "issuer_cik", "trans_date", "trans_code"])


def routine_label(hist, when):
    """Cohen-Malloy-Pomorski for one insider in one company. hist: rows with trans_date, trans_code before `when`.
    Routine: bought in the same calendar month in each of the 3 prior calendar years.
    Opportunistic: traded in each of the 3 prior years, but not that pattern. Unclassified: < 3 years of history."""
    y, m = when.year, when.month
    prior = hist[hist.trans_date.dt.year.between(y - 3, y - 1)]
    years = set(prior.trans_date.dt.year)
    if not {y - 1, y - 2, y - 3} <= years:
        return "Unclassified"
    buys = prior[(prior.trans_code == "P") & (prior.trans_date.dt.month == m)]
    return "Routine" if {y - 1, y - 2, y - 3} <= set(buys.trans_date.dt.year) else "Opportunistic"


def event_routine(labels):
    if "Opportunistic" in labels:
        return "Opportunistic"
    if "Routine" in labels:
        return "Routine"
    return "Unclassified"


def size_bucket(x, cuts, labels):
    if x is None or np.isnan(x):
        return None
    for c, l in zip(cuts, labels):
        if x < c:
            return l
    return labels[-1]


def build_events(f, hist_by, universe, book):
    """f: qualifying filings of one kind, deduped. Returns one row per event with tags and entry prices."""
    rows = []
    for eid, g in f[f.event_id >= 0].groupby("event_id"):
        g = g.sort_values(["filing_date", "accession"])
        cik = int(g.issuer_cik.iloc[0])
        start = g.filing_date.iloc[0]
        s = book.series(cik)
        ep = entry_point(s, start) if s is not None else None
        r = {"event_id": eid, "issuer_cik": cik, "filing_date": start, "first_trans_date": g.trans_date.iloc[0],
             "n_filings": len(g), "dollars": g.dollars.sum(), "shares": g.shares.sum(), "accessions": ";".join(g.accession)}
        if g.kind.iloc[0] == "insider":
            seen, pre, labels, best = [], 0.0, [], None
            completion = {}
            for row in g.itertuples():
                if any(ocik not in seen for ocik in row.insiders) and not np.isnan(row.pre_holding):
                    pre += row.pre_holding
                for ocik, role in zip(row.insiders, row.roles):
                    if best is None or ROLE_RANK[role] > ROLE_RANK[best]:
                        best = role
                    if ocik in seen:
                        continue
                    seen.append(ocik)
                    completion.setdefault(len(seen), row.filing_date)
                    h = hist_by.get((ocik, cik))
                    labels.append(routine_label(h[h.trans_date < row.trans_date], row.trans_date)
                                  if h is not None else "Unclassified")
            r.update(role=best, n_insiders=len(seen), routine=event_routine(labels),
                     stake=(r["shares"] / pre) if pre > 0 else np.inf,
                     fd_2=completion.get(2), fd_3=completion.get(3), fd_4=completion.get(4))
        else:
            r.update(role="10% owner / entity", n_insiders=np.nan, routine=None, stake=np.nan)
        if ep is None:
            r.update(entry_date=None, missing_price=True)
        else:
            ed, adj, close, raw = ep
            u = universe.row(cik, start)
            shares_out = u[1] if u else np.nan
            r.update(entry_date=ed, entry_adj=adj, missing_price=False, mktcap=shares_out * raw,
                     drawdown=drawdown_52w(s, ed))
        rows.append(r)
    ev = pd.DataFrame(rows)
    if ev.empty:
        return ev
    ev["cluster"] = ev.n_insiders.map(lambda n: None if np.isnan(n) else ("Single" if n == 1 else "Pair" if n == 2 else "Cluster"))
    ev["size_usd"] = ev.dollars.map(lambda x: size_bucket(x, [1e5, 1e6], ["<$100k", "$100k-$1m", ">$1m"]))
    ev["size_stake"] = ev.stake.map(lambda x: size_bucket(x, [0.10, 0.50], ["<10%", "10-50%", ">50%"]))
    ev["price_context"] = ev.get("drawdown", pd.Series(np.nan, index=ev.index)).map(
        lambda x: size_bucket(x, [0.10, 0.25], ["<10% off high", "10-25% off high", ">25% off high"]))
    ev["company_size"] = ev.get("mktcap", pd.Series(np.nan, index=ev.index)).map(
        lambda x: size_bucket(x, [10e9, 100e9], ["$2-10bn", "$10-100bn", ">$100bn"]))
    return ev


def main(universe, book):
    out = STATE / "events.parquet"
    if out.exists():
        log("events already built")
        return pd.read_parquet(out)
    trades = pd.read_parquet(STATE / "trades.parquet")
    owners = pd.read_parquet(STATE / "owners.parquet")
    f = filings(purchases(trades), owners)
    f = f.dropna(subset=["filing_date", "issuer_cik"])
    # every individual-insider buy filing, IN universe or not: the control group excludes these companies
    f[f.kind == "insider"][["issuer_cik", "filing_date"]].to_parquet(STATE / "buy_dates.parquet")
    f["in_u"] = [universe.contains(int(c), d) for c, d in zip(f.issuer_cik, f.filing_date)]
    f = f[f.in_u]
    o = owner_roles(owners)
    hist = insider_history(trades, o[o.insider])
    hist_by = {k: g for k, g in hist.groupby(["owner_cik", "issuer_cik"])}
    parts = []
    for kind in ("insider", "entity"):
        d = dedupe(f[f.kind == kind])
        e = build_events(d, hist_by, universe, book)
        e["kind"] = kind
        parts.append(e)
        log(kind, "events:", len(e))
    ev = pd.concat(parts, ignore_index=True)
    ev.to_parquet(out)
    return ev


if __name__ == "__main__":
    log("run via run.py: events need the universe and prices")
    sys.exit(0)
