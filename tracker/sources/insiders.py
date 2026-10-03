"""Insider purchases from Form 4 filings (transaction code P = open-market buy). Sales are ignored."""
import re
from ..http import get

def raw_xml_url(url):
    return url.replace("/xslF345X06/", "/").replace("/xslF345X05/", "/")

def parse_form4(url):
    """Returns list of buys: {owner, shares, price, value}."""
    try:
        x = get(raw_xml_url(url), as_json=False)
    except Exception:
        return []
    owners = re.findall(r"<rptOwnerName>(.*?)</rptOwnerName>", x)
    owner = owners[0].strip() if owners else "insider"
    buys = []
    for tx in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", x, re.S):
        code = re.search(r"<transactionCode>(\w)</transactionCode>", tx)
        ad = re.search(r"<transactionAcquiredDisposedCode>\s*<value>(\w)</value>", tx)
        sh = re.search(r"<transactionShares>\s*<value>([\d.]+)</value>", tx)
        pr = re.search(r"<transactionPricePerShare>\s*<value>([\d.]+)</value>", tx)
        if code and code.group(1) == "P" and ad and ad.group(1) == "A" and sh:
            shares = float(sh.group(1)); price = float(pr.group(1)) if pr else 0.0
            buys.append({"owner": owner, "shares": shares, "price": price, "value": shares * price})
    return buys
