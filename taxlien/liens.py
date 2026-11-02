"""Lien / mortgage screen using Pinellas Clerk Official Records (Acclaim), searched by owner name.

This is an automated first pass, not a title search. It looks for:
  * government liens (city / county / code enforcement) - these can SURVIVE a Florida tax deed
  * State of Florida / Dept of Revenue liens - flagged for a title attorney to confirm
  * mortgages, lis pendens and private judgments - generally wiped out by the tax deed
"""
from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from .http import get, post, session

BASE = "https://officialrecords.mypinellasclerk.gov"
XHR = {"X-Requested-With": "XMLHttpRequest",
       "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

GOV_RE = re.compile(r"\b(ST PETERSBURG|CITY OF|PINELLAS PARK|PINELLAS COUNTY|^PINELLAS$|CODE ENFORCEMENT|"
                    r"CLEARWATER|LARGO|SEMINOLE|GULFPORT|DUNEDIN|TARPON|SOUTH PASADENA|KENNETH CITY|"
                    r"ENFORCEMENT BOARD|BOARD OF COUNTY)\b")
STATE_RE = re.compile(r"\b(FLA|FLORIDA|STATE OF FLORIDA|DEPARTMENT OF REVENUE|DEPT OF REVENUE)\b")
NOT_LIENHOLDER = re.compile(r"CLERK OF THE CIRCUIT COURT")


class Clerk:
    def __init__(self):
        self.s = session()
        self.ready = False

    def _accept(self):
        # Standard public-records disclaimer (no login, no fee).
        post(self.s, f"{BASE}/search/Disclaimer?st=/search/SearchTypeName", data={"disclaimer": "true"})
        get(self.s, f"{BASE}/search/SearchTypeName")
        self.ready = True

    def search_name(self, name: str) -> list[dict]:
        if not self.ready:
            self._accept()
        today = dt.date.today()
        to_date = f"{today.month}/{today.day}/{today.year}"
        body = {
            "IsParsedName": "False", "Both": "Both", "PartyType": "Both", "SearchOnName": name,
            "DateRangeList": " ", "DocTypes": "all", "DocTypesDisplay-input": "All", "DocTypesDisplay": "",
            "RecordDateFrom": "1/2/1957", "BookTypesDisplay": "All", "BookTypes": "All",
            "RecordDateTo": to_date, "X-Requested-With": "XMLHttpRequest",
        }
        html = post(self.s, f"{BASE}/search/SearchTypeName?Length=6", data=body, headers=XHR).text
        soup = BeautifulSoup(html, "html.parser")
        tree = soup.select_one("#NameListTreeView")
        if tree:
            pairs, values = [], []
            for inp in tree.select("input"):
                n, v = inp.get("name"), inp.get("value", "")
                if not n:
                    continue
                pairs.append((n, v))
                if n.endswith(".Value"):
                    values.append(v)
            data = [("NameList", "|||".join(values))] + pairs + [
                ("PartyType", "Both"), ("RecordDateFrom", "1/2/1957 12:00:00 AM"),
                ("RecordDateTo", f"{to_date} 12:00:00 AM"), ("BookTypes", "All"), ("DocTypes", "all"),
                ("SearchOnName", name), ("SearchOnLastOrBusinessName", ""), ("SearchOnFirstName", ""),
                ("X-Requested-With", "XMLHttpRequest")]
            post(self.s, f"{BASE}/Search/SearchTypePreName", data=data, headers=XHR)
        get(self.s, f"{BASE}/Search/PartialGrid")
        j = post(self.s, f"{BASE}/Search/GridResults", data={"page": 1, "size": 500}, headers=XHR).json()
        rows = []
        for x in j.get("data", []):
            ms = int(re.search(r"-?\d+", x["RecordDate"]).group())
            rows.append({
                "date": (dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=ms)).date().isoformat(),
                "party": x.get("Party"), "name": x.get("Name") or "", "cross": x.get("CrossPartyName") or "",
                "doc": (x.get("DocTypeDescription") or "").upper(), "legal": x.get("Comments") or "",
                "instrument": x.get("InstrumentNumber") or "",
            })
        return rows


