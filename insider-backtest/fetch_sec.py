"""Stage 1: fetch and cache SEC data.

* Form 3/4/5 bulk quarterly files, 2006 Q1 -> latest  -> state/trades.parquet, state/owners.parquet
* XBRL companyfacts (bulk zip of the same JSON the companyfacts API serves) -> state/facts.parquet
* Submissions (bulk zip) for SIC codes and current tickers -> state/companies.parquet
* company_tickers.json -> state/tickers.parquet

Every download lands in cache/ and is never fetched twice. Each output is skipped if it already exists,
so a stopped run resumes where it left off.
"""
import datetime as dt, io, json, sys, zipfile
from pathlib import Path

import pandas as pd

from common import CACHE, STATE, download, log, sec_get

F345_URLS = [
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{q}_form345.zip",
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip",
]
FACTS_ZIP = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SUBS_ZIP = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
TICKERS = "https://www.sec.gov/files/company_tickers.json"

# XBRL concepts read from each 10-K. Fallbacks are tried in order by universe.py.
FLOW = [
    "NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    "InterestExpense",
]
INSTANT = [
    "LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtCurrent", "DebtCurrent",
    "LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "ShortTermBorrowings", "CommercialPaper",
    "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash",
]
DEI = ["EntityCommonStockSharesOutstanding"]
ANNUAL_FORMS = {"10-K", "10-K405", "10-KT"}


def quarters(start=(2006, 1), today=None):
    today = today or dt.date.today()
    y, q = start
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        yield f"{y}q{q}"
        q += 1
        if q == 5:
            y, q = y + 1, 1


def fetch_form345():
    d = CACHE / "form345"
    d.mkdir(exist_ok=True)
    got = []
    for q in quarters():
        dest = d / f"{q}_form345.zip"
        path = None
        for u in F345_URLS:
            path = download(u.format(q=q), dest, ok404=True)
            if path:
                break
        if path:
            got.append(path)
            log("form345", q, "ok")
        else:
            log("form345", q, "not published (404) - skipped")
    return got


def _read(z, name):
    member = next(n for n in z.namelist() if n.upper().endswith(name + ".TSV"))
    with z.open(member) as f:
        return pd.read_csv(f, sep="\t", dtype=str, keep_default_na=False, quoting=3, on_bad_lines="warn")


def parse_date(s):
    out = pd.to_datetime(s, format="%d-%b-%Y", errors="coerce")
    miss = out.isna() & (s.str.len() > 0)
    if miss.any():
        out[miss] = pd.to_datetime(s[miss], errors="coerce")
    return out


def num(s):
    return pd.to_numeric(s.str.replace(",", ""), errors="coerce")


def parse_form345(paths):
    out_t, out_o = STATE / "trades.parquet", STATE / "owners.parquet"
    if out_t.exists() and out_o.exists():
        log("trades/owners already parsed")
        return
    trades, owners = [], []
    for p in paths:
        with zipfile.ZipFile(p) as z:
            sub = _read(z, "SUBMISSION")
            own = _read(z, "REPORTINGOWNER")
            ndt = _read(z, "NONDERIV_TRANS")
        sub = sub[sub.DOCUMENT_TYPE.isin(["4", "5"])]
        ndt = ndt[ndt.TRANS_CODE.isin(["P", "S"])]
        t = ndt.merge(sub[["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME",
                           "ISSUERTRADINGSYMBOL"]], on="ACCESSION_NUMBER")
        t = pd.DataFrame({
            "accession": t.ACCESSION_NUMBER,
            "filing_date": parse_date(t.FILING_DATE),
            "doc_type": t.DOCUMENT_TYPE,
            "issuer_cik": num(t.ISSUERCIK).astype("Int64"),
            "issuer_name": t.ISSUERNAME,
            "issuer_symbol": t.ISSUERTRADINGSYMBOL.str.strip().str.upper(),
            "security_title": t.SECURITY_TITLE,
            "trans_date": parse_date(t.TRANS_DATE),
            "trans_code": t.TRANS_CODE,
            "acq_disp": t.TRANS_ACQUIRED_DISP_CD,
            "shares": num(t.TRANS_SHARES),
            "price": num(t.TRANS_PRICEPERSHARE),
            "shares_after": num(t.SHRS_OWND_FOLWNG_TRANS),
            "direct": t.DIRECT_INDIRECT_OWNERSHIP,
        })
        o = own[own.ACCESSION_NUMBER.isin(set(t.accession))]
        rel = o.RPTOWNER_RELATIONSHIP.str.lower()
        o = pd.DataFrame({
            "accession": o.ACCESSION_NUMBER,
            "owner_cik": num(o.RPTOWNERCIK).astype("Int64"),
            "owner_name": o.RPTOWNERNAME,
            "is_director": rel.str.contains("director"),
            "is_officer": rel.str.contains("officer"),
            "is_ten_pct": rel.str.contains("ten") | rel.str.contains("10"),
            "is_other": rel.str.contains("other"),
            "officer_title": o.RPTOWNER_TITLE,
        })
        trades.append(t)
        owners.append(o)
        log("parsed", p.name, len(t), "P/S rows")
    pd.concat(trades, ignore_index=True).to_parquet(out_t)
    pd.concat(owners, ignore_index=True).drop_duplicates().to_parquet(out_o)


