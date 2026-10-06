"""Shared paths, polite HTTP and small helpers for the insider backtest."""
import os, time, json, threading
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
STATE = ROOT / "state"
RESULTS = ROOT / "results"
for p in (CACHE, STATE, RESULTS):
    p.mkdir(exist_ok=True)

SEC_UA = os.environ.get("SEC_UA", "Scott Research scottschuback@gmail.com")
YAHOO_UA = "Mozilla/5.0 (compatible; Scott Research)"


class Throttle:
    """At most `per_sec` calls a second, shared by every caller."""
    def __init__(self, per_sec):
        self.gap = 1.0 / per_sec
        self.last = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            sleep = self.last + self.gap - now
            if sleep > 0:
                time.sleep(sleep)
            self.last = time.monotonic()


SEC_THROTTLE = Throttle(8)
YAHOO_THROTTLE = Throttle(2)
_session = requests.Session()


def http_get(url, throttle, headers, stream=False, tries=5, ok404=False):
    """GET with throttle and back-off. Returns the response, or None on 404 when ok404."""
    for i in range(tries):
        throttle.wait()
        try:
            r = _session.get(url, headers=headers, timeout=120, stream=stream)
        except requests.RequestException as e:
            err = e
        else:
            if r.status_code == 404 and ok404:
                return None
            if r.status_code == 200:
                return r
            err = RuntimeError(f"HTTP {r.status_code} for {url}")
            if r.status_code in (400, 401, 403, 404) and i >= 1:
                break
        time.sleep(2 ** (i + 1))
    raise err


def sec_get(url, **kw):
    return http_get(url, SEC_THROTTLE, {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}, **kw)


def download(url, dest, getter=sec_get, ok404=False):
    """Stream url to dest once. Never refetches a file already on disk."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = getter(url, stream=True, ok404=ok404)
    if r is None:
        return None
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def save_json(obj, path):
    Path(path).write_text(json.dumps(obj, indent=1, default=str))


def load_json(path):
    return json.loads(Path(path).read_text())
