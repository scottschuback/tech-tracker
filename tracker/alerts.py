"""Turns raw filings into coloured alerts using config/rules.yaml."""
import os, yaml
RULES = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "rules.yaml")))
RANK = {"red": 3, "amber": 2, "green": 1, "grey": 0}

def classify_filing(f):
    form = f["form"]
    base = RULES["forms"].get(form) or RULES["forms"].get(form.replace("/A", ""))
    if form.startswith("8-K"):
        best, why = ("green", "Current report"), []
        for it in f["items"]:
            r = RULES["eight_k_items"].get(it)
            if r:
                why.append(f"Item {it}: {r['why']}")
                if RANK[r["colour"]] > RANK[best[0]]: best = (r["colour"], r["why"])
        return best[0], "; ".join(why) or "Current report", ("8-K " + ",".join(f["items"])).strip()
    if base:
        colour = base["colour"]
        if form.endswith("/A") and form[:-2] in ("10-Q", "10-K", "20-F"):
            colour, why = "red", "Amended report: check what changed"
            return colour, why, form
        return colour, base["why"], form
    return "grey", f"{form} filing", form
