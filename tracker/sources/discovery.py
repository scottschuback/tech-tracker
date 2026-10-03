"""Discovery: companies NOT on the watchlist whose filings start mentioning the AI stack. Catches new listings in
AI hardware and small companies naming the big buyers or key technologies. Uses EDGAR full-text search."""
from .sec import full_text_search

PHRASES = ["high bandwidth memory", "co-packaged optics", "AI accelerator", "liquid cooling", "advanced packaging",
           "800 volt", "data center GPUs", "GPU cluster", "AI data center"]
SKIP = ["acquisition corp", "therapeutics", "pharma", "biosciences", "bancorp", "bank", "capital corp", "trust"]
NEW_LISTING_FORMS = ["S-1", "F-1", "424B4"]

def scan(since, watch_ciks):
    out, seen = [], set()
    for ph in PHRASES:
        for forms, kind, colour in ((NEW_LISTING_FORMS, "New listing in the AI stack", "amber"),
                                    (["10-Q", "10-K"], "New name mentioning the stack", "green")):
            try:
                hits = full_text_search(ph, since, forms)
            except Exception:
                continue
            for h in hits:
                try: cik = int(h["cik"] or 0)
                except ValueError: continue
                if cik in watch_ciks or not cik: continue
                key = (cik, kind)
                if key in seen: continue
                seen.add(key)
                name = ", ".join(h["names"])[:80]
                if any(w in name.lower() for w in SKIP): continue
                out.append({"id": f"dx:{h['accn']}:{kind[:4]}", "title": f"{name}: '{ph}' in a {h['form']}", "url": h["url"],
                            "published": h["filed"], "kind": kind, "colour": colour,
                            "reason": "Not on the watchlist. Worth a look before adding; nothing is scored until it is added.",
                            "extra": {"cik": cik, "phrase": ph}})
    return out
