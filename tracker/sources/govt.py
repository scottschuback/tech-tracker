"""Government sources: export-control rules (Federal Register / Commerce BIS), federal chip money (USAspending),
and patents (PatentsView, needs a free key). Each returns a list of items; failures are reported, never hidden."""
import os, datetime as dt
from ..http import get

FR = "https://www.federalregister.gov/api/v1/documents.json"
FR_TERMS = ["semiconductor", "advanced computing", "integrated circuit", "Entity List", "high bandwidth memory",
            "data center", "export administration regulations"]

def federal_register(since):
    seen, out = set(), []
    for term in FR_TERMS:
        params = {"conditions[term]": term, "conditions[publication_date][gte]": since,
                  "per_page": 50, "order": "newest",
                  "fields[]": ["title", "html_url", "publication_date", "type", "agencies", "abstract", "document_number"]}
        j = get(FR, params=params)
        for d in j.get("results", []):
            if d["document_number"] in seen: continue
            seen.add(d["document_number"])
            agencies = ", ".join(a.get("name", "") for a in d.get("agencies", []) if a)
            bis = "Industry and Security" in agencies
            out.append({"id": "fr:" + d["document_number"], "title": d["title"], "url": d["html_url"],
                        "published": d["publication_date"], "kind": d.get("type", "Notice"),
                        "colour": "red" if (bis and d.get("type") == "Rule") else ("amber" if bis else "green"),
                        "reason": f"{agencies}: {d.get('type','')}. Matched '{term}'",
                        "extra": {"abstract": (d.get("abstract") or "")[:600]}})
    return out

USA = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
USA_WORDS = ["semiconductor", "advanced packaging", "high bandwidth memory", "photonics", "CHIPS"]

def usaspending(since):
    out = []
    groups = {"contract": ["A", "B", "C", "D"], "grant": ["02", "03", "04", "05"]}
    for gname, codes in groups.items():
        body = {"filters": {"keywords": USA_WORDS, "award_type_codes": codes,
                            "time_period": [{"start_date": since, "end_date": dt.date.today().isoformat()}]},
                "fields": ["Award ID", "Recipient Name", "Award Amount", "Start Date", "Awarding Agency", "generated_internal_id"],
                "limit": 50, "sort": "Award Amount", "order": "desc"}
        j = get(USA, method="POST", body=body)
        for a in j.get("results", []):
            amt = a.get("Award Amount") or 0
            if amt < 5_000_000: continue
            out.append({"id": "usa:" + str(a.get("generated_internal_id")),
                        "title": f"{a.get('Recipient Name')}: ${amt/1e6:,.0f}M {gname} from {a.get('Awarding Agency')}",
                        "url": f"https://www.usaspending.gov/award/{a.get('generated_internal_id')}",
                        "published": a.get("Start Date"), "kind": f"Federal {gname}",
                        "colour": "amber" if amt >= 100_000_000 else "green",
                        "reason": "Federal money into chips, packaging or photonics", "extra": {"amount": amt}})
    return out

# Patent classification groups that matter for this stack
CPC = {"G06N3/063": "AI hardware", "H01L25": "Chip packaging/stacking", "H10B": "Memory",
       "G02B6/42": "Optics coupled to chips", "H02M": "Power conversion"}
PV = "https://search.patentsview.org/api/v1/patent/"

def patents(since, assignees):
    key = os.environ.get("PATENTSVIEW_API_KEY")
    if not key:
        raise RuntimeError("no PATENTSVIEW_API_KEY set (free key: see SETUP.md step 5)")
    out = []
    for cpc, label in CPC.items():
        q = {"_and": [{"_gte": {"patent_date": since}},
                      {"_begins": {"cpc_current.cpc_group_id": cpc}},
                      {"_or": [{"_contains": {"assignees.assignee_organization": a}} for a in assignees]}]}
        j = get(PV, params={"q": str(q).replace("'", '"'),
                            "f": '["patent_id","patent_title","patent_date","assignees.assignee_organization"]',
                            "o": '{"size":100}'},
                headers={"X-Api-Key": key})
        for p in j.get("patents", []) or []:
            org = ", ".join(a.get("assignee_organization") or "" for a in p.get("assignees", []))
            out.append({"id": "pat:" + p["patent_id"], "title": f"{org}: {p['patent_title']}",
                        "url": f"https://patents.google.com/patent/US{p['patent_id']}",
                        "published": p["patent_date"], "kind": f"Patent ({label})",
                        "colour": "green", "reason": f"New grant in {label} ({cpc})", "extra": {"cpc": cpc, "org": org}})
    return out
