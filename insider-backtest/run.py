"""Runs every stage in order. Each stage saves to state/ and is skipped when its output exists, so a stopped run
resumes without refetching. Delete a file in state/ to redo that stage (cache/ is never refetched).

    python run.py
"""
import sys

import numpy as np
import pandas as pd

import backtest as bt
import events
import fetch_sec
import prices
import report
import universe as uni
from common import RESULTS, STATE, log


def stage_universe(book_holder):
    out = STATE / "universe.parquet"
    fin = uni.build_financials()
    tmap = prices.fetch_all(sorted(fin[fin.pass_fin].cik.unique()))
    book = prices.PriceBook(tmap)
    book_holder.append(book)
    if out.exists():
        return pd.read_parquet(out), tmap
    u = uni.apply_market_cap(fin, book.raw_close_on)
    u.to_parquet(out)
    log("10-K rows IN universe:", int(u.in_universe.sum()), "companies:", u[u.in_universe].cik.nunique())
    return u, tmap


def completion_events(ev):
    """For the threshold check: clusters entered once the k-th insider had filed."""
    out = {}
    ins = ev[(ev.kind == "insider")]
    for k in (2, 3, 4):
        e = ins[ins[f"fd_{k}"].notna()].copy()
        e["filing_date"] = pd.to_datetime(e[f"fd_{k}"])
        out[k] = e
    return out


def entry_after(book, e):
    dates = []
    for r in e.itertuples():
        s = book.series(r.issuer_cik)
        ep = prices.entry_point(s, r.filing_date) if s is not None else None
        dates.append(ep[0] if ep else pd.NaT)
    e = e.copy()
    e["entry_date"] = dates
    return e


def stage_returns(ev, u, book):
    out = STATE / "events_returns.parquet"
    universe = uni.Universe(u)
    panel = bt.Panel(book, sorted(u[u.in_universe].cik.unique()))
    controls = bt.control_sets(universe, pd.read_parquet(STATE / "buy_dates.parquet"))
    if out.exists():
        evr = pd.read_parquet(out)
    else:
        evr = bt.add_returns(ev, panel, controls)
        evr.to_parquet(out)
    comp = {}
    for k, e in completion_events(evr).items():
        p = STATE / f"completion_{k}.parquet"
        if p.exists():
            comp[k] = pd.read_parquet(p)
        else:
            comp[k] = bt.add_returns(entry_after(book, e.drop(columns=[c for c in e.columns if c.startswith(
                ("ret_", "ctrl_", "spy_", "dead_", "xs_"))])), panel, controls)
            comp[k].to_parquet(p)
    return evr, comp


