"""Polite HTTP: identifies itself, retries, and rate-limits so government sites never block us."""
import os, time, requests

UA = os.environ.get("TRACKER_USER_AGENT", "TechTracker research contact@example.com")
_last = {}
MIN_GAP = {"sec.gov": 0.15}  # SEC asks for no more than 10 requests a second

def get(url, params=None, headers=None, timeout=30, tries=3, as_json=True, method="GET", body=None):
    host = url.split("/")[2]
    gap = next((v for k, v in MIN_GAP.items() if host.endswith(k)), 0.05)
    h = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
    if headers: h.update(headers)
    err = None
    for i in range(tries):
        wait = gap - (time.time() - _last.get(host, 0))
        if wait > 0: time.sleep(wait)
        _last[host] = time.time()
        try:
            if method == "POST":
                r = requests.post(url, params=params, headers=h, json=body, timeout=timeout)
            else:
                r = requests.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                err = f"HTTP {r.status_code}"; time.sleep(2 * (i + 1)); continue
            r.raise_for_status()
            return r.json() if as_json else r.text
        except Exception as e:  # network or parse error: retry, then report
            err = str(e)[:200]; time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url.split('?')[0]}: {err}")
