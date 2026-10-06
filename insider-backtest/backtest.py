"""Stage 5: returns, controls, group statistics, pass/fail and robustness.

Returns are total returns (adjusted close) from the entry close to the last close on or before entry + horizon.
Control for each event: every other company IN the universe on the event's filing date with no insider purchase
filed in the 12 months before entry, measured over the same dates. Headline = event return - control median.
Survivorship: a series that stops before the horizon ends is (a) excluded, (b) held at its last price, or
(c) set to -100%. Version (b) is the main one; all three are reported.
"""
import numpy as np
import pandas as pd

from common import STATE, log

HORIZONS = {"6m": 182, "1y": 365, "3y": 1095, "5y": 1826}
YEARS = {"6m": (2009, 2025), "1y": (2009, 2025), "3y": (2009, 2023), "5y": (2009, 2021)}
MODES = ("exclude", "last", "zero")
MAIN = "last"
STALE_DAYS = 10
BOOT = 2000
MIN_EVENTS = 100
MIN_YEAR_EVENTS = 5

TAGS = ["role", "cluster", "size_usd", "size_stake", "routine", "price_context", "company_size"]


class Panel:
    """Adjusted closes for many companies on SPY's trading calendar, for vectorised returns."""
    def __init__(self, book, ciks):
        self.dates = book.bench.index
        self.days = self.dates.values.astype("datetime64[D]").astype(np.int64)
        self.data_end = self.dates[-1]
        self.spy = book.bench.adjclose.reindex(self.dates).to_numpy()
        self.col = {}
        cols, last_day = [], []
        for c in ciks:
            s = book.series(c)
            if s is None:
                continue
            a = s.adjclose.reindex(self.dates)
            if a.notna().sum() == 0:
                continue
            self.col[c] = len(cols)
            last_day.append(a.last_valid_index())
            cols.append(a.to_numpy())
        self.raw = np.column_stack(cols) if cols else np.empty((len(self.dates), 0))
        self.ffill = pd.DataFrame(self.raw).ffill().to_numpy()
        self.last_day = np.array([d.toordinal() for d in last_day]) if last_day else np.array([])

    def idx(self, date):
        """Index of the last trading day on or before date."""
        return int(np.searchsorted(self.days, np.datetime64(date, "D").astype(np.int64), side="right") - 1)

    def returns(self, cols, entry_date, days):
        """Per-mode return arrays for columns `cols` from entry_date over `days`. None if horizon incomplete."""
        target = entry_date + pd.Timedelta(days=days)
        if target > self.data_end:
            return None
        i, j = self.idx(entry_date), self.idx(target)
        p0 = self.raw[i, cols]
        r_last = self.ffill[j, cols] / p0 - 1
        dead = self.last_day[cols] < (target - pd.Timedelta(days=STALE_DAYS)).toordinal()
        ok = ~np.isnan(p0)
        out = {"last": np.where(ok, r_last, np.nan)}
        out["exclude"] = np.where(ok & ~dead, r_last, np.nan)
        out["zero"] = np.where(ok & dead, -1.0, out["last"])
        return out, dead

    def spy_return(self, entry_date, days):
        target = entry_date + pd.Timedelta(days=days)
        if target > self.data_end:
            return np.nan
        return self.spy[self.idx(target)] / self.spy[self.idx(entry_date)] - 1


def control_sets(universe, buy_dates):
    """Function (filing_date, entry_date) -> control CIKs."""
    bd = buy_dates.sort_values("filing_date")
    by = {c: g.filing_date.to_numpy() for c, g in bd.groupby("issuer_cik")}

    def controls(filing_date, entry_date):
        m = universe.members(filing_date).cik.to_numpy()
        lo = np.datetime64(entry_date - pd.Timedelta(days=365))
        hi = np.datetime64(entry_date)
        keep = []
        for c in m:
            d = by.get(c)
            if d is not None:
                k = np.searchsorted(d, lo)
                if k < len(d) and d[k] < hi:
                    continue
            keep.append(c)
        return keep
    return controls


