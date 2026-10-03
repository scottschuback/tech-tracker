"""SEC EDGAR: new filings per company, plus the numbers from each company's own XBRL data."""
import re, html, datetime as dt
from ..http import get

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

def resolve(tickers):
    data = get(TICKERS_URL)
    m = {v["ticker"].upper(): (int(v["cik_str"]), v["title"]) for v in data.values()}
    found, missing = {}, []
    for t in tickers:
        if t.upper() in m: found[t] = m[t.upper()]
        else: missing.append(t)
    return found, missing

def recent_filings(cik, since):
    """Filings on or after `since` (YYYY-MM-DD) from the company's submissions feed."""
    j = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    r = j["filings"]["recent"]
    out = []
    for i, form in enumerate(r["form"]):
        if r["filingDate"][i] < since: continue
        accn = r["accessionNumber"][i]
        doc = r["primaryDocument"][i]
        out.append({
            "form": form, "filed": r["filingDate"][i], "accn": accn,
            "items": [x.strip() for x in (r.get("items", [""] * len(r["form"]))[i] or "").split(",") if x.strip()],
            "desc": r.get("primaryDocDescription", [""] * len(r["form"]))[i],
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{doc}",
        })
    return out

def company_facts(cik):
    return get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")

def doc_text(url):
    raw = get(url, as_json=False, timeout=60)
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t))

def full_text_search(phrase, since, forms=None, ciks=None):
    """EDGAR full-text search. Returns hits filed since `since`."""
    params = {"q": f'"{phrase}"', "dateRange": "custom", "startdt": since,
              "enddt": dt.date.today().isoformat()}
    if forms: params["forms"] = ",".join(forms)
    if ciks: params["ciks"] = ",".join(f"{c:010d}" for c in ciks)
    j = get("https://efts.sec.gov/LATEST/search-index", params=params)
    hits = []
    for h in j.get("hits", {}).get("hits", [])[:50]:
        s = h["_source"]
        accn = h["_id"].split(":")[0]
        cik = (s.get("ciks") or ["0"])[0].lstrip("0")
        fname = h["_id"].split(":")[1] if ":" in h["_id"] else ""
        hits.append({"accn": accn, "form": s.get("form"), "filed": s.get("file_date"),
                     "names": s.get("display_names", []), "cik": cik,
                     "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{fname}"})
    return hits