def robustness(ev, comp, verdicts):
    rng = np.random.default_rng(7)
    halves = pd.concat([bt.window_table(ev, "2009-2016 entries", (2009, 2016)),
                        bt.window_table(ev, "2017-2021 entries", (2017, 2021))])
    shock = bt.window_table(ev, "2021-2022 entries (rate shock)", (2021, 2022), ("6m", "1y", "3y"))
    surv = []
    for name, tag, val, g in bt.groups(ev):
        for m in bt.MODES:
            v, c = bt.verdict(g, rng, m=m)
            surv.append({"check": f"survivorship: {m}", "group": name, "horizon": "3y", "verdict": v,
                         "count": c["n_3y"], "median_xs_control": c["median_xs_3y"], "ci_lo": c["ci_lo"],
                         "ci_hi": c["ci_hi"]})
    surv = pd.DataFrame(surv)
    thr = bt.threshold_table(ev, comp)
    thr.insert(0, "check", "threshold")
    robust = pd.concat([halves, shock, surv, thr.rename(columns={"family": "group", "n_3y": "count",
                                                                 "median_xs_3y": "median_xs_control"})],
                       ignore_index=True)
    # markdown
    focus = ["All insider buys"] + list(verdicts[verdicts.verdict == "PASS"].group)
    L = ["**By start year** (all insider buys, 3-year median excess vs control):\n",
         "| Year | Events | Median excess |", "|---|---|---|"]
    for yr, r in bt.by_year(ev[ev.kind == "insider"]).iterrows():
        L.append(f"| {yr} | {int(r['count'])} | {report.pct(r['median'])} |")
    L += ["", "**Two halves** (3-year median excess vs control):\n", "| Group | 2009-16 | 2017-21 |", "|---|---|---|"]
    for gname in focus:
        vr = verdicts[verdicts.group == gname].iloc[0]
        L.append(f"| {gname} | {report.pct(vr.half1_median)} | {report.pct(vr.half2_median)} |")
    L += ["", "**Rate-shock window, 2021-2022 entries** (all insider buys):\n",
          "| Horizon | Events | Median excess vs control | 95% interval |", "|---|---|---|---|"]
    for r in shock[shock.group == "All insider buys"].itertuples():
        L.append(f"| {r.horizon} | {r.count} | {report.pct(r.median_xs_control)} | {report.pct(r.ci_lo)} to {report.pct(r.ci_hi)} |")
    L += ["", "**Survivorship** (3-year median excess vs control and verdict):\n",
          "| Group | Excluded | Last price | Zero |", "|---|---|---|---|"]
    for gname in focus:
        s = surv[surv.group == gname].set_index("check")
        L.append(f"| {gname} | " + " | ".join(
            f"{report.pct(s.loc['survivorship: ' + m, 'median_xs_control'])} ({s.loc['survivorship: ' + m, 'verdict']})"
            for m in bt.MODES) + " |")
    L += ["", "**Threshold check** (each cut moved one step either way):\n",
          "| Family | Cuts: verdict (n) | Flag |", "|---|---|---|"]
    for fam, g in thr.groupby("family", sort=False):
        cells = ", ".join(f"{c:g}: {v} ({n})" for c, v, n in zip(g.cut, g.verdict, g.n_3y))
        L.append(f"| {fam} | {cells} | {g.family_flag.iloc[0]} |")
    return robust, "\n".join(L) + "\n\n"


def sentences(ev, groups_df, verdicts):
    allg = groups_df[(groups_df.group == "All insider buys") & (groups_df.survivorship == bt.MAIN)].set_index("horizon")
    x1, x5 = allg.loc["1y", "median_xs_control"], allg.loc["5y", "median_xs_control"]
    if pd.isna(x1) or pd.isna(x5):
        hs = "Not enough complete events to compare 1 and 5 years."
    else:
        per1, per5 = x1, (1 + x5) ** (1 / 5) - 1 if x5 > -1 else np.nan
        hs = (f"The 1-year median excess is {report.pct(x1)}. The 5-year is {report.pct(x5)}, about "
              f"{report.pct(per5)} a year. " +
              ("Per year, the gap is larger at 1 year than at 5." if per1 > per5 else
               "Per year, the gap is at least as large at 5 years as at 1.") +
              " Check the intervals in the table before reading anything into the difference.")
    passed = verdicts[(verdicts.verdict == "PASS")]
    if passed.empty:
        ns = ("**Noise.** No kind of insider buy beat equally good companies without insider buying reliably enough "
              "to pass the tests. In this universe, a buy is not a sign that the company will do better than its peers.")
    else:
        ns = ("**Real, for the passing groups only:** " + ", ".join(passed.group) +
              ". These beat equally good companies with no insider buying, across start years and in both halves. "
              "Every other kind of buy is noise.")
    return hs, ns


def data_notes(u, ev, missing):
    fin = u[u.pass_fin]
    return [
        f"10-K company-years read: {len(u)}. Passing rules 1-3 and the SIC screen: {len(fin)}. IN the universe after "
        f"the $2bn market-cap test: {int(u.in_universe.sum())} ({u[u.in_universe].cik.nunique()} companies).",
        f"Operating income from the pre-tax + interest fallback: {int((u.opinc_src == 'pretax+interest').sum())} "
        f"company-years ({int((fin.opinc_src == 'pretax+interest').sum())} of them passing rules 1-3).",
        f"Capex tag missing, treated as 0: {int(u.capex_missing.sum())} company-years.",
        f"Capital of zero or below, so return on capital is undefined and the year is excluded: "
        f"{int(u.roc_undefined.sum())}.",
        f"Capital averaged across both year-ends: {int(u.capital_avg.sum())} of {len(u)} company-years.",
        f"Companies passing rules 1-3 but with no usable price, so they never enter the universe: "
        f"{int(u[u.price_missing].cik.nunique())}. These are listed in missing_prices.csv.",
        f"Insider events whose price history stops before the 3-year mark (delisted, acquired or a ticker gap): "
        f"{int(ev[(ev.kind == 'insider')].get('dead_3y', pd.Series(dtype=bool)).fillna(False).sum())}.",
    ]