def add_returns(ev, panel, controls, entry_col="entry_date", filing_col="filing_date", prefix=""):
    """Adds ret/ctrl/spy/excess columns per horizon and mode. Control medians are cached per (filing, entry) date."""
    ev = ev.copy()
    cache = {}
    for h, days in HORIZONS.items():
        for m in MODES:
            ev[f"{prefix}ret_{h}_{m}"] = np.nan
            ev[f"{prefix}ctrl_{h}_{m}"] = np.nan
        ev[f"{prefix}spy_{h}"] = np.nan
        ev[f"{prefix}dead_{h}"] = False
    for n, r in enumerate(ev.itertuples()):
        ed, fd = getattr(r, entry_col), getattr(r, filing_col)
        if ed is None or pd.isna(ed) or r.issuer_cik not in panel.col:
            continue
        key = (fd, ed)
        if key not in cache:
            ccols = np.array([panel.col[c] for c in controls(fd, ed) if c in panel.col], dtype=int)
            cache[key] = {}
            for h, days in HORIZONS.items():
                res = panel.returns(ccols, ed, days)
                cache[key][h] = None if res is None else {m: np.nanmedian(v) if np.isfinite(v).any() else np.nan
                                                         for m, v in res[0].items()}
            cache[key]["n"] = len(ccols)
        own = np.array([panel.col[r.issuer_cik]])
        for h, days in HORIZONS.items():
            res = panel.returns(own, ed, days)
            if res is None:
                continue
            vals, dead = res
            i = ev.index[n]
            for m in MODES:
                ev.at[i, f"{prefix}ret_{h}_{m}"] = vals[m][0]
                ev.at[i, f"{prefix}ctrl_{h}_{m}"] = cache[key][h][m]
            ev.at[i, f"{prefix}spy_{h}"] = panel.spy_return(ed, days)
            ev.at[i, f"{prefix}dead_{h}"] = bool(dead[0])
        ev.at[ev.index[n], f"{prefix}n_control"] = cache[key]["n"]
        if n % 500 == 0:
            log(f"returns {n}/{len(ev)}")
    for h in HORIZONS:
        for m in MODES:
            ev[f"{prefix}xs_ctrl_{h}_{m}"] = ev[f"{prefix}ret_{h}_{m}"] - ev[f"{prefix}ctrl_{h}_{m}"]
        ev[f"{prefix}xs_spy_{h}"] = ev[f"{prefix}ret_{h}_{MAIN}"] - ev[f"{prefix}spy_{h}"]
    return ev


def in_years(ev, h, years=None):
    lo, hi = years or YEARS[h]
    y = pd.to_datetime(ev.entry_date).dt.year
    return ev[y.between(lo, hi)]


def boot_ci(x, rng):
    if len(x) < 2:
        return np.nan, np.nan
    meds = np.median(x[rng.integers(0, len(x), (BOOT, len(x)))], axis=1)
    return float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def stats(g, h, m, rng=None):
    rng = np.random.default_rng(0)  # same seed every call, so one group always shows the same interval
    ret, ctrl = g[f"ret_{h}_{m}"], g[f"ctrl_{h}_{m}"]
    ok = ret.notna() & ctrl.notna()
    ret, ctrl, spy = ret[ok], ctrl[ok], g[f"spy_{h}"][ok]
    xs = (ret - ctrl).to_numpy()
    lo, hi = boot_ci(xs, rng)
    n = int(ok.sum())
    return {
        "count": n,
        "median": ret.median() if n else np.nan,
        "mean": ret.mean() if n else np.nan,
        "pct_positive": (ret > 0).mean() if n else np.nan,
        "pct_beat_control": (ret > ctrl).mean() if n else np.nan,
        "pct_beat_spy": (ret > spy).mean() if n else np.nan,
        "median_xs_control": float(np.median(xs)) if n else np.nan,
        "ci_lo": lo, "ci_hi": hi,
        "median_xs_spy": (ret - spy).median() if n else np.nan,
        "control_median": ctrl.median() if n else np.nan,
    }


def groups(ev):
    """(name, tag, value, frame) for every group to test."""
    ins = ev[ev.kind == "insider"]
    out = [("All insider buys", "all", "all", ins)]
    for t in TAGS:
        for v in sorted(ins[t].dropna().unique()):
            out.append((f"{t} = {v}", t, v, ins[ins[t] == v]))
    out.append(("10% owner / entity", "entity", "entity", ev[ev.kind == "entity"]))
    return out


