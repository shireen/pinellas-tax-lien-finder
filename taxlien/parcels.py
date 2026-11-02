"""Parcel location (Pinellas County GIS), market value (Property Appraiser), and distance."""
import math
import re

from bs4 import BeautifulSoup

from .http import get

GIS = "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1/query"
PCPAO = "https://www.pcpao.gov/property-details?s="
GEOCODE = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"


def strap(parcel_id: str) -> str:
    """'26-31-16-72936-000-0260' -> '163126729360000260' (range+township+section+rest)."""
    p = parcel_id.split("-")
    return p[2] + p[1] + p[0] + "".join(p[3:])


def geocode(s, address: str) -> tuple[float, float]:
    try:
        r = get(s, GEOCODE, params={"SingleLine": address, "f": "json", "maxLocations": 1}).json()
        c = r["candidates"][0]["location"]
    except Exception:  # noqa: BLE001 - don't echo the address (Actions logs are public)
        raise SystemExit("Could not geocode HOME_ADDRESS.") from None
    return c["x"], c["y"]


def miles(a: tuple, b: tuple) -> float:
    r = 3958.8
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dla, dlo = la2 - la1, math.radians(b[0] - a[0])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def gis_parcels(s, parcel_ids: list[str]) -> dict:
    out = {}
    for i in range(0, len(parcel_ids), 50):
        chunk = parcel_ids[i:i + 50]
        where = "PARCELID_DSP1 IN (" + ",".join(f"'{p}'" for p in chunk) + ")"
        r = get(s, GIS, params={
            "where": where, "outFields": "PARCELID_DSP1,STRAP,SITE_ADDRESS,SITE_CITY,USE_CODE,Acres,OWNER1,OWNER2,LEGAL,SALEDATE1",
            "returnGeometry": "true", "outSR": "4326", "f": "json",
        }).json()
        for f in r.get("features", []):
            pts = [pt for ring in f["geometry"]["rings"] for pt in ring]
            c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            a = f["attributes"]
            out[a["PARCELID_DSP1"]] = {**a, "lon": c[0], "lat": c[1]}
    return out


def pcpao_details(s, strap_id: str) -> dict:
    html = get(s, PCPAO + strap_id).text
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    out = {}
    # The PCPAO page lays out the current-year value table as 5 header labels,
    # then the year, then 5 dollar amounts; the first $ after "Just/Market Value" is it.
    m = re.search(r"Just/Market Value\b.{0,500}?\$(\d[\d,]+)", text, re.DOTALL)
    if m:
        out["market_value"] = int(m.group(1).replace(",", ""))
    m = re.search(r"Property Use\n ?([^\n]+)", text)
    out["use"] = m.group(1).strip() if m else ""
    m = re.search(r"Year Built[^\d]{0,40}(\d{4})", text)
    out["year_built"] = m.group(1) if m else ""
    m = re.search(r"Living (?:SF|Area)[^\d]{0,40}([\d,]+)", text)
    out["living_sf"] = m.group(1) if m else ""
    m = re.search(r"Legal Description\n(?:More\n)?([^\n]+)", text)
    out["legal"] = m.group(1).strip() if m else ""
    return out