def _annual_rows(cik, js):
    rows = []
    facts = js.get("facts", {})
    want = [("us-gaap", c, "USD") for c in FLOW + INSTANT] + [("dei", c, "shares") for c in DEI]
    for tax, concept, unit in want:
        units = facts.get(tax, {}).get(concept, {}).get("units", {})
        for e in units.get(unit, []):
            if e.get("form") not in ANNUAL_FORMS:
                continue
            rows.append((cik, concept, e.get("start"), e["end"], float(e["val"]), e["accn"], e["filed"], e["form"]))
    return rows


def fetch_facts():
    out = STATE / "facts.parquet"
    if out.exists():
        log("facts already parsed")
        return
    zp = download(FACTS_ZIP, CACHE / "companyfacts.zip")
    rows = []
    with zipfile.ZipFile(zp) as z:
        names = [n for n in z.namelist() if n.endswith(".json")]
        for i, n in enumerate(names):
            js = json.loads(z.read(n))
            cik = int(js.get("cik") or Path(n).stem.replace("CIK", ""))
            rows.extend(_annual_rows(cik, js))
            if i % 2000 == 0:
                log(f"companyfacts {i}/{len(names)}")
    df = pd.DataFrame(rows, columns=["cik", "concept", "start", "end", "val", "accn", "filed", "form"])
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df.to_parquet(out)
    log("facts rows", len(df))


def fetch_companies():
    out = STATE / "companies.parquet"
    if out.exists():
        log("companies already parsed")
        return
    zp = download(SUBS_ZIP, CACHE / "submissions.zip")
    rows = []
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            if "-submissions-" in n or not n.endswith(".json"):
                continue
            js = json.loads(z.read(n))
            sic = js.get("sic")
            rows.append({
                "cik": int(js.get("cik") or Path(n).stem.replace("CIK", "")),
                "name": js.get("name"),
                "sic": int(sic) if str(sic or "").isdigit() else None,
                "tickers": ",".join(js.get("tickers") or []),
                "entity_type": js.get("entityType"),
            })
    pd.DataFrame(rows).to_parquet(out)
    log("companies", len(rows))


def fetch_tickers():
    out = STATE / "tickers.parquet"
    if out.exists():
        return
    js = sec_get(TICKERS).json()
    df = pd.DataFrame(js.values()).rename(columns={"cik_str": "cik"})
    df["ticker"] = df.ticker.str.upper()
    df[["cik", "ticker", "title"]].to_parquet(out)


def main():
    fetch_tickers()
    parse_form345(fetch_form345())
    fetch_facts()
    fetch_companies()
    log("fetch_sec done")


if __name__ == "__main__":
    sys.exit(main())
