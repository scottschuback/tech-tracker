"""Stage 2: the point-in-time universe.

One row per company per 10-K. A company is IN from that 10-K's `filed` date until the next 10-K's filed date
(capped at 400 days if no later 10-K was filed), when all of these hold:
  1. GAAP net income > 0
  2. operating cash flow - capex > 0
  3. return on capital >= 15%: operating income x (1 - 21%) / (debt + equity - cash), average capital when
     both year-ends are in the filing
  4. 10-K filer, SIC outside 6000-6799, market cap >= $2bn at the 10-K filed date (shares on the cover x close)

Only values tagged in that 10-K are used, so nothing filed later can leak in.
Rules 1-4 except the market cap are built by `build_financials`; `apply_market_cap` needs prices (prices.py).
"""
import sys

import numpy as np
import pandas as pd

from common import STATE, log

TAX = 0.21
MIN_ROC = 0.15
MIN_CAP = 2e9
MAX_GAP_DAYS = 400
FIRST_FY = 2008

NI = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
OCF = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
OPINC = ["OperatingIncomeLoss"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
EQUITY = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"]


def fiscal_year(period_end):
    """Fiscal years ending Jan-May belong to the prior calendar year (retail convention)."""
    return period_end.year if period_end.month >= 6 else period_end.year - 1


def _first(d, names):
    for n in names:
        if n in d and d[n] is not None and not np.isnan(d[n]):
            return d[n], n
    return None, None


def total_debt(d):
    """Debt from the tags a 10-K uses, without counting the same borrowings twice."""
    lt = d.get("LongTermDebt")
    cur_from_debtcurrent = False
    if lt is None:
        nonc = d.get("LongTermDebtNoncurrent")
        if nonc is None:
            nonc = d.get("LongTermDebtAndCapitalLeaseObligations")
        cur = d.get("LongTermDebtCurrent")
        if cur is None:
            cur = d.get("LongTermDebtAndCapitalLeaseObligationsCurrent")
        if cur is None and d.get("DebtCurrent") is not None:
            cur, cur_from_debtcurrent = d["DebtCurrent"], True
        lt = (nonc or 0.0) + (cur or 0.0)
    short = 0.0
    if not cur_from_debtcurrent:  # DebtCurrent already includes short-term borrowings
        short = d.get("ShortTermBorrowings")
        if short is None:
            short = d.get("CommercialPaper") or 0.0
    return lt + short


def one_filing(rows):
    """Values for the fiscal year a single 10-K reports. rows = facts with this accession only."""
    flows = rows[rows.start.notna()]
    dur = (flows.end - flows.start).dt.days
    flows = flows[(dur >= 330) & (dur <= 400)]
    if flows.empty:
        return None
    period_end = flows.end.max()
    cur = flows[flows.end == period_end].groupby("concept").val.first().to_dict()
    inst = rows[rows.start.isna()]
    now = inst[inst.end == period_end].groupby("concept").val.first().to_dict()
    prior_ends = inst.end[(inst.end < period_end - pd.Timedelta(days=300)) & (inst.end > period_end - pd.Timedelta(days=430))]
    prior = inst[inst.end == prior_ends.max()].groupby("concept").val.first().to_dict() if len(prior_ends) else {}

    ni, ni_src = _first(cur, NI)
    ocf, _ = _first(cur, OCF)
    capex, capex_src = _first(cur, CAPEX)
    op, op_src = _first(cur, OPINC)
    if op is None:  # fallback: pre-tax income + interest expense, flagged
        pt, _ = _first(cur, PRETAX)
        if pt is not None:
            op, op_src = pt + (cur.get("InterestExpense") or 0.0), "pretax+interest"

    def capital(d):
        eq, _ = _first(d, EQUITY)
        if eq is None:
            return None
        cash, _ = _first(d, CASH)
        return total_debt(d) + eq - (cash or 0.0)

    cap_now, cap_prior = capital(now), capital(prior) if prior else None
    cap = (cap_now + cap_prior) / 2 if cap_now is not None and cap_prior is not None else cap_now

    sh = rows[(rows.concept == "EntityCommonStockSharesOutstanding")]
    shares = None
    if not sh.empty:
        last = sh[sh.end == sh.end.max()]
        shares = float(last.val.drop_duplicates().sum())  # several classes are summed

    return {
        "period_end": period_end, "ni": ni, "ni_src": ni_src, "ocf": ocf,
        "capex": capex if capex is not None else (0.0 if ocf is not None else None),
        "capex_missing": capex is None, "opinc": op, "opinc_src": op_src,
        "capital": cap, "capital_avg": cap_prior is not None and cap_now is not None, "shares": shares,
    }


def build_financials(facts=None, companies=None):
    out = STATE / "universe_fin.parquet"
    save = facts is None
    if save and out.exists():
        log("universe financials already built")
        return pd.read_parquet(out)
    facts = facts if facts is not None else pd.read_parquet(STATE / "facts.parquet")
    companies = companies if companies is not None else pd.read_parquet(STATE / "companies.parquet")
    facts = facts[facts.form.isin(["10-K", "10-K405", "10-KT"])]
    recs = []
    for (cik, accn), rows in facts.groupby(["cik", "accn"], sort=False):
        v = one_filing(rows)
        if v is None:
            continue
        v.update(cik=int(cik), accn=accn, filed=rows.filed.min())
        recs.append(v)
    u = pd.DataFrame(recs)
    if u.empty:
        return u
    u["fy"] = u.period_end.map(fiscal_year)
    # one 10-K per company per fiscal year: the first one filed (later ones are amendments or re-filings)
    u = u.sort_values(["cik", "fy", "filed"]).drop_duplicates(["cik", "fy"], keep="first")
    u = u[u.fy >= FIRST_FY].sort_values(["cik", "filed"])
    nxt = u.groupby("cik").filed.shift(-1)
    u["valid_from"] = u.filed
    cap = u.filed + pd.Timedelta(days=MAX_GAP_DAYS)
    u["valid_to"] = nxt.where(nxt.notna() & (nxt < cap), cap)

    u["nopat"] = u.opinc * (1 - TAX)
    u["roc"] = np.where(u.capital > 0, u.nopat / u.capital, np.nan)
    u["fcf"] = u.ocf - u.capex
    u = u.merge(companies[["cik", "sic", "name"]], on="cik", how="left")
    u["pass_profit"] = u.ni > 0
    u["pass_fcf"] = u.fcf > 0
    u["pass_roc"] = u.roc >= MIN_ROC
    u["roc_undefined"] = u.capital.notna() & (u.capital <= 0)
    u["sic_ok"] = ~u.sic.between(6000, 6799)
    u["pass_fin"] = u.pass_profit & u.pass_fcf & u.pass_roc & u.sic_ok
    if save:
        u.to_parquet(out)
    log("10-K rows", len(u), "pass rules 1-3 and SIC:", int(u.pass_fin.sum()))
    return u


def apply_market_cap(u, raw_close_on):
    """raw_close_on(cik, date) -> unadjusted close on the last trading day <= date, or None."""
    caps = []
    for r in u.itertuples():
        if not r.pass_fin or not r.shares:
            caps.append(np.nan)
            continue
        px = raw_close_on(r.cik, r.filed)
        caps.append(r.shares * px if px else np.nan)
    u = u.copy()
    u["mktcap_at_filing"] = caps
    u["price_missing"] = u.pass_fin & u.shares.notna() & u.mktcap_at_filing.isna()
    u["in_universe"] = u.pass_fin & (u.mktcap_at_filing >= MIN_CAP)
    return u


class Universe:
    """Fast point-in-time membership lookups."""
    def __init__(self, u):
        self.u = u[u.in_universe].copy()
        self.by_cik = {c: g[["valid_from", "valid_to", "accn", "shares"]].to_numpy()
                       for c, g in self.u.groupby("cik")}

    def row(self, cik, date):
        for vf, vt, accn, shares in self.by_cik.get(cik, ()):
            if vf <= date < vt:
                return accn, shares
        return None

    def contains(self, cik, date):
        return self.row(cik, date) is not None

    def members(self, date):
        m = self.u[(self.u.valid_from <= date) & (self.u.valid_to > date)]
        return m[["cik", "shares"]].drop_duplicates("cik")


if __name__ == "__main__":
    build_financials()
    sys.exit(0)
