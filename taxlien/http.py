import time
import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def get(s: requests.Session, url: str, retries: int = 3, **kw) -> requests.Response:
    last = None
    for i in range(retries):
        try:
            r = s.get(url, timeout=kw.pop("timeout", 30), **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # noqa: PERF203
            last = e
            time.sleep(2 * (i + 1))
    raise last


def post(s: requests.Session, url: str, retries: int = 3, **kw) -> requests.Response:
    last = None
    for i in range(retries):
        try:
            r = s.post(url, timeout=kw.pop("timeout", 30), **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # noqa: PERF203
            last = e
            time.sleep(2 * (i + 1))
    raise last
