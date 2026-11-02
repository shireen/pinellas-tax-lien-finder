"""Upcoming Pinellas County tax deed auctions from RealTaxDeed (the Clerk's auction vendor)."""
import datetime as dt
import json
import re

from bs4 import BeautifulSoup

from .http import get, session

BASE = "https://pinellas.realtaxdeed.com"

_CODES = [
    ("@A", '<div class="'), ("@B", "</div>"), ("@C", 'class="'), ("@D", "<div>"),
    ("@E", "AUCTION"), ("@F", "</td><td"), ("@G", "</td></tr>"), ("@H", "<tr><td "),
    ("@I", "table"), ("@J", 'p_back="NextCheck='), ("@K", 'style="Display:none"'),
    ("@L", "/index.cfm?zaction=auction&zmethod=details&AID="),
]


def _decode(html: str) -> str:
    for code, rep in _CODES:
        html = html.replace(code, rep)
    return html


def preview_url(date: str) -> str:
    return f"{BASE}/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE={date}"


def auction_dates(s, months_ahead: int = 3) -> list[str]:
    """Return MM/DD/YYYY dates with tax deed (TD) auctions from today through N months ahead."""
    today = dt.date.today()
    dates = set()
    for i in range(months_ahead + 1):
        y, m = divmod(today.month - 1 + i, 12)
        first = dt.date(today.year + y, m + 1, 1)
        ts = f"%7Bts%20%27{first:%Y-%m-%d}%2000%3A00%3A00%27%7D"
        html = get(s, f"{BASE}/index.cfm?zaction=user&zmethod=calendar&selCalDate={ts}").text
        soup = BeautifulSoup(html, "html.parser")
        for box in soup.select(".CALBOX"):
            day = box.get("dayid")
            if day and re.search(r"\bTD\b|Tax Deed", box.get_text(" ")):
                d = dt.datetime.strptime(day, "%m/%d/%Y").date()
                if d >= today:
                    dates.add(day)
    return sorted(dates, key=lambda d: dt.datetime.strptime(d, "%m/%d/%Y"))


def _parse_items(html: str, auction_date: str) -> list[dict]:
    soup = BeautifulSoup(_decode(html), "html.parser")
    items = []
    for div in soup.select("div.AUCTION_ITEM"):
        rows = {}
        last_label = None
        for tr in div.select("table.ad_tab tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            label = tds[0].get_text(strip=True).rstrip(":")
            val = tds[1].get_text(" ", strip=True)
            if label:
                rows[label] = val
                last_label = label
            elif last_label == "Property Address":
                rows["City Line"] = val
        status = div.select_one(".ASTAT_MSGB")
        money = lambda v: float(re.sub(r"[^\d.]", "", v)) if v and re.search(r"\d", v) else None  # noqa: E731
        city_line = rows.get("City Line", "")
        m = re.match(r"(.*?),\s*FL-?\s*(\d{5})?", city_line)
        items.append({
            "aid": div.get("aid"),
            "case": rows.get("Case #", "").strip(),
            "auction_date": auction_date,
            "opening_bid": money(rows.get("Opening Bid")),
            "parcel_id": rows.get("Parcel ID", "").strip(),
            "address": rows.get("Property Address", "").strip(),
            "city": (m.group(1).strip() if m else city_line).title(),
            "zip": (m.group(2) if m else "") or "",
            "assessed": money(rows.get("Assessed Value")),
            "status": (status.get_text(strip=True) if status else "") or "Scheduled",
        })
    return items


def _load_area(s, area: str, page_dir: int, bypass: int) -> list:
    url = (f"{BASE}/index.cfm?zaction=AUCTION&Zmethod=UPDATE&FNC=LOAD&AREA={area}"
           f"&PageDir={page_dir}&doR=1&bypassPage={bypass}&test=1")
    txt = get(s, url).text
    return json.loads(txt[txt.index("{"):])["retHTML"]


def auction_items(s, date: str) -> tuple[list[dict], list[dict]]:
    """Return (active, closed_or_canceled) items for one auction date."""
    get(s, preview_url(date))  # sets the auction date in the session
    active, seen = [], set()
    for page in range(15):
        html = _load_area(s, "W", 0 if page == 0 else 1, 1 if page == 0 else 0)
        new = [i for i in _parse_items(html, date) if i["aid"] not in seen]
        if not new:
            break
        seen.update(i["aid"] for i in new)
        active += new
    closed = _parse_items(_load_area(s, "C", 0, 0), date)
    return active, closed


def upcoming(months_ahead: int = 3) -> dict:
    s = session()
    out = {"dates": [], "active": [], "closed": []}
    for d in auction_dates(s, months_ahead):
        a, c = auction_items(s, d)
        out["dates"].append({"date": d, "active": len(a), "closed": len(c)})
        out["active"] += a
        out["closed"] += c
    return out
