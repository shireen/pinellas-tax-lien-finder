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


def _request(method, url, retries, **kw):
    if retries < 1:
        raise ValueError("retries must be at least 1")
    timeout = kw.pop("timeout", 30)
    for i in range(retries):
        try:
            r = method(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as error:
            status = getattr(error.response, "status_code", None)
            # Do not retry permission denials, validation errors, or missing endpoints.
            if status is not None and status not in (408, 429, 500, 502, 503, 504):
                raise
            if i + 1 == retries:
                raise
            delay = 2 * (i + 1)
            if status == 429:
                retry_after = error.response.headers.get("Retry-After", "")
                # If the service asks for an unbounded/date delay, defer to a later run.
                if retry_after and (not retry_after.isdigit() or int(retry_after) > 60):
                    raise
                if retry_after:
                    delay = max(delay, int(retry_after))
            time.sleep(delay)


def get(s: requests.Session, url: str, retries: int = 3, **kw) -> requests.Response:
    return _request(s.get, url, retries, **kw)


def post(s: requests.Session, url: str, retries: int = 3, **kw) -> requests.Response:
    return _request(s.post, url, retries, **kw)
