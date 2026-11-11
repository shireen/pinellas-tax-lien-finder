"""Lien / mortgage screen using Pinellas Clerk Official Records (Acclaim), searched by owner name.

This is an automated first pass, not a title search. It looks for:
  * government liens (city / county / code enforcement) - these can SURVIVE a Florida tax deed
  * State of Florida / Dept of Revenue liens - flagged for a title attorney to confirm
  * mortgages, lis pendens and private judgments - generally wiped out by the tax deed
"""
from __future__ import annotations

import datetime as dt
import json
import re

import requests

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


class ClerkSearchError(RuntimeError):
    """A safe diagnostic: no owner, URL query, cookies or response body."""

    def __init__(self, stage: str, code: str, status_code: int | None = None):
        self.stage = stage
        self.code = code
        self.status_code = status_code
        super().__init__(f"{stage}: {code}" + (f" (HTTP {status_code})" if status_code else ""))


class Clerk:
    PAGE_SIZE = 500
    MAX_PAGES = 20

    def __init__(self):
        self.s = session()
        self.ready = False
        self.blocked = None

    def _request(self, method, path, stage, **kwargs):
        try:
            response = method(self.s, f"{BASE}{path}", **kwargs)
        except requests.RequestException as error:
            status = getattr(error.response, "status_code", None)
            code = "access_denied" if status in (401, 403) else "http_error" if status else "network_error"
            failure = ClerkSearchError(stage, code, status)
            # Do not hammer an access denial for every property or try to evade it.
            if code == "access_denied":
                self.blocked = failure
            raise failure from None
        return response

    def _accept(self):
        # Establish the public session before posting its non-account disclaimer.
        response = self._request(get, "/search/SearchTypeName", "search_form")
        soup = BeautifulSoup(response.text, "html.parser")
        if soup.select_one('input[name="disclaimer"][value="true"]'):
            response = self._request(
                post, "/search/Disclaimer?st=/search/SearchTypeName", "disclaimer",
                data={"disclaimer": "true"})
            soup = BeautifulSoup(response.text, "html.parser")
        if not soup.select_one('form#schfrm[action*="SearchTypeName"]'):
            raise ClerkSearchError("search_form", "unexpected_response")
        self.ready = True

    def search_name(self, name: str) -> list[dict]:
        if self.blocked:
            raise self.blocked
        try:
            return self._search_name(name)
        except Exception:
            # Failed or expired sessions must not leave an old owner's grid usable.
            self.ready = False
            self.s.close()
            self.s = session()
            raise

    def _search_name(self, name: str) -> list[dict]:
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
        html = self._request(post, "/search/SearchTypeName?Length=6", "name_search",
                             data=body, headers=XHR).text
        soup = BeautifulSoup(html, "html.parser")
        if soup.get_text(" ", strip=True) == "No names found. Please try your search again.":
            # The Clerk keeps the PREVIOUS grid in this case. Never request it.
            return []
        tree = soup.select_one("#NameListTreeView")
        if tree is None:
            raise ClerkSearchError("name_search", "unexpected_response")
        pairs, values = [], []
        for inp in tree.select("input"):
            n, v = inp.get("name"), inp.get("value", "")
            if not n:
                continue
            pairs.append((n, v))
            if n.endswith(".Value") and v and not re.search(r" \(\d+\)$", v):
                values.append(v)
        if not values:
            raise ClerkSearchError("name_selection", "unexpected_response")
        data = [("NameList", "|||".join(values))] + pairs + [
            ("PartyType", "Both"), ("RecordDateFrom", "1/2/1957 12:00:00 AM"),
            ("RecordDateTo", f"{to_date} 12:00:00 AM"), ("BookTypes", "All"), ("DocTypes", "all"),
            ("SearchOnName", name), ("SearchOnLastOrBusinessName", ""), ("SearchOnFirstName", ""),
            ("X-Requested-With", "XMLHttpRequest")]
        selected = self._request(post, "/Search/SearchTypePreName", "name_selection", data=data, headers=XHR)
        if not BeautifulSoup(selected.text, "html.parser").select_one("#RsltsGrid"):
            raise ClerkSearchError("name_selection", "unexpected_response")
        self._request(get, "/Search/PartialGrid", "partial_grid")
        rows, expected, seen_pages = [], None, set()
        for page in range(1, self.MAX_PAGES + 1):
            response = self._request(post, "/Search/GridResults", "grid_results",
                                     data={"page": page, "size": self.PAGE_SIZE}, headers=XHR)
            try:
                result = response.json()
            except ValueError:
                raise ClerkSearchError("grid_results", "unexpected_response") from None
            if (not isinstance(result, dict) or result.get("errors") or result.get("Errors")
                    or not isinstance(result.get("data"), list)
                    or type(result.get("total")) is not int or result["total"] < 0):
                raise ClerkSearchError("grid_results", "unexpected_response")
            if expected is None:
                expected = result["total"]
            if result["total"] != expected:
                raise ClerkSearchError("pagination", "incomplete_results")
            batch = result["data"]
            signature = json.dumps(batch, sort_keys=True)
            if (signature in seen_pages or len(rows) + len(batch) > expected
                    or (not batch and len(rows) < expected)):
                raise ClerkSearchError("pagination", "incomplete_results")
            seen_pages.add(signature)
            rows.extend(self._parse_record(x) for x in batch)
            if len(rows) == expected:
                return rows
        raise ClerkSearchError("pagination", "incomplete_results")

    @staticmethod
    def _parse_record(x):
        try:
            if not isinstance(x, dict) or not x.get("Name") or not x.get("DocTypeDescription"):
                raise ValueError
            # Acclaim returns .NET JSON dates, optionally with a timezone suffix.
            match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", x["RecordDate"])
            if not match:
                raise ValueError
            date = (dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=int(match[1]))).date().isoformat()
            for key in ("Name", "CrossPartyName", "DocTypeDescription", "Comments"):
                if x.get(key) is not None and not isinstance(x[key], str):
                    raise ValueError
            return {"date": date, "party": x.get("Party"), "name": x["Name"],
                    "cross": (x.get("CrossPartyName") or "").upper(),
                    "doc": x["DocTypeDescription"].upper(), "legal": x.get("Comments") or "",
                    "instrument": x.get("InstrumentNumber") or ""}
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ClerkSearchError("parse", "invalid_record") from None