def main():
    fetch_sec.main()
    holder = []
    u, tmap = stage_universe(holder)
    book = holder[0]
    universe = uni.Universe(u)
    ev = events.main(universe, book)
    evr, comp = stage_returns(ev, u, book)

    log("statistics")
    groups_df = bt.group_table(evr)
    verdicts = bt.verdict_table(evr)
    years_df = bt.year_table(evr)
    robust_df, robust_md = robustness(evr, comp, verdicts)

    names = pd.read_parquet(STATE / "companies.parquet").set_index("cik").name.to_dict()
    tickers = {c: v[0] for c, v in tmap.items()}
    dead = evr[evr.dead_3y.fillna(False) | evr.dead_5y.fillna(False)]
    extra = [{"cik": r.issuer_cik, "name": names.get(r.issuer_cik), "ticker": tickers.get(r.issuer_cik),
              "problem": "price history stops before the horizon ends (event " + str(r.event_id) + ")"}
             for r in dead.drop_duplicates("issuer_cik").itertuples()]
    extra += [{"cik": r.issuer_cik, "name": names.get(r.issuer_cik), "ticker": tickers.get(r.issuer_cik),
               "problem": "no price at event entry"} for r in evr[evr.missing_price].itertuples()]
    missing = prices.write_missing(tmap, names, extra)

    out = evr.copy()
    out.insert(1, "company", out.issuer_cik.map(names))
    out.insert(2, "ticker", out.issuer_cik.map(tickers))
    out.to_csv(RESULTS / "events.csv", index=False)
    groups_df.merge(verdicts[["group", "verdict"]], on="group", how="left").to_csv(RESULTS / "groups.csv", index=False)
    verdicts.to_csv(RESULTS / "verdicts.csv", index=False)

    hs, ns = sentences(evr, groups_df, verdicts)
    report.summary_md({"verdicts": verdicts, "groups": groups_df, "events": evr, "horizon_sentence": hs,
                       "noise_sentence": ns, "robust_md": robust_md, "data_notes": data_notes(u, evr, missing)})
    allv = verdicts[verdicts.group == "All insider buys"].iloc[0]
    summary_rows = [
        ["Does insider buying work in this universe?",
         f"All insider buys: median 3-year excess vs control {report.pct(allv.median_xs_3y)} "
         f"({report.pct(allv.ci_lo)} to {report.pct(allv.ci_hi)}), n={int(allv.n_3y)}", allv.verdict],
        ["Is the effect bigger at 1 year than at 5?", hs, ""],
        ["Noise or real?", ns.replace("**", ""), "PASS" if (verdicts.verdict == "PASS").any() else "FAIL"],
    ] + [[f"Tag: {r.group}", f"median 3y excess {report.pct(r.median_xs_3y)} ({report.pct(r.ci_lo)} to "
          f"{report.pct(r.ci_hi)}), n={int(r.n_3y)}, start years beating {report.plain_pct(r.share_years_beating)}, "
          f"halves {report.pct(r.half1_median)} / {report.pct(r.half2_median)}", r.verdict]
         for r in verdicts.itertuples() if r.group != "All insider buys"]
    report.workbook(summary_rows, verdicts, groups_df, years_df, robust_df, missing)

    passed = verdicts[(verdicts.verdict == "PASS") & (verdicts.tag != "entity")]
    recent = evr[(evr.kind == "insider") & (evr.filing_date >= pd.Timestamp("2026-01-01"))]
    report.today_md(passed, recent, names, tickers)
    log("done. results in", RESULTS)


if __name__ == "__main__":
    sys.exit(main())