def owner_search_name(owner1: str) -> str | None:
    """'SCHILLINGER, LISA' -> 'SCHILLINGER LISA'. Returns None for owners we can't search reliably."""
    if not owner1 or re.search(r"TRUST\s*#|^TRUST\b", owner1):
        return None
    name = re.sub(r"\b(EST|ESTATE|TRE|TR|ET AL|JR|SR|III|II)\b\.?", "", owner1)
    name = re.sub(r"[,.]", " ", name)
    words = name.split()
    # last name + first name is precise enough; companies keep up to 4 words
    return " ".join(words[:2] if "LLC" not in words and "INC" not in words else words[:4]) or None


def _legal_tokens(legal: str) -> list[str]:
    stop = {"LOT", "LOTS", "BLK", "BLOCK", "SUB", "SUBDIVISION", "OF", "THE", "AND", "&", "ETC", "DESC",
            "ADD", "ADDITION", "UNIT", "NO", "N", "S", "E", "W", "FT", "PART", "REPLAT", "SEC", "1ST", "2ND"}
    words = [w for w in re.findall(r"[A-Z]{3,}", (legal or "").upper()) if w not in stop]
    return words[:2]


def screen(rows: list[dict], legal: str) -> dict:
    """Classify records that plausibly relate to this parcel."""
    toks = _legal_tokens(legal)

    def relevant(r):
        lg = r["legal"].upper()
        return (not lg.strip()) or "SEE IMAGE" in lg or any(t in lg for t in toks)

    gov_liens = [r for r in rows if "LIEN" in r["doc"] and "RELEASE" not in r["doc"]
                 and GOV_RE.search(r["cross"]) and not NOT_LIENHOLDER.search(r["cross"]) and relevant(r)]
    gov_releases = [r for r in rows if ("RELEASE" in r["doc"] or "SATISFACTION" in r["doc"])
                    and GOV_RE.search(r["cross"]) and not NOT_LIENHOLDER.search(r["cross"])]
    state = [r for r in rows if ("LIEN" in r["doc"]) and STATE_RE.search(r["cross"])]
    mortgages = [r for r in rows if r["doc"] == "MORTGAGE" and relevant(r) and r["date"] >= "2000"]
    lis = [r for r in rows if "LIS PENDENS" in r["doc"] and "RELEASE" not in r["doc"] and relevant(r)]
    judgments = [r for r in rows if r["doc"].startswith("JUDGEMENT") and r["date"] >= "2005"
                 and not STATE_RE.search(r["cross"])]

    active_gov = max(0, len(gov_liens) - len(gov_releases))
    notes = []
    if gov_liens:
        yrs = sorted({r["date"][:4] for r in gov_liens})
        who = sorted({r["cross"] for r in gov_liens})
        notes.append(f"🔴 {len(gov_liens)} government lien(s) {yrs[0]}–{yrs[-1]} ({', '.join(who)[:80]}), "
                     f"{len(gov_releases)} release(s)")
    for r in state:
        notes.append(f"⚠️ {r['date'][:4]} State of Florida lien ({r['cross']}) — confirm with title")
    if mortgages:
        notes.append(f"{len(mortgages)} mortgage(s) on record (wiped by tax deed)")
    if lis:
        notes.append(f"{len(lis)} lis pendens (private)")
    if judgments:
        notes.append(f"{len(judgments)} personal judgment(s) against owner (wiped by tax deed)")
    if not notes:
        notes.append("✅ No liens or mortgages found")
    elif not gov_liens:
        notes.insert(0, "✅ No city/county liens found.")
    return {"active_gov_liens": active_gov, "gov_liens": len(gov_liens), "state_liens": len(state),
            "notes": " ".join(notes), "checked": True}