def group_table(ev, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for name, tag, val, g in groups(ev):
        for h in HORIZONS:
            gh = in_years(g, h)
            for m in MODES:
                rows.append({"group": name, "tag": tag, "value": val, "horizon": h, "survivorship": m,
                             **stats(gh, h, m, rng)})
    return pd.DataFrame(rows)


def by_year(g, h="3y", m=MAIN):
    gh = in_years(g, h)
    x = gh[f"xs_ctrl_{h}_{m}"]
    y = pd.to_datetime(gh.entry_date).dt.year
    return pd.DataFrame({"year": y, "xs": x}).dropna().groupby("year").xs.agg(["count", "median"])


def verdict(g, rng, h="3y", m=MAIN):
    """Section 7. Returns (verdict, reasons dict)."""
    s = stats(in_years(g, h), h, m, rng)
    yrs = by_year(g, h, m)
    yrs = yrs[yrs["count"] >= MIN_YEAR_EVENTS]
    yr_share = (yrs["median"] > 0).mean() if len(yrs) else np.nan
    halves = [stats(in_years(g, h, yr), h, m, rng)["median_xs_control"] for yr in ((2009, 2016), (2017, 2021))]
    checks = {
        "n_3y": s["count"],
        "median_xs_3y": s["median_xs_control"], "ci_lo": s["ci_lo"], "ci_hi": s["ci_hi"],
        "ok_count": s["count"] >= MIN_EVENTS,
        "ok_median_ci": bool(s["median_xs_control"] > 0 and s["ci_lo"] > 0),
        "years_tested": len(yrs), "share_years_beating": yr_share,
        "ok_years": bool(yr_share >= 2 / 3) if len(yrs) else False,
        "half1_median": halves[0], "half2_median": halves[1],
        "ok_halves": bool(halves[0] > 0 and halves[1] > 0),
    }
    if all(checks[k] for k in ("ok_count", "ok_median_ci", "ok_years", "ok_halves")):
        v = "PASS"
    elif s["count"] and s["median_xs_control"] > 0:
        v = "MIXED"
    else:
        v = "FAIL"
    return v, checks


def verdict_table(ev, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for name, tag, val, g in groups(ev):
        v, c = verdict(g, rng)
        rows.append({"group": name, "tag": tag, "value": val, "verdict": v, **c})
    return pd.DataFrame(rows)


def year_table(ev):
    rows = []
    for name, tag, val, g in groups(ev):
        for h in HORIZONS:
            gh = in_years(g, h)
            y = pd.to_datetime(gh.entry_date).dt.year
            for yr, gy in gh.groupby(y):
                x = (gy[f"ret_{h}_{MAIN}"] - gy[f"ctrl_{h}_{MAIN}"]).dropna()
                if len(x):
                    rows.append({"group": name, "horizon": h, "year": int(yr), "count": len(x),
                                 "median_xs_control": x.median(), "pct_beat_control": (x > 0).mean()})
    return pd.DataFrame(rows)


def window_table(ev, label, years, horizons=HORIZONS, seed=2):
    rng = np.random.default_rng(seed)
    rows = []
    for name, tag, val, g in groups(ev):
        for h in horizons:
            gh = in_years(g, h, years)
            s = stats(gh, h, MAIN, rng)
            rows.append({"check": label, "group": name, "horizon": h, **s})
    return pd.DataFrame(rows)


def threshold_table(ev, ev_completion):
    """Moves each cut one step either way. A family that passes at only one of its three cuts is flagged."""
    rng = np.random.default_rng(3)
    ins = ev[ev.kind == "insider"]
    fams = [
        ("Cluster: >= k insiders (entry at first filing)", [2, 3, 4], lambda k: ins[ins.n_insiders >= k]),
        ("Cluster: >= k insiders (entry once k-th insider filed)", [2, 3, 4],
         lambda k: ev_completion[k][ev_completion[k].n_insiders >= k] if k in ev_completion else ins.iloc[0:0]),
        ("Size $: top bucket > cut", [5e5, 1e6, 2e6], lambda c: ins[ins.dollars > c]),
        ("Size $: bottom bucket < cut", [5e4, 1e5, 2.5e5], lambda c: ins[ins.dollars < c]),
        ("Stake: top bucket > cut", [0.25, 0.50, 0.75], lambda c: ins[ins.stake > c]),
        ("Stake: bottom bucket < cut", [0.05, 0.10, 0.20], lambda c: ins[ins.stake < c]),
    ]
    rows = []
    for fam, cuts, sel in fams:
        res = []
        for c in cuts:
            v, ch = verdict(sel(c), rng)
            res.append(v)
            rows.append({"family": fam, "cut": c, "verdict": v, "n_3y": ch["n_3y"], "median_xs_3y": ch["median_xs_3y"],
                         "ci_lo": ch["ci_lo"], "ci_hi": ch["ci_hi"]})
        flag = "cherry-picked (passes at one cut only)" if res.count("PASS") == 1 else (
            "robust (passes at all cuts)" if res.count("PASS") == 3 else
            "passes at two of three cuts" if res.count("PASS") == 2 else "passes at no cut")
        for r in rows[-len(cuts):]:
            r["family_flag"] = flag
    return pd.DataFrame(rows)