def owner_search_name(owner1: str) -> str | None:
    """'SCHILLINGER, LISA' -> 'SCHILLINGER LISA'. Returns None for owners we can't search reliably."""
    owner1 = (owner1 or "").strip().upper()
    if not owner1 or re.search(r"TRUST\s*#|^TRUST\b", owner1):
        return None
    name = re.sub(r"\b(EST|ESTATE|TRE|TR|ET AL|JR|SR|III|II)\b\.?", "", owner1)
    name = re.sub(r"[,.]", " ", name)
    words = name.split()
    # last name + first name is precise enough; companies keep their full name
    return " ".join(words[:2] if "LLC" not in words and "INC" not in words else words) or None


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
        return (not toks) or (not lg.strip()) or "SEE IMAGE" in lg or any(t in lg for t in toks)

    gov_liens = [r for r in rows if "LIEN" in r["doc"] and "RELEASE" not in r["doc"]
                 and GOV_RE.search(r["cross"]) and not NOT_LIENHOLDER.search(r["cross"]) and relevant(r)]
    gov_releases = [r for r in rows if ("RELEASE" in r["doc"] or "SATISFACTION" in r["doc"])
                    and GOV_RE.search(r["cross"]) and not NOT_LIENHOLDER.search(r["cross"])]
    state = [r for r in rows if ("LIEN" in r["doc"]) and STATE_RE.search(r["cross"])]
    mortgages = [r for r in rows if r["doc"] == "MORTGAGE" and relevant(r) and r["date"] >= "2000"]
    lis = [r for r in rows if "LIS PENDENS" in r["doc"] and "RELEASE" not in r["doc"] and relevant(r)]
    judgments = [r for r in rows if r["doc"].startswith("JUDGEMENT") and r["date"] >= "2005"
                 and not STATE_RE.search(r["cross"])]

    active_gov = len(gov_liens)  # Unlinked releases cannot prove these liens were discharged.
    notes = []
    if gov_liens:
        yrs = sorted({r["date"][:4] for r in gov_liens})
        who = sorted({r["cross"] for r in gov_liens})
        notes.append(f"🔴 {len(gov_liens)} government lien(s) {yrs[0]}–{yrs[-1]} ({', '.join(who)[:80]}), "
                     f"{len(gov_releases)} unlinked release(s); title review required")
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
